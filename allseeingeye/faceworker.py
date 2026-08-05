"""Face recognition worker + manager.

FaceRecognizer  — shared: holds the identity store, the sighting log, the
                  match/enroll/cooldown logic, and a rolling list of unknown-
                  face alerts.
FaceWorker      — one thread per camera: periodically grabs the camera's latest
                  frame, runs recognition, and publishes the current faces for
                  the UI to overlay.

Runs off the capture thread, so the live video is never blocked by ArcFace.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Dict, List, Optional

from . import recognizer
from .config import FaceConfig
from .sightings import SightingLog

log = logging.getLogger(__name__)


class FaceRecognizer:
    def __init__(self, faces_dir: str, cfg: FaceConfig):
        self.cfg = cfg
        self.store = recognizer.IdentityStore(faces_dir)
        self.log = SightingLog(faces_dir)
        self._lock = threading.Lock()           # serialize store access
        self._last_logged: Dict[str, float] = {}
        self._alerts: deque = deque(maxlen=100)  # recent unknown-face alerts

    def _should_log(self, iid: str, now: float) -> bool:
        last = self._last_logged.get(iid, 0.0)
        if now - last >= self.cfg.cooldown:
            self._last_logged[iid] = now
            return True
        return False

    def process_frame(self, frame_bgr, camera_id: str) -> List[dict]:
        """Detect + identify faces in one frame. Returns per-face results for
        the UI: [{bbox:[x,y,w,h], name, identity_id, status, score}]."""
        results: List[dict] = []
        with self._lock:
            for face in recognizer.detect(frame_bgr):
                if not recognizer.is_good_crop(frame_bgr, face):
                    continue  # too small / too blurry to trust — skip
                emb = face.normed_embedding
                idx, score = self.store.match(emb)

                now = time.time()
                if idx is not None and score >= self.cfg.threshold:
                    identity = self.store.identities[idx]
                    status = "known"
                elif self.cfg.auto_enroll:
                    crop = recognizer.crop_face(frame_bgr, face)
                    identity = self.store.add(emb, crop)   # new "Unknown-N"
                    status = "unknown"
                    self._alerts.append({
                        "ts": now, "identity_id": identity["id"],
                        "name": identity["name"], "camera": camera_id,
                        "thumb": identity["thumb"], "score": round(score, 3),
                    })
                    log.info("camera %s: UNKNOWN face -> enrolled %s",
                             camera_id, identity["name"])
                else:
                    identity, status = None, "unknown"

                iid = identity["id"] if identity else None
                name = identity["name"] if identity else "Unknown"
                if iid and self._should_log(iid, now):
                    self.log.add(iid, name, camera_id, score, status,
                                 snapshot=identity.get("thumb"))

                x1, y1, x2, y2 = [int(v) for v in face.bbox]
                results.append({
                    "bbox": [x1, y1, x2 - x1, y2 - y1],
                    "name": name, "identity_id": iid,
                    "status": status, "score": round(float(score), 2),
                })
        return results

    # ---- read APIs (used by the HTTP layer) ----
    def alerts(self, limit: int = 20) -> List[dict]:
        return list(self._alerts)[-limit:][::-1]

    def faces_with_counts(self, day: Optional[str] = None) -> List[dict]:
        summary = {s["identity_id"]: s for s in self.log.summary(day)}
        out = []
        for ident in self.store.all():
            s = summary.get(ident["id"], {})
            out.append({
                **ident,
                "count_today": s.get("count", 0),
                "first": s.get("first"), "last": s.get("last"),
            })
        return out


class FaceWorker(threading.Thread):
    def __init__(self, camera_worker, manager: FaceRecognizer, cfg: FaceConfig):
        super().__init__(name=f"face-{camera_worker.cfg.id}", daemon=True)
        self.cam = camera_worker
        self.manager = manager
        self.cfg = cfg
        self._stop = threading.Event()
        self._cond = threading.Condition()
        self._faces_payload: dict = {"ts": 0, "camera": camera_worker.cfg.id, "faces": []}

    def run(self) -> None:
        while not self._stop.is_set():
            frame = self.cam.latest_raw()
            if frame is not None:
                try:
                    faces = self.manager.process_frame(frame, self.cam.cfg.id)
                except Exception as e:  # never let a bad frame kill the thread
                    log.warning("camera %s: face pass failed: %s", self.cam.cfg.id, e)
                    faces = []
                with self._cond:
                    self._faces_payload = {
                        "ts": time.time(), "camera": self.cam.cfg.id, "faces": faces,
                    }
            self._stop.wait(self.cfg.interval)

    def faces_payload(self) -> dict:
        with self._cond:
            return self._faces_payload

    def stop(self) -> None:
        self._stop.set()
