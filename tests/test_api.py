import json
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


def test_update_request_creates_flag_and_status_roundtrip(client, tmp_path):
    import os
    state_dir = os.path.dirname(str(tmp_path))
    flag = os.path.join(state_dir, "update.request")
    status = os.path.join(state_dir, "update.status")
    try:
        r = client.post("/api/update")
        assert r.json()["requested"] is True
        assert os.path.exists(flag)

        # No updater has run yet.
        assert client.get("/api/update/status").json()["state"] == "none"

        # Simulate the updater reporting back.
        with open(status, "w") as f:
            f.write("updated abc1234 1784560000")
        st = client.get("/api/update/status").json()
        assert st["state"] == "updated"
        assert st["commit"] == "abc1234"
        assert st["ts"] == 1784560000.0
    finally:
        for p in (flag, status):
            if os.path.exists(p):
                os.remove(p)


def test_state_includes_build(client):
    assert client.get("/api/state").json()["build"] == "dev"


def test_add_camera_builds_rtsp_and_writes_request(client, tmp_path):
    import os
    state_dir = os.path.dirname(str(tmp_path))
    req_path = os.path.join(state_dir, "addcamera.request")
    try:
        r = client.post("/api/cameras", json={
            "username": "allseeingeye", "password": "p@ss:word",
            "ip": "192.168.1.50", "mac": "0C:EF:15:12:3A:18", "name": "Yard",
        })
        body = r.json()
        assert body["requested"] is True
        # first added camera alongside the fixture's 'demo' -> cam2
        assert body["id"] == "cam2"
        with open(req_path) as f:
            req = json.load(f)
        # password special chars are URL-encoded in the built rtsp
        assert req["rtsp"] == "rtsp://allseeingeye:p%40ss%3Aword@192.168.1.50:554/stream2"
        assert req["mac"] == "0C:EF:15:12:3A:18"
        assert req["name"] == "Yard"
    finally:
        if os.path.exists(req_path):
            os.remove(req_path)


def test_add_camera_requires_ip_or_rtsp(client):
    assert client.post("/api/cameras", json={"username": "x"}).status_code == 400


def test_get_camera_fields(client):
    r = client.get("/api/cameras/demo")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == "demo"
    assert "password" not in body  # never exposed
    assert client.get("/api/cameras/nope").status_code == 404


def test_delete_camera_writes_request(client, tmp_path):
    import os
    req_path = os.path.join(os.path.dirname(str(tmp_path)), "addcamera.request")
    try:
        assert client.delete("/api/cameras/demo").json()["requested"] is True
        with open(req_path) as f:
            req = json.load(f)
        assert req == {"action": "delete", "id": "demo"}
        assert client.delete("/api/cameras/nope").status_code == 404
    finally:
        if os.path.exists(req_path):
            os.remove(req_path)


def test_patch_camera_writes_update_request(client, tmp_path):
    import os
    req_path = os.path.join(os.path.dirname(str(tmp_path)), "addcamera.request")
    try:
        r = client.patch("/api/cameras/demo", json={"name": "Backyard", "password": "new"})
        assert r.json()["requested"] is True
        with open(req_path) as f:
            req = json.load(f)
        assert req["action"] == "update"
        assert req["id"] == "demo"
        assert req["name"] == "Backyard"
        assert req["password"] == "new"
        assert client.patch("/api/cameras/nope", json={}).status_code == 404
    finally:
        if os.path.exists(req_path):
            os.remove(req_path)


def test_add_camera_status_route_not_shadowed(client):
    # /api/cameras/add-status must not be captured by /api/cameras/{cam_id}
    assert client.get("/api/cameras/add-status").json()["state"] == "none"


def test_add_camera_status_default(client):
    assert client.get("/api/cameras/add-status").json()["state"] == "none"


def test_sensitivity_set_and_reflected_in_state(client, tmp_path):
    import os
    ov = os.path.join(os.path.dirname(str(tmp_path)), "overrides.json")
    try:
        r = client.post("/api/cameras/demo/sensitivity", json={"value": 25})
        assert r.json() == {"ok": True, "sensitivity": 25}
        cam = client.get("/api/state").json()["cameras"][0]
        assert cam["sensitivity"] == 25
        # persisted to the overrides file
        assert os.path.exists(ov)
    finally:
        if os.path.exists(ov):
            os.remove(ov)


def test_sensitivity_clamped_and_validated(client):
    assert client.post("/api/cameras/demo/sensitivity", json={"value": 999}).json()["sensitivity"] == 100
    assert client.post("/api/cameras/demo/sensitivity", json={"value": "x"}).status_code == 400
    assert client.post("/api/cameras/nope/sensitivity", json={"value": 50}).status_code == 404


def test_media_path_confined(client):
    assert client.get("/api/media/../../etc/passwd").status_code == 404


def test_index_served(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "MUC.IO ALL SEEING EYE" in r.text


def test_ui_files_marked_no_cache(client):
    assert client.get("/").headers["Cache-Control"] == "no-cache"
    assert client.get("/static/app.js").headers["Cache-Control"] == "no-cache"
    # API responses are unaffected by the UI cache rule.
    assert client.get("/api/state").headers.get("Cache-Control") != "no-cache"
