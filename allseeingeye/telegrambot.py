"""Telegram bot: remote control + detection alerts for All-Seeing Eye.

Uses the Telegram Bot API over outbound HTTPS long-polling only — no inbound
ports, no public URL, works from behind the home NAT. Only the configured
chat_id may control the system; every other chat is ignored.

Two background threads:
  * poll   — long-polls getUpdates and dispatches /commands
  * alert  — drains a queue of detection snapshots and uploads them, so the
             network never blocks a camera capture thread.
"""

from __future__ import annotations

import logging
import os
import queue
import threading
import time
from typing import Optional

import requests

log = logging.getLogger(__name__)

_API = "https://api.telegram.org/bot{token}/{method}"


class TelegramClient:
    """Thin wrapper over the Telegram Bot API. The token is never logged."""

    def __init__(self, token: str):
        self._token = token
        self._s = requests.Session()

    def _url(self, method: str) -> str:
        return _API.format(token=self._token, method=method)

    def get_updates(self, offset: int, timeout: int = 30):
        try:
            r = self._s.get(self._url("getUpdates"),
                            params={"offset": offset, "timeout": timeout},
                            timeout=timeout + 10)
            r.raise_for_status()
            return r.json().get("result", [])
        except (requests.RequestException, ValueError) as e:
            log.warning("telegram getUpdates failed: %s", e)
            time.sleep(3)  # throttle retries when Telegram is unreachable
            return []

    def send_message(self, chat_id: str, text: str) -> bool:
        return self._post("sendMessage", data={"chat_id": chat_id, "text": text})

    def send_photo(self, chat_id: str, photo, caption: Optional[str] = None,
                   filename: str = "snapshot.jpg") -> bool:
        if isinstance(photo, (bytes, bytearray)):
            data_bytes = bytes(photo)
        else:  # a filesystem path
            filename = os.path.basename(photo)
            with open(photo, "rb") as f:
                data_bytes = f.read()
        data = {"chat_id": chat_id}
        if caption:
            data["caption"] = caption
        return self._post("sendPhoto", data=data,
                          files={"photo": (filename, data_bytes, "image/jpeg")})

    def send_video(self, chat_id: str, path: str, caption: Optional[str] = None) -> bool:
        with open(path, "rb") as f:
            data = {"chat_id": chat_id}
            if caption:
                data["caption"] = caption
            return self._post("sendVideo", data=data,
                              files={"video": (os.path.basename(path), f.read(), "video/mp4")})

    def _post(self, method: str, data=None, files=None) -> bool:
        try:
            r = self._s.post(self._url(method), data=data, files=files, timeout=120)
            r.raise_for_status()
            return True
        except (requests.RequestException, ValueError) as e:
            log.warning("telegram %s failed: %s", method, e)
            return False


def _fmt_time(epoch: Optional[float]) -> str:
    if not epoch:
        return ""
    return time.strftime("%I:%M %p", time.localtime(epoch)).lstrip("0")


