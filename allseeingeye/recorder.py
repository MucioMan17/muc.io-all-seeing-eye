"""Motion-triggered clip recording.

Each camera feeds every frame into a ClipRecorder. A short pre-roll ring
buffer means the clip includes the seconds *before* motion started; the
clip keeps rolling until motion has been absent for post_seconds. A JPEG
snapshot is taken at trigger time for the event list thumbnails.

Events are appended to events.jsonl in the recordings dir; a background
sweep enforces the retention window and a disk-usage ceiling.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import threading
import time
import uuid
from collections import deque
from typing import Deque, List, Optional, Tuple

import cv2
import numpy as np

from .config import RecordingConfig

log = logging.getLogger(__name__)

HAS_FFMPEG = shutil.which("ffmpeg") is not None


class FfmpegClipWriter:
    """H.264 clip writer: pipes raw BGR frames into ffmpeg/libx264.

    Browsers can't decode the MPEG-4 Part 2 video OpenCV's VideoWriter
    produces, so clips must be H.264 to play in the console UI.
    +faststart moves the index to the front of the file so playback can
    begin before the whole clip has downloaded.
    """

    def __init__(self, path: str, fps: int, width: int, height: int):
        self.proc = subprocess.Popen(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "rawvideo", "-pix_fmt", "bgr24",
                "-s", f"{width}x{height}", "-r", str(fps), "-i", "-",
                "-c:v", "libx264", "-preset", "superfast", "-crf", "23",
                "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                path,
            ],
            stdin=subprocess.PIPE,
        )

    def isOpened(self) -> bool:
        return self.proc.poll() is None

    def write(self, frame: np.ndarray) -> None:
        try:
            self.proc.stdin.write(frame.tobytes())
        except (BrokenPipeError, ValueError, OSError):
            pass  # ffmpeg died; release() will log via returncode

    def release(self) -> None:
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            self.proc.kill()
        if self.proc.returncode not in (0, None):
            log.error("ffmpeg clip writer exited with code %s", self.proc.returncode)


def open_clip_writer(path: str, fps: int, width: int, height: int):
    if HAS_FFMPEG:
        return FfmpegClipWriter(path, fps, width, height)
    log.warning(
        "ffmpeg not found — falling back to OpenCV mp4v clips, which "
        "browsers cannot play. Install ffmpeg for in-UI playback."
    )
    return cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height))


class EventLog:
    """Append-only JSONL event index, shared by all cameras."""

    def __init__(self, root: str):
        self.root = root
        self.path = os.path.join(root, "events.jsonl")
        self._lock = threading.Lock()
        os.makedirs(root, exist_ok=True)

    def append(self, event: dict) -> None:
        with self._lock:
            with open(self.path, "a") as f:
                f.write(json.dumps(event) + "\n")

    def list(self, limit: int = 50, camera: Optional[str] = None) -> List[dict]:
        try:
            with self._lock, open(self.path) as f:
                lines = f.readlines()
        except FileNotFoundError:
            return []
        events = []
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if camera and ev.get("camera") != camera:
                continue
            events.append(ev)
            if len(events) >= limit:
                break
        return events

    def _delete_files(self, ev: dict) -> None:
        for key in ("video", "snapshot"):
            rel = ev.get(key)
            if rel:
                try:
                    os.remove(os.path.join(self.root, rel))
                except OSError:
                    pass

    def _rewrite(self, lines: List[str]) -> None:
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            f.writelines(lines)
        os.replace(tmp, self.path)

    def remove(self, event_id: str) -> bool:
        """Delete one event: its index entry, clip, and snapshot."""
        with self._lock:
            try:
                with open(self.path) as f:
                    lines = f.readlines()
            except FileNotFoundError:
                return False
            keep, removed = [], None
            for line in lines:
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if ev.get("id") == event_id:
                    removed = ev
                else:
                    keep.append(line)
            if removed is None:
                return False
            self._delete_files(removed)
            self._rewrite(keep)
            return True

    def clear(self) -> int:
        """Delete every indexed event and its files; returns the count."""
        with self._lock:
            try:
                with open(self.path) as f:
                    lines = f.readlines()
            except FileNotFoundError:
                return 0
            n = 0
            for line in lines:
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                self._delete_files(ev)
                n += 1
            self._rewrite([])
            return n

    def prune(self, retention_days: int) -> None:
        """Drop index entries (and their files) older than the window."""
        cutoff = time.time() - retention_days * 86400
        with self._lock:
            try:
                with open(self.path) as f:
                    lines = f.readlines()
            except FileNotFoundError:
                return
            keep = []
            for line in lines:
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if ev.get("start", 0) >= cutoff:
                    keep.append(line)
                else:
                    self._delete_files(ev)
            if len(keep) != len(lines):
                self._rewrite(keep)


class ClipRecorder:
    def __init__(self, camera_id: str, cfg: RecordingConfig, fps: int, event_log: EventLog):
        self.camera_id = camera_id
        self.cfg = cfg
        self.fps = max(1, fps)
        self.events = event_log
        self.dir = os.path.join(cfg.dir, camera_id)
        os.makedirs(self.dir, exist_ok=True)

        self._prebuffer: Deque[Tuple[float, np.ndarray]] = deque()
        self._writer: Optional[cv2.VideoWriter] = None
        self._event: Optional[dict] = None
        self._last_active = 0.0

    def _disk_ok(self) -> bool:
        try:
            usage = shutil.disk_usage(self.cfg.dir)
            return (usage.used / usage.total) * 100 < self.cfg.max_disk_percent
        except OSError:
            return False

    def feed(self, frame: np.ndarray, ts: float, active: bool) -> None:
        if not self.cfg.enabled:
            return

        if self._writer is None:
            self._prebuffer.append((ts, frame))
            while self._prebuffer and ts - self._prebuffer[0][0] > self.cfg.pre_seconds:
                self._prebuffer.popleft()

            if active and self._disk_ok():
                self._start(frame, ts)
        else:
            self._writer.write(frame)
            if active:
                self._last_active = ts
            elif ts - self._last_active > self.cfg.post_seconds:
                self._stop(ts)
                return
            if self._event and ts - self._event["start"] > self.cfg.max_seconds:
                # Motion is ongoing but the clip is long enough — close it out
                # (a follow-on clip starts on the next active frame).
                self._stop(ts)

    def _start(self, frame: np.ndarray, ts: float) -> None:
        eid = time.strftime("%Y%m%d-%H%M%S", time.localtime(ts)) + "-" + uuid.uuid4().hex[:6]
        video_rel = os.path.join(self.camera_id, eid + ".mp4")
        snap_rel = os.path.join(self.camera_id, eid + ".jpg")
        h, w = frame.shape[:2]
        writer = open_clip_writer(os.path.join(self.cfg.dir, video_rel), self.fps, w, h)
        if not writer.isOpened():
            log.error("camera %s: failed to open clip writer", self.camera_id)
            return
        cv2.imwrite(os.path.join(self.cfg.dir, snap_rel), frame)
        for _, buffered in self._prebuffer:
            writer.write(buffered)
        self._prebuffer.clear()
        self._writer = writer
        self._last_active = ts
        self._event = {
            "id": eid,
            "camera": self.camera_id,
            "start": ts,
            "video": video_rel,
            "snapshot": snap_rel,
        }
        log.info("camera %s: recording event %s", self.camera_id, eid)

    def _stop(self, ts: float) -> None:
        if self._writer is not None:
            self._writer.release()
            self._writer = None
        if self._event is not None:
            self._event["end"] = ts
            self.events.append(self._event)
            self._event = None

    def close(self) -> None:
        self._stop(time.time())


def start_retention_thread(events: EventLog, cfg: RecordingConfig) -> threading.Thread:
    def loop() -> None:
        while True:
            try:
                events.prune(cfg.retention_days)
            except Exception:
                log.exception("retention sweep failed")
            time.sleep(3600)

    t = threading.Thread(target=loop, name="retention", daemon=True)
    t.start()
    return t
