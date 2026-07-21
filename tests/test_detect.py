import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from allseeingeye.detect import MotionDetector, _merge_boxes


def test_merge_overlapping_boxes():
    merged = _merge_boxes([[0, 0, 50, 50], [40, 40, 50, 50], [300, 300, 20, 20]])
    assert len(merged) == 2


def test_merge_keeps_separate_boxes():
    merged = _merge_boxes([[0, 0, 30, 30], [200, 200, 30, 30]])
    assert len(merged) == 2


def test_motion_detector_finds_moving_object():
    md = MotionDetector(min_area=400)
    rng = np.random.default_rng(42)
    base = rng.integers(0, 40, (480, 640, 3), dtype=np.uint8)

    # Warm up the background model on a static scene.
    for _ in range(40):
        md.detect(base.copy())

    # Drop in a bright moving block.
    frame = base.copy()
    frame[200:280, 300:380] = 255
    dets = md.detect(frame)
    assert len(dets) >= 1
    d = max(dets, key=lambda d: d.w * d.h)
    # Box should be around the block (coords are back in full-frame space).
    assert 250 <= d.x <= 350
    assert 150 <= d.y <= 250


def test_sensitivity_mapping_monotonic():
    from allseeingeye.detect import sensitivity_to_params
    # Higher sensitivity -> smaller min_area and lower variance threshold.
    a100, v100 = sensitivity_to_params(100)
    a50, v50 = sensitivity_to_params(50)
    a1, v1 = sensitivity_to_params(1)
    assert a100 < a50 < a1
    assert v100 < v50 < v1
    assert a100 < 200 and a1 > 5000  # sane bounds


def test_set_sensitivity_updates_min_area():
    md = MotionDetector(sensitivity=100)
    high = md.min_area
    md.set_sensitivity(10)
    assert md.min_area > high


def test_ignore_zone_mutes_motion():
    # Same moving block as above, but the zone covers it -> no detections.
    zone = [{"x": 0.3, "y": 0.3, "w": 0.4, "h": 0.4}]
    md = MotionDetector(min_area=400, ignore=zone)
    rng = np.random.default_rng(42)
    base = rng.integers(0, 40, (480, 640, 3), dtype=np.uint8)
    for _ in range(40):
        md.detect(base.copy())
    frame = base.copy()
    frame[200:280, 300:380] = 255
    assert md.detect(frame) == []


def test_ignore_zone_elsewhere_keeps_detection():
    zone = [{"x": 0.0, "y": 0.0, "w": 0.2, "h": 0.2}]
    md = MotionDetector(min_area=400, ignore=zone)
    rng = np.random.default_rng(42)
    base = rng.integers(0, 40, (480, 640, 3), dtype=np.uint8)
    for _ in range(40):
        md.detect(base.copy())
    frame = base.copy()
    frame[200:280, 300:380] = 255
    assert len(md.detect(frame)) >= 1


def test_in_ignore_zone_point_check():
    from allseeingeye.detect import in_ignore_zone
    zones = [{"x": 0.0, "y": 0.0, "w": 0.5, "h": 0.5}]
    assert in_ignore_zone(0.25, 0.25, zones)
    assert not in_ignore_zone(0.75, 0.75, zones)
    assert not in_ignore_zone(0.25, 0.25, [])


def test_ensure_models_downloads_missing_files(tmp_path):
    from allseeingeye.detect import DnnDetector, ensure_models

    def fake_fetch(url, dest):
        size = 20_000_000 if "caffemodel" in url else 5_000
        with open(dest, "wb") as f:
            f.write(b"x" * size)

    assert ensure_models(str(tmp_path), fetch=fake_fetch) is True
    assert (tmp_path / DnnDetector.PROTOTXT).exists()
    assert (tmp_path / DnnDetector.WEIGHTS).exists()
    # Second call is a no-op that still reports success.
    assert ensure_models(str(tmp_path), fetch=lambda u, d: 1 / 0) is True


def test_ensure_models_rejects_truncated_download(tmp_path):
    from allseeingeye.detect import DnnDetector, ensure_models

    def tiny_fetch(url, dest):
        with open(dest, "wb") as f:
            f.write(b"not a model")

    assert ensure_models(str(tmp_path), fetch=tiny_fetch) is False
    assert not (tmp_path / DnnDetector.WEIGHTS).exists()


def test_motion_detector_quiet_on_static_scene():
    md = MotionDetector(min_area=400)
    frame = np.full((480, 640, 3), 60, np.uint8)
    for _ in range(40):
        md.detect(frame.copy())
    assert md.detect(frame.copy()) == []
