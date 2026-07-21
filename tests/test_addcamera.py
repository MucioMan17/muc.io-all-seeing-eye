import json

import pytest

import allseeingeye.addcamera as ac
from allseeingeye.addcamera import add_camera, apply, build_block
from allseeingeye.config import load_config

BASE = """\
site_name: "Main House"

server:
  host: 0.0.0.0
  port: 8080

cameras:
  - id: front
    name: "Front Yard"
    source: 0
    fps: 15
    detect:
      mode: dnn

recording:
  enabled: true
  dir: /var/lib/allseeingeye/recordings

models_dir: /var/lib/allseeingeye/models
"""


def _block():
    return build_block(
        "cam2",
        "rtsp://u:p@192.168.1.50:554/stream2",
        "0C:EF:15:12:3A:18",
        10, "dnn", None,
    )


def test_insert_and_parse(tmp_path):
    new = add_camera(BASE, "cam2", _block())
    assert new is not None
    path = tmp_path / "config.yml"
    path.write_text(new)

    cfg = load_config(str(path))
    ids = {c.id for c in cfg.cameras}
    assert ids == {"front", "cam2"}
    cam2 = next(c for c in cfg.cameras if c.id == "cam2")
    assert cam2.mac == "0C:EF:15:12:3A:18"
    assert cam2.detect.mode == "dnn"
    assert cam2.source == "rtsp://u:p@192.168.1.50:554/stream2"
    # existing camera preserved
    assert any(c.id == "front" and c.source == 0 for c in cfg.cameras)


def test_idempotent_by_id():
    once = add_camera(BASE, "cam2", _block())
    assert add_camera(once, "cam2", _block()) is None


def test_missing_cameras_key_raises():
    with pytest.raises(SystemExit):
        add_camera("site_name: x\n", "cam2", _block())


def test_block_without_mac_omits_field():
    block = build_block("c", "rtsp://h/s", None, 10, "dnn", None)
    assert "mac:" not in block


def test_apply_writes_config_and_status(tmp_path, monkeypatch):
    cfg = tmp_path / "config.yml"
    cfg.write_text(BASE)
    status = tmp_path / "status"
    monkeypatch.setattr(ac, "STATUS_FILE", str(status))

    rc = apply(str(cfg), "cam2", "rtsp://u:p@h:554/stream2",
               "0C:EF:15:12:3A:18", 10, "dnn", None, restart=False, status=True)
    assert rc == 0
    assert status.read_text().startswith("added cam2 ")
    parsed = load_config(str(cfg))
    assert {c.id for c in parsed.cameras} == {"front", "cam2"}


def test_from_request_consumes_file_and_reports(tmp_path, monkeypatch):
    cfg = tmp_path / "config.yml"
    cfg.write_text(BASE)
    status = tmp_path / "status"
    monkeypatch.setattr(ac, "STATUS_FILE", str(status))
    monkeypatch.setattr(ac.subprocess, "run", lambda *a, **k: None)  # no real systemctl

    req = tmp_path / "addcamera.request"
    req.write_text(json.dumps({
        "id": "cam2", "rtsp": "rtsp://u:p@h:554/stream2",
        "mac": "0C:EF:15:12:3A:18", "fps": 10, "mode": "dnn", "name": "",
    }))
    rc = ac._from_request(str(req), str(cfg))
    assert rc == 0
    assert not req.exists()  # request consumed
    assert status.read_text().startswith("added cam2 ")


def test_from_request_bad_json_reports_error(tmp_path, monkeypatch):
    status = tmp_path / "status"
    monkeypatch.setattr(ac, "STATUS_FILE", str(status))
    req = tmp_path / "addcamera.request"
    req.write_text("{not json")
    rc = ac._from_request(str(req), str(tmp_path / "config.yml"))
    assert rc == 1
    assert not req.exists()
    assert status.read_text().startswith("error ")
