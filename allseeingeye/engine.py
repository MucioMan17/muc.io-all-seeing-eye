"""WatchEngine — the camera workers + recording that make up "watch mode".

Extracted from the ``--lite`` entry point so night-watch and the Telegram bot
can start and stop watching through one object. Face recognition and the web
server are intentionally out of scope here; this is the lightweight,
record-events-only engine (same footprint as ``--lite``).
"""

from __future__ import annotations

import logging
import os
import shutil
import threading
import time
from typing import Callable, Dict, Optional, Tuple

from . import settings
from .camera import CameraWorker
from .config import AppConfig
from .recorder import EventLog, start_retention_thread

log = logging.getLogger(__name__)


class WatchEngine:
    def __init__(self, cfg: AppConfig, on_event: Optional[Callable] = None):
        self.cfg = cfg
        # Keep recordings under the project's own run/ folder (never the Pi
        # default /var/lib, which on Windows lands in C:\var\lib away from the
        # app). Absolute, so it's independent of the launch directory.
        project = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        cfg.recording.dir = os.path.join(project, "run", "recordings")
        self.recordings_dir = cfg.recording.dir
        self.state_dir = os.path.dirname(os.path.abspath(cfg.recording.dir))

        self.events = EventLog(cfg.recording.dir)
        self._on_event = on_event
        self._workers: Dict[str, CameraWorker] = {}
        self._lock = threading.Lock()
        self._retention_started = False
        self._offline_thread: Optional[threading.Thread] = None
        self.started_at = 0.0

    def set_on_event(self, cb: Optional[Callable]) -> None:
        """Set the recording-event callback. Applied to workers created by the
        next start_watch(), so set it before starting."""
        self._on_event = cb

    def is_watching(self) -> bool:
        return bool(self._workers)

    def start_watch(self) -> bool:
        """Start every camera worker. Returns True if it started watching, or
        False if it was already watching (idempotent)."""
        with self._lock:
            if self._workers:
                return False
            if not self._retention_started:
                start_retention_thread(self.events, self.cfg.recording)
                self._retention_started = True
            for cam in self.cfg.cameras:
                worker = CameraWorker(cam, self.cfg.recording, self.events,
                                      self.cfg.models_dir, on_event=self._on_event)
                saved = settings.get(self.state_dir, cam.id, "sensitivity", None)
                if saved is not None:
                    worker.set_motion_sensitivity(saved)
                worker.start()
                self._workers[cam.id] = worker
            self.started_at = time.time()
            self._start_offline_watch()
            log.info("watch mode ON — %d camera(s) recording", len(self._workers))
            return True

    def stop_watch(self) -> bool:
        """Stop every camera worker. Returns True if it stopped, False if it
        wasn't watching (idempotent)."""
        with self._lock:
            if not self._workers:
                return False
            workers = list(self._workers.values())
            self._workers.clear()
        for w in workers:
            w.stop()
        for w in workers:
            w.join(timeout=5.0)
        log.info("watch mode OFF")
        return True

    def snapshot(self, cam_id: Optional[str] = None) -> Dict[str, bytes]:
        """Latest JPEG frame for one camera (by id) or all cameras currently up."""
        out: Dict[str, bytes] = {}
        with self._lock:
            workers = dict(self._workers)
        for cid, w in workers.items():
            if cam_id and cid != cam_id:
                continue
            jpg = w.snapshot_jpeg()
            if jpg:
                out[cid] = jpg
        return out

    def latest_clip(self, cam_id: Optional[str] = None) -> Tuple[Optional[str], Optional[dict]]:
        """Absolute path + event of the most recent recorded clip on disk."""
        for ev in self.events.list(limit=50, camera=cam_id):
            rel = ev.get("video")
            if rel:
                path = os.path.join(self.recordings_dir, rel)
                if os.path.exists(path):
                    return path, ev
        return None, None

    def status(self) -> dict:
        with self._lock:
            cams = {cid: {"name": w.cfg.name, "online": bool(w.online)}
                    for cid, w in self._workers.items()}
        try:
            u = shutil.disk_usage(self.recordings_dir)
            disk = {"free_gb": round(u.free / 1024 ** 3, 1),
                    "used_pct": round(u.used / u.total * 100, 1)}
        except OSError:
            disk = {}
        return {
            "watching": self.is_watching(),
            "cameras": cams,
            "disk": disk,
            "uptime_s": (time.time() - self.started_at) if self.started_at else 0.0,
        }

    def _start_offline_watch(self) -> None:
        """Log when a camera drops offline or comes back, so a silent failure
        is visible (a silent failure is exactly why a night went unrecorded)."""
        if self._offline_thread and self._offline_thread.is_alive():
            return

        def loop() -> None:
            seen: Dict[str, bool] = {}
            while self.is_watching():
                for cid, w in list(self._workers.items()):
                    on = bool(w.online)
                    if cid not in seen:
                        seen[cid] = on
                    elif on != seen[cid]:
                        seen[cid] = on
                        log.info("camera %s is %s", cid, "BACK ONLINE" if on else "OFFLINE")
                time.sleep(2.0)

        self._offline_thread = threading.Thread(target=loop, name="offline-watch", daemon=True)
        self._offline_thread.start()
