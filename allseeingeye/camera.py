"""Per-camera capture + analysis worker.

One thread per camera:
  read frame -> detect -> track -> feed recorder -> publish
    - latest JPEG (shared by all MJPEG stream clients)
    - latest detection payload (shared by all WebSocket clients)

Sources:
  int          -> local USB / V4L2 device index
  "rtsp://..." -> IP camera over WiFi/LAN (also http:// MJPEG urls)
  "synthetic"  -> generated test scene with moving objects (no hardware)

RTSP/USB dropouts are handled with automatic reconnect + backoff, since a
security system must survive a camera or WiFi hiccup unattended.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from typing import List, Optional

import cv2
import numpy as np

from .config import CameraConfig, RecordingConfig
from .detect import DnnDetector, MotionDetector, in_ignore_zone
from .discover import find_ip_for_mac, substitute_host
from .recorder import ClipRecorder, EventLog
from .tracker import CentroidTracker, Detection, Track

log = logging.getLogger(__name__)

JPEG_QUALITY = 80
RECONNECT_BACKOFF_MAX = 30.0


class SyntheticSource:
    """Test scene: dark yard with two objects wandering around. Lets the whole
    stack (detection, tracking, UI, recording) run with no camera attached."""

    def __init__(self, width: int, height: int, fps: int):
        self.w, self.h, self.fps = width, height, fps
        self.t0 = time.time()

    def isOpened(self) -> bool:
        return True

    def read(self):
        t = time.time() - self.t0
        frame = np.full((self.h, self.w, 3), 24, np.uint8)
        # Static scenery so the background model has texture to learn.
        cv2.rectangle(frame, (0, int(self.h * 0.8)), (self.w, self.h), (34, 40, 34), -1)
        for i in range(6):
            x = int((i + 0.5) * self.w / 6)
            cv2.line(frame, (x, int(self.h * 0.8)), (x, int(self.h * 0.55)), (50, 60, 50), 3)

        # Object 1: "person" pacing horizontally.
        px = int((0.5 + 0.4 * math.sin(t * 0.5)) * self.w)
        py = int(self.h * 0.62)
        cv2.rectangle(frame, (px - 18, py - 60), (px + 18, py + 60), (60, 120, 200), -1)
        cv2.circle(frame, (px, py - 78), 16, (60, 120, 200), -1)

        # Object 2: "car" crossing on a slower loop.
        cx = int(((t * 0.06) % 1.4 - 0.2) * self.w)
        cy = int(self.h * 0.83)
        cv2.rectangle(frame, (cx - 70, cy - 25), (cx + 70, cy + 25), (80, 80, 190), -1)
        cv2.circle(frame, (cx - 40, cy + 25), 12, (30, 30, 30), -1)
        cv2.circle(frame, (cx + 40, cy + 25), 12, (30, 30, 30), -1)

        noise = np.random.randint(0, 6, frame.shape, np.uint8)
        frame = cv2.add(frame, noise)
        return True, frame

    def release(self) -> None:
        pass


class CameraWorker(threading.Thread):
    def __init__(self, cfg: CameraConfig, rec_cfg: RecordingConfig,
                 event_log: EventLog, models_dir: str):
        super().__init__(name=f"camera-{cfg.id}", daemon=True)
        self.cfg = cfg
        self.tracker = CentroidTracker()
        self.recorder = ClipRecorder(cfg.id, rec_cfg, cfg.fps, event_log)
        self._stop = threading.Event()
        self._known_ip: Optional[str] = None

        self.detector = MotionDetector(min_area=cfg.detect.min_area, ignore=cfg.detect.ignore)
        self.dnn: Optional[DnnDetector] = None
        if cfg.detect.mode == "dnn":
            try:
                self.dnn = DnnDetector(models_dir, cfg.detect.confidence, cfg.detect.classes)
                log.info("camera %s: DNN object detection enabled", cfg.id)
            except FileNotFoundError as e:
                log.warning("camera %s: %s — falling back to motion detection", cfg.id, e)

        # Published state (guarded by _cond).
        self._cond = threading.Condition()
        self._jpeg: Optional[bytes] = None
        self._frame_seq = 0
        self._tracks_payload: dict = {"ts": 0, "objects": []}
        self.online = False
        self.last_frame_ts = 0.0

    # ---- capture ----

    def _open(self):
        src = self.cfg.source
        if src == "synthetic":
            return SyntheticSource(self.cfg.width, self.cfg.height, self.cfg.fps)
        if self.cfg.mac and isinstance(src, str) and "://" in src:
            # Camera pinned by MAC: find whatever IP it has right now.
            ip = find_ip_for_mac(self.cfg.mac)
            if ip:
                if ip != self._known_ip:
                    log.info("camera %s: MAC %s is at %s", self.cfg.id, self.cfg.mac, ip)
                self._known_ip = ip
            elif self._known_ip:
                log.warning("camera %s: MAC %s not found, trying last IP %s",
                            self.cfg.id, self.cfg.mac, self._known_ip)
            if self._known_ip:
                src = substitute_host(src, self._known_ip)
        cap = cv2.VideoCapture(src)
        if isinstance(src, int):
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.cfg.width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.cfg.height)
            cap.set(cv2.CAP_PROP_FPS, self.cfg.fps)
        # Keep RTSP latency down: don't buffer stale frames.
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 2)
        return cap

    def run(self) -> None:
        backoff = 1.0
        frame_interval = 1.0 / max(1, self.cfg.fps)
        frame_i = 0
        while not self._stop.is_set():
            cap = self._open()
            if not cap.isOpened():
                self.online = False
                log.warning("camera %s: cannot open source %r, retrying in %.0fs",
                            self.cfg.id, self.cfg.source, backoff)
                cap.release()
                self._stop.wait(backoff)
                backoff = min(backoff * 2, RECONNECT_BACKOFF_MAX)
                continue

            log.info("camera %s: source %r opened", self.cfg.id, self.cfg.source)
            backoff = 1.0
            self.online = True
            is_synthetic = isinstance(cap, SyntheticSource)

            while not self._stop.is_set():
                loop_start = time.time()
                ok, frame = cap.read()
                if not ok or frame is None:
                    log.warning("camera %s: read failed, reconnecting", self.cfg.id)
                    break
                ts = time.time()
                frame_i += 1
                tracks = self._process(frame, ts, frame_i)
                self.recorder.feed(frame, ts, active=len(tracks) > 0)
                self._publish(frame, ts, tracks)
                # Synthetic (and some USB) sources return frames instantly;
                # pace to the configured fps so we don't burn a full core.
                if is_synthetic or isinstance(self.cfg.source, int):
                    elapsed = time.time() - loop_start
                    if elapsed < frame_interval:
                        self._stop.wait(frame_interval - elapsed)

            self.online = False
            cap.release()
        self.recorder.close()

    # ---- analysis ----

    def _process(self, frame: np.ndarray, ts: float, frame_i: int) -> List[Track]:
        detections: List[Detection]
        if self.dnn is not None and frame_i % max(1, self.cfg.detect.dnn_interval) == 0:
            detections = self.dnn.detect(frame)
            # Motion mode masks zones at the pixel level; for DNN boxes,
            # drop any detection centered inside an ignore zone.
            zones = self.cfg.detect.ignore
            if zones:
                fh, fw = frame.shape[:2]
                detections = [
                    d for d in detections
                    if not in_ignore_zone((d.x + d.w / 2) / fw, (d.y + d.h / 2) / fh, zones)
                ]
        elif self.dnn is not None:
            # Between DNN passes, keep tracks alive with cheap motion boxes.
            detections = self.detector.detect(frame)
        else:
            detections = self.detector.detect(frame)
        return self.tracker.update(detections)

    def _publish(self, frame: np.ndarray, ts: float, tracks: List[Track]) -> None:
        ok, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
        payload = {
            "ts": ts,
            "camera": self.cfg.id,
            "frame_w": frame.shape[1],
            "frame_h": frame.shape[0],
            "objects": [
                {
                    "id": t.id,
                    "x": t.box[0], "y": t.box[1], "w": t.box[2], "h": t.box[3],
                    "label": t.label,
                    "conf": round(t.conf, 2),
                    "trail": list(t.trail)[-24:],
                }
                for t in tracks
            ],
        }
        with self._cond:
            if ok:
                self._jpeg = jpeg.tobytes()
                self._frame_seq += 1
            self._tracks_payload = payload
            self.last_frame_ts = ts
            self._cond.notify_all()

    # ---- consumers ----

    def wait_jpeg(self, last_seq: int, timeout: float = 5.0):
        """Block until a frame newer than last_seq exists; return (seq, jpeg)."""
        with self._cond:
            self._cond.wait_for(lambda: self._frame_seq != last_seq, timeout=timeout)
            return self._frame_seq, self._jpeg

    def snapshot_jpeg(self) -> Optional[bytes]:
        with self._cond:
            return self._jpeg

    def tracks_payload(self) -> dict:
        with self._cond:
            return self._tracks_payload

    def stop(self) -> None:
        self._stop.set()
