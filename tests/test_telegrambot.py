"""TelegramBot command dispatch, chat_id allowlist, and alert cooldown —
all exercised with a fake client + fake engine, so no network or token."""

from allseeingeye.config import AppConfig, CameraConfig, TelegramConfig
from allseeingeye.telegrambot import TelegramBot


class FakeClient:
    def __init__(self):
        self.messages = []   # (chat_id, text)
        self.photos = []     # (chat_id, photo, caption)
        self.videos = []     # (chat_id, path, caption)

    def send_message(self, chat_id, text):
        self.messages.append((str(chat_id), text)); return True

    def send_photo(self, chat_id, photo, caption=None, filename="s.jpg"):
        self.photos.append((str(chat_id), photo, caption)); return True

    def send_video(self, chat_id, path, caption=None):
        self.videos.append((str(chat_id), path, caption)); return True

    def get_updates(self, offset, timeout=30):
        return []


class FakeEngine:
    def __init__(self):
        self.watching = False
        self.started = self.stopped = 0

    def start_watch(self):
        if self.watching:
            return False
        self.watching = True; self.started += 1; return True

    def stop_watch(self):
        if not self.watching:
            return False
        self.watching = False; self.stopped += 1; return True

    def is_watching(self):
        return self.watching

    def status(self):
        return {"watching": self.watching,
                "cameras": {"cam1": {"name": "Driveway", "online": True}},
                "disk": {"free_gb": 61.5, "used_pct": 93.4}}

    def snapshot(self, cam_id=None):
        return {"cam1": b"JPEGDATA"}

    def latest_clip(self, cam_id=None):
        return ("/tmp/clip.mp4", {"camera": "cam1"})

    def clear_footage(self):
        self.cleared = getattr(self, "cleared", 0) + 1
        return 7


def make_bot(chat_id="42", cooldown=30):
    cfg = AppConfig(
        telegram=TelegramConfig(token="tok", chat_id=chat_id, alert_cooldown=cooldown),
        cameras=[CameraConfig(id="cam1", name="Driveway"),
                 CameraConfig(id="cam2", name="Room")],
    )
    client = FakeClient()
    return TelegramBot(cfg, FakeEngine(), client=client), client


def msg(text, chat="42"):
    return {"update_id": 1, "message": {"chat": {"id": int(chat)}, "text": text}}


def test_watch_on_starts_engine_and_replies():
    bot, client = make_bot()
    bot.handle_update(msg("/watch_on"))
    assert bot.engine.started == 1 and bot.engine.watching
    assert client.messages and "ON" in client.messages[-1][1]


def test_unauthorized_chat_is_ignored():
    bot, client = make_bot(chat_id="42")
    bot.handle_update(msg("/watch_on", chat="999"))
    assert bot.engine.started == 0            # command not executed
    assert client.messages == []              # and no reply leaked to them


def test_unconfigured_chat_id_replies_with_id():
    bot, client = make_bot(chat_id="")        # not set up yet
    bot.handle_update(msg("/status", chat="777"))
    assert client.messages and "777" in client.messages[-1][1]
    assert bot.engine.started == 0            # no control granted


def test_command_at_botname_suffix_is_handled():
    bot, _ = make_bot()
    bot.handle_update(msg("/watch_on@my_bot"))
    assert bot.engine.watching


def test_alert_cooldown_dedupes_same_camera():
    bot, _ = make_bot(cooldown=30)
    ev = {"camera": "cam1", "labels": ["person"], "start": 123.0}
    bot.on_event(ev, "a.jpg")
    bot.on_event(ev, "b.jpg")                 # within cooldown -> dropped
    assert bot._alert_q.qsize() == 1


def test_alert_cooldown_is_per_camera():
    bot, _ = make_bot(cooldown=30)
    bot.on_event({"camera": "cam1", "labels": ["person"], "start": 1.0}, "a.jpg")
    bot.on_event({"camera": "cam2", "labels": ["person"], "start": 1.0}, "b.jpg")
    assert bot._alert_q.qsize() == 2          # different cameras both alert


def test_alerts_muted_suppresses_events():
    bot, _ = make_bot(cooldown=0)
    bot._cmd_mute("15")
    bot.on_event({"camera": "cam1", "labels": ["person"], "start": 1.0}, "a.jpg")
    assert bot._alert_q.qsize() == 0


def test_deliver_alert_builds_caption_and_sends_photo():
    bot, client = make_bot()
    bot._deliver_alert(("cam1", "snap.jpg", ["person"], 1_000_000.0))
    assert client.photos and client.photos[-1][0] == "42"
    caption = client.photos[-1][2]
    assert "Driveway" in caption and "person" in caption
    assert bot._alerts_sent == 1


def test_photo_command_sends_live_snapshot():
    bot, client = make_bot()
    bot.handle_update(msg("/photo"))
    assert client.photos and client.photos[-1][1] == b"JPEGDATA"


def test_status_reports_state():
    bot, client = make_bot()
    bot.handle_update(msg("/status"))
    assert client.messages and "Driveway" in client.messages[-1][1]


def test_clear_deletes_footage_and_reports_count():
    bot, client = make_bot()
    bot.handle_update(msg("/clear"))
    assert bot.engine.cleared == 1
    assert client.messages and "7 clips" in client.messages[-1][1]


def test_clear_only_from_authorized_chat():
    bot, _ = make_bot(chat_id="42")
    bot.handle_update(msg("/clear", chat="999"))
    assert getattr(bot.engine, "cleared", 0) == 0   # destructive cmd blocked
