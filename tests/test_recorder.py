import json
import time

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from allseeingeye.config import RecordingConfig
from allseeingeye.recorder import ClipRecorder, EventLog


def frame():
    return np.zeros((120, 160, 3), np.uint8)


def make(tmp_path, **kw):
    cfg = RecordingConfig(dir=str(tmp_path), pre_seconds=1.0, post_seconds=1.0,
                          max_disk_percent=100, **kw)
    log = EventLog(cfg.dir)
    return ClipRecorder("cam1", cfg, fps=10, event_log=log), log


def test_motion_event_produces_clip_snapshot_and_index(tmp_path):
    rec, log = make(tmp_path)
    t = time.time()
    # Quiet pre-roll, then motion, then quiet past post_seconds.
    for i in range(5):
        rec.feed(frame(), t + i * 0.1, active=False)
    for i in range(5, 10):
        rec.feed(frame(), t + i * 0.1, active=True)
    rec.feed(frame(), t + 3.0, active=False)

    events = log.list()
    assert len(events) == 1
    ev = events[0]
    assert ev["camera"] == "cam1"
    assert (tmp_path / ev["video"]).exists()
    assert (tmp_path / ev["snapshot"]).exists()
    assert ev["end"] > ev["start"]


def test_no_event_without_motion(tmp_path):
    rec, log = make(tmp_path)
    t = time.time()
    for i in range(30):
        rec.feed(frame(), t + i * 0.1, active=False)
    assert log.list() == []


def test_long_event_splits_at_max_seconds(tmp_path):
    rec, log = make(tmp_path, max_seconds=2.0)
    t = time.time()
    # 6 seconds of continuous motion at 10fps.
    for i in range(60):
        rec.feed(frame(), t + i * 0.1, active=True)
    rec.close()
    events = log.list()
    assert len(events) >= 2


def test_retention_prunes_old_events(tmp_path):
    rec, log = make(tmp_path)
    old = {"id": "old", "camera": "cam1", "start": time.time() - 40 * 86400,
           "video": "cam1/old.mp4", "snapshot": "cam1/old.jpg"}
    (tmp_path / "cam1").mkdir(exist_ok=True)
    (tmp_path / "cam1" / "old.mp4").write_bytes(b"x")
    (tmp_path / "cam1" / "old.jpg").write_bytes(b"x")
    log.append(old)
    log.append({"id": "new", "camera": "cam1", "start": time.time(),
                "video": "cam1/new.mp4", "snapshot": "cam1/new.jpg"})

    log.prune(retention_days=14)
    ids = [e["id"] for e in log.list()]
    assert ids == ["new"]
    assert not (tmp_path / "cam1" / "old.mp4").exists()
