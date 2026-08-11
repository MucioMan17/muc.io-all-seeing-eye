"""Motion-triggered clip recording.

Each camera feeds every frame into a ClipRecorder. A short pre-roll ring
buffer means the clip includes the seconds *before* motion started; the
clip keeps rolling until motion has been absent for post_seconds. A JPEG
snapshot is taken at trigger time for the event list thumbnails. Detected
objects are drawn as labeled boxes onto the saved clip and snapshot (the
live frame is left clean for face recognition and the MJPEG stream).

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

# Absolute free-space safety floor. The percentage ceiling (max_disk_percent)
# is meaningful on a small Pi SD card but wrong on a large disk — 90% of a 1 TB
# drive still leaves 100 GB, ample for motion clips. Keep recording as long as
# at least this much space is free, regardless of percentage, so a big mostly-
# full drive does not silently stop a security recorder.
MIN_FREE_GB = 10.0


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


BOX_COLOR = (0, 220, 0)  # BGR green


def boxes_from_tracks(tracks) -> List[Tuple[int, int, int, int, str, float]]:
    """Snapshot each track's geometry/label *now*, as plain tuples. The tracker
    mutates its Track objects in place across frames, so storing references
    would let a later frame move a box we've already buffered for an earlier
    one."""
    if not tracks:
        return []
    out = []
    for t in tracks:
        x, y, w, h = t.box
        out.append((int(x), int(y), int(w), int(h), t.label, float(t.conf)))
    return out


def draw_boxes(frame: np.ndarray, boxes) -> np.ndarray:
    """Return a copy of frame with a labeled box drawn for each detection.
    The original frame is left untouched (face recognition and the live MJPEG
    stream use the clean frame; only the recorded clip/snapshot get boxes)."""
    if not boxes:
        return frame
    out = frame.copy()
    w = out.shape[1]
    thick = max(2, round(w / 640))        # scale to the frame's resolution
    text_thick = max(1, thick // 2)
    font = cv2.FONT_HERSHEY_SIMPLEX
    fs = max(0.4, w / 1600.0)
    for x, y, bw, bh, label, conf in boxes:
        cv2.rectangle(out, (x, y), (x + bw, y + bh), BOX_COLOR, thick)
        text = f"{label} {conf:.0%}"
        (tw, th), base = cv2.getTextSize(text, font, fs, text_thick)
        y0 = y - th - base - 2
        if y0 < 0:                        # box hugs the top edge — label inside
            y0 = y + 2
        cv2.rectangle(out, (x, y0), (x + tw, y0 + th + base), BOX_COLOR, -1)
        cv2.putText(out, text, (x, y0 + th), font, fs, (0, 0, 0), text_thick, cv2.LINE_AA)
    return out


class ClipRecorder:
    def __init__(self, camera_id: str, cfg: RecordingConfig, fps: int, event_log: EventLog,
                 on_event=None):
        self.camera_id = camera_id
        self.cfg = cfg
        self.fps = max(1, fps)
        self.events = event_log
        # Optional callback fired when a recording starts: on_event(event_dict,
        # snapshot_abspath). Used to push detection alerts (e.g. Telegram). Kept
        # off the hot path — the callback must return quickly and never raise.
        self.on_event = on_event
        self.dir = os.path.join(cfg.dir, camera_id)
        os.makedirs(self.dir, exist_ok=True)

        self._prebuffer: Deque[Tuple[float, np.ndarray, list]] = deque()
        self._writer: Optional[cv2.VideoWriter] = None
        self._event: Optional[dict] = None
        self._last_active = 0.0
        self._last_disk_warn = 0.0

    def _disk_ok(self) -> bool:
        try:
            usage = shutil.disk_usage(self.cfg.dir)
        except OSError:
            return False
        pct_used = (usage.used / usage.total) * 100
        free_gb = usage.free / (1024 ** 3)
        # Record while under the percentage ceiling OR while a healthy absolute
        # amount is still free (the floor keeps a large, mostly-full drive
        # recording; on a tiny SD card the free space is gone before the floor
        # is met, so the percentage ceiling still protects it).
        if pct_used < self.cfg.max_disk_percent or free_gb >= MIN_FREE_GB:
            return True
        # Never fail silently: a security recorder that quietly stops is exactly
        # how a night went unrecorded. Warn at most once every 5 minutes.
        now = time.monotonic()
        if now - self._last_disk_warn > 300:
            self._last_disk_warn = now
            log.warning("camera %s: recording PAUSED - disk %.1f%% full, only "
                        "%.1f GB free (ceiling %d%%, floor %.0f GB)",
                        self.camera_id, pct_used, free_gb,
                        self.cfg.max_disk_percent, MIN_FREE_GB)
        return False

    def feed(self, frame: np.ndarray, ts: float, active: bool, tracks=None) -> None:
        if not self.cfg.enabled:
            return

        boxes = boxes_from_tracks(tracks)
        if self._writer is None:
            self._prebuffer.append((ts, frame, boxes))
            while self._prebuffer and ts - self._prebuffer[0][0] > self.cfg.pre_seconds:
                self._prebuffer.popleft()

            if active and self._disk_ok():
                self._start(frame, ts, boxes)
        else:
            self._writer.write(draw_boxes(frame, boxes))
            if active:
                self._last_active = ts
            elif ts - self._last_active > self.cfg.post_seconds:
                self._stop(ts)
                return
            if self._event and ts - self._event["start"] > self.cfg.max_seconds:
                # Motion is ongoing but the clip is long enough — close it out
                # (a follow-on clip starts on the next active frame).
                self._stop(ts)

    def _start(self, frame: np.ndarray, ts: float, boxes) -> None:
        eid = time.strftime("%Y%m%d-%H%M%S", time.localtime(ts)) + "-" + uuid.uuid4().hex[:6]
        video_rel = os.path.join(self.camera_id, eid + ".mp4")
        snap_rel = os.path.join(self.camera_id, eid + ".jpg")
        h, w = frame.shape[:2]
        writer = open_clip_writer(os.path.join(self.cfg.dir, video_rel), self.fps, w, h)
        if not writer.isOpened():
            log.error("camera %s: failed to open clip writer", self.camera_id)
            return
        snap_abs = os.path.join(self.cfg.dir, snap_rel)
        cv2.imwrite(snap_abs, draw_boxes(frame, boxes))
        for _, buffered, bboxes in self._prebuffer:
            writer.write(draw_boxes(buffered, bboxes))
        self._prebuffer.clear()
        self._writer = writer
        self._last_active = ts
        self._event = {
            "id": eid,
            "camera": self.camera_id,
            "start": ts,
            "video": video_rel,
            "snapshot": snap_rel,
            # What triggered the clip, for the event list and alert captions.
            "labels": sorted({b[4] for b in boxes}),
        }
        log.info("camera %s: recording event %s", self.camera_id, eid)
        if self.on_event is not None:
            try:
                self.on_event(dict(self._event), snap_abs)
            except Exception:
                log.exception("camera %s: on_event callback failed", self.camera_id)

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
