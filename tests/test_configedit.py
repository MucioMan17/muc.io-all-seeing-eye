from allseeingeye import configedit as ce
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
      mode: motion
  - id: cam2
    source: "rtsp://allseeingeye:p%40ss@192.168.1.237:554/stream2"
    mac: "0C:EF:15:12:3A:18"
    fps: 10
    detect:
      mode: dnn
recording:
  dir: /var/lib/allseeingeye/recordings
models_dir: /var/lib/allseeingeye/models
"""


def _roundtrip(tmp_path, text):
    p = tmp_path / "config.yml"
    p.write_text(text)
    data = ce.load(str(p))
    return p, data


def test_remove_camera(tmp_path):
    p, data = _roundtrip(tmp_path, BASE)
    assert ce.remove(data, "front") is True
    ce.save(str(p), data)
    cfg = load_config(str(p))
    assert {c.id for c in cfg.cameras} == {"cam2"}


def test_remove_missing_returns_false(tmp_path):
    _, data = _roundtrip(tmp_path, BASE)
    assert ce.remove(data, "nope") is False


def test_add_camera(tmp_path):
    p, data = _roundtrip(tmp_path, BASE)
    cam = ce.build_camera({
        "id": "cam3", "name": "Drive",
        "source": ce.build_rtsp("u", "pw", "192.168.1.9"),
        "mac": "AA:BB", "fps": 8, "mode": "dnn",
    })
    ce.add(data, cam)
    ce.save(str(p), data)
    cfg = load_config(str(p))
    c3 = next(c for c in cfg.cameras if c.id == "cam3")
    assert c3.source == "rtsp://u:pw@192.168.1.9:554/stream2"
    assert c3.mac == "AA:BB"


def test_update_password_only_keeps_username_and_ip(tmp_path):
    p, data = _roundtrip(tmp_path, BASE)
    assert ce.update(data, "cam2", {"password": "newsecret"}) is True
    ce.save(str(p), data)
    cfg = load_config(str(p))
    cam2 = next(c for c in cfg.cameras if c.id == "cam2")
    assert cam2.source == "rtsp://allseeingeye:newsecret@192.168.1.237:554/stream2"


def test_update_blank_password_keeps_existing(tmp_path):
    _, data = _roundtrip(tmp_path, BASE)
    ce.update(data, "cam2", {"username": "newuser", "password": ""})
    cam = ce.find(data, "cam2")
    # existing password (p@ss) preserved, username changed
    assert cam["source"] == "rtsp://newuser:p%40ss@192.168.1.237:554/stream2"


def test_update_ip_and_name(tmp_path):
    _, data = _roundtrip(tmp_path, BASE)
    ce.update(data, "cam2", {"ip": "10.0.0.5", "name": "Backyard"})
    cam = ce.find(data, "cam2")
    assert "10.0.0.5" in cam["source"]
    assert cam["name"] == "Backyard"


def test_update_clears_name_when_blank(tmp_path):
    _, data = _roundtrip(tmp_path, BASE)
    ce.update(data, "front", {"name": ""})
    assert "name" not in ce.find(data, "front")


def test_update_usb_device_index(tmp_path):
    _, data = _roundtrip(tmp_path, BASE)
    ce.update(data, "front", {"device": "2"})
    assert ce.find(data, "front")["source"] == 2


def test_password_with_special_chars_roundtrips(tmp_path):
    p, data = _roundtrip(tmp_path, BASE)
    ce.update(data, "cam2", {"password": "a:b@c/d"})
    ce.save(str(p), data)
    # config loader + re-parse yields the original password back
    cfg = load_config(str(p))
    cam2 = next(c for c in cfg.cameras if c.id == "cam2")
    got = ce._split_rtsp(cam2.source)
    assert got["pass"] == "a:b@c/d"
