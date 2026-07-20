import time

import pytest

cv2 = pytest.importorskip("cv2")
from fastapi.testclient import TestClient

from allseeingeye.app import create_app
from allseeingeye.camera import CameraWorker
from allseeingeye.config import AppConfig, CameraConfig, RecordingConfig
from allseeingeye.recorder import EventLog


@pytest.fixture()
def client(tmp_path):
    cfg = AppConfig(site_name="Test Site")
    cfg.recording = RecordingConfig(dir=str(tmp_path), enabled=False)
    cfg.cameras = [CameraConfig(id="demo", name="Demo", source="synthetic",
                                width=320, height=240, fps=30)]
    events = EventLog(cfg.recording.dir)
    worker = CameraWorker(cfg.cameras[0], cfg.recording, events, str(tmp_path))
    worker.start()
    # Wait for first published frame.
    deadline = time.time() + 5
    while worker.snapshot_jpeg() is None and time.time() < deadline:
        time.sleep(0.05)
    app = create_app(cfg, {"demo": worker}, events)
    yield TestClient(app)
    worker.stop()


def test_health_and_state(client):
    assert client.get("/api/health").json()["ok"] is True
    state = client.get("/api/state").json()
    assert state["site"] == "Test Site"
    assert state["cameras"][0]["id"] == "demo"
    assert state["cameras"][0]["online"] is True


def test_snapshot_is_jpeg(client):
    r = client.get("/api/snapshot/demo")
    assert r.status_code == 200
    assert r.content[:2] == b"\xff\xd8"


def test_unknown_camera_404(client):
    assert client.get("/api/snapshot/nope").status_code == 404


def test_websocket_delivers_detections(client):
    with client.websocket_connect("/api/ws/demo") as ws:
        payload = ws.receive_json()
    assert payload["camera"] == "demo"
    assert "objects" in payload
    assert payload["frame_w"] == 320


def _seed_event(tmp_path, events_log, eid):
    d = tmp_path / "demo"
    d.mkdir(exist_ok=True)
    (d / f"{eid}.mp4").write_bytes(b"v")
    (d / f"{eid}.jpg").write_bytes(b"s")
    events_log.append({
        "id": eid, "camera": "demo", "start": time.time(),
        "video": f"demo/{eid}.mp4", "snapshot": f"demo/{eid}.jpg",
    })


def test_delete_single_event(client, tmp_path):
    from allseeingeye.recorder import EventLog
    log = EventLog(str(tmp_path))
    _seed_event(tmp_path, log, "ev1")
    _seed_event(tmp_path, log, "ev2")

    assert client.delete("/api/events/ev1").json() == {"deleted": True}
    ids = [e["id"] for e in client.get("/api/events").json()["events"]]
    assert ids == ["ev2"]
    assert not (tmp_path / "demo" / "ev1.mp4").exists()
    assert (tmp_path / "demo" / "ev2.mp4").exists()
    assert client.delete("/api/events/ev1").status_code == 404


def test_clear_all_events(client, tmp_path):
    from allseeingeye.recorder import EventLog
    log = EventLog(str(tmp_path))
    _seed_event(tmp_path, log, "ev3")
    _seed_event(tmp_path, log, "ev4")

    assert client.delete("/api/events").json()["deleted"] >= 2
    assert client.get("/api/events").json()["events"] == []
    assert not (tmp_path / "demo" / "ev3.mp4").exists()


def test_media_path_confined(client):
    assert client.get("/api/media/../../etc/passwd").status_code == 404


def test_index_served(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "ALL-SEEING EYE" in r.text