class TelegramBot:
    def __init__(self, cfg, engine, client: Optional[TelegramClient] = None):
        self.cfg = cfg
        self.tg = cfg.telegram
        self.engine = engine
        self.client = client or TelegramClient(self.tg.token)
        self.chat_id = str(self.tg.chat_id or "")
        self._names = {c.id: c.name for c in cfg.cameras}

        self._alert_q: "queue.Queue" = queue.Queue(maxsize=100)
        self._last_alert = {}          # cam_id -> monotonic ts of last alert
        self._muted_until = 0.0
        self._alerts_sent = 0
        self._offset = 0
        self._stop = threading.Event()

    # ---- lifecycle ----

    def start(self) -> None:
        threading.Thread(target=self._poll_loop, name="tg-poll", daemon=True).start()
        threading.Thread(target=self._alert_loop, name="tg-alert", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def notify(self, text: str) -> None:
        if self.chat_id:
            self.client.send_message(self.chat_id, text)

    # ---- detection alerts (called from a camera thread; must be quick) ----

    def on_event(self, event: dict, snapshot_path: str) -> None:
        if not self.tg.alerts:
            return
        now = time.monotonic()
        if now < self._muted_until:
            return
        cam = event.get("camera", "")
        if now - self._last_alert.get(cam, 0.0) < self.tg.alert_cooldown:
            return
        self._last_alert[cam] = now
        try:
            self._alert_q.put_nowait((cam, snapshot_path, event.get("labels") or [],
                                      event.get("start")))
        except queue.Full:
            log.warning("telegram alert queue full — dropping snapshot")

    def _alert_loop(self) -> None:
        while not self._stop.is_set():
            try:
                item = self._alert_q.get(timeout=1.0)
            except queue.Empty:
                continue
            self._deliver_alert(item)

    def _deliver_alert(self, item) -> None:
        cam, path, labels, start = item
        name = self._names.get(cam, cam)
        what = ", ".join(labels) if labels else "motion"
        caption = " · ".join(p for p in (name, what, _fmt_time(start)) if p)
        if self.client.send_photo(self.chat_id, path, caption=caption):
            self._alerts_sent += 1

    # ---- polling + command dispatch ----

    def _poll_loop(self) -> None:
        # Skip any backlog so we don't replay commands sent before we launched.
        backlog = self.client.get_updates(0, timeout=0)
        if backlog:
            self._offset = backlog[-1]["update_id"] + 1
        while not self._stop.is_set():
            for u in self.client.get_updates(self._offset, timeout=30):
                self._offset = u["update_id"] + 1
                self.handle_update(u)

    def handle_update(self, update: dict) -> None:
        msg = update.get("message") or update.get("channel_post")
        if not msg:
            return
        chat = str(msg.get("chat", {}).get("id", ""))
        text = (msg.get("text") or "").strip()
        if not text:
            return
        # Allowlist: only the configured chat may control the system. If no
        # chat_id is configured yet, reply with the sender's id so it can be
        # filled in — this grants no control.
        if not self.chat_id:
            self.client.send_message(
                chat, f"Your chat_id is {chat}. Add it to config/local.yml "
                      "under telegram.chat_id and restart the bot.")
            return
        if chat != self.chat_id:
            log.warning("ignoring telegram command from unauthorized chat %s", chat)
            return

        cmd, *rest = text.split()
        cmd = cmd.lower().lstrip("/").split("@")[0]  # strip leading / and @botname
        arg = rest[0] if rest else None
        handler = getattr(self, f"_cmd_{cmd}", None)
        if handler is None:
            self._cmd_help(None)
            return
        try:
            handler(arg)
        except Exception:
            log.exception("telegram command /%s failed", cmd)
            self.notify(f"⚠️ /{cmd} failed")

    # ---- commands ----

    def _cmd_start(self, arg):
        self._cmd_help(arg)

    def _cmd_help(self, arg):
        self.notify(
            "All-Seeing Eye\n"
            "/status – system status\n"
            "/watch_on – start watching\n"
            "/watch_off – stop watching\n"
            "/photo [cam] – live snapshot\n"
            "/cameras – list cameras\n"
            "/clip [cam] – last recorded clip\n"
            "/clear – delete ALL recorded footage\n"
            "/mute [min] – pause alerts\n"
            "/unmute – resume alerts")

    def _cmd_watch_on(self, arg):
        self.notify("\U0001f440 Watch mode ON." if self.engine.start_watch()
                    else "Already watching.")

    def _cmd_watch_off(self, arg):
        self.notify("Watch mode OFF." if self.engine.stop_watch()
                    else "Not currently watching.")

    def _cmd_status(self, arg):
        s = self.engine.status()
        lines = ["\U0001f440 Watching" if s["watching"] else "\U0001f4a4 Standby"]
        for c in s["cameras"].values():
            lines.append(f"  {c['name']}: {'online' if c['online'] else 'OFFLINE'}")
        d = s.get("disk") or {}
        if d:
            lines.append(f"Disk: {d['free_gb']} GB free ({d['used_pct']}% used)")
        if self._muted_until > time.monotonic():
            lines.append("Alerts: muted")
        lines.append(f"Alerts sent: {self._alerts_sent}")
        self.notify("\n".join(lines))

    def _cmd_cameras(self, arg):
        s = self.engine.status()
        if not s["cameras"]:
            self.notify("No cameras up (watch is off). Use /watch_on.")
            return
        self.notify("\n".join(
            f"{c['name']} ({cid}): {'online' if c['online'] else 'OFFLINE'}"
            for cid, c in s["cameras"].items()))

    def _cmd_photo(self, arg):
        shots = self.engine.snapshot(arg)
        if not shots:
            self.notify("No live frame (watch off or camera offline).")
            return
        for cid, jpg in shots.items():
            self.client.send_photo(self.chat_id, jpg, caption=self._names.get(cid, cid))

    def _cmd_clip(self, arg):
        path, ev = self.engine.latest_clip(arg)
        if not path:
            self.notify("No clips recorded yet.")
            return
        self.notify("Uploading last clip…")
        self.client.send_video(self.chat_id, path,
                               caption=self._names.get(ev.get("camera"), ev.get("camera")))

    def _cmd_clear(self, arg):
        n = self.engine.clear_footage()
        self.notify(f"\U0001f5d1 Cleared all footage — {n} clip{'' if n == 1 else 's'} removed.")

    def _cmd_mute(self, arg):
        mins = 60
        if arg:
            try:
                mins = max(1, int(arg))
            except ValueError:
                pass
        self._muted_until = time.monotonic() + mins * 60
        self.notify(f"\U0001f515 Alerts muted for {mins} min.")

    def _cmd_unmute(self, arg):
        self._muted_until = 0.0
        self.notify("\U0001f514 Alerts on.")
