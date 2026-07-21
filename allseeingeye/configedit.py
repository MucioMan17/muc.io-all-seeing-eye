"""Safe add / remove / update of cameras in the YAML config.

Parse -> modify -> dump, so structure can never be corrupted by text
surgery (a broken config takes the whole system down). Comments in the
live config are not preserved; config/config.example.yml stays the
documented reference.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional
from urllib.parse import quote, unquote, urlsplit

import yaml


def load(path: str) -> dict:
    with open(path) as f:
        return yaml.safe_load(f) or {}


def save(path: str, data: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        yaml.safe_dump(data, f, sort_keys=False, default_flow_style=False)
    os.replace(tmp, path)


def _cameras(data: dict) -> List[dict]:
    cams = data.get("cameras")
    if not isinstance(cams, list):
        cams = []
        data["cameras"] = cams
    return cams


def find(data: dict, cam_id: str) -> Optional[dict]:
    for c in _cameras(data):
        if str(c.get("id")) == str(cam_id):
            return c
    return None


def build_rtsp(username: str, password: str, ip: str,
               stream: str = "stream2", port: str = "554") -> str:
    creds = ""
    if username or password:
        creds = f"{quote(username, safe='')}:{quote(password, safe='')}@"
    return f"rtsp://{creds}{ip}:{port}/{str(stream).lstrip('/')}"


def build_camera(fields: Dict[str, Any]) -> dict:
    """Build a camera dict from request fields (source already resolved)."""
    cam: Dict[str, Any] = {"id": fields["id"]}
    if fields.get("name"):
        cam["name"] = fields["name"]
    cam["source"] = fields["source"]
    if fields.get("mac"):
        cam["mac"] = fields["mac"]
    cam["fps"] = int(fields.get("fps") or 10)
    cam["detect"] = {"mode": fields.get("mode") if fields.get("mode") in ("motion", "dnn") else "dnn"}
    return cam


def add(data: dict, camera: dict) -> None:
    _cameras(data).append(camera)


def remove(data: dict, cam_id: str) -> bool:
    cams = _cameras(data)
    kept = [c for c in cams if str(c.get("id")) != str(cam_id)]
    data["cameras"] = kept
    return len(kept) != len(cams)


def _split_rtsp(url: str):
    parts = urlsplit(url)
    userinfo, _, hostport = parts.netloc.rpartition("@")
    user, _, pw = userinfo.partition(":")
    host, _, port = hostport.partition(":")
    return {
        "user": unquote(user), "pass": unquote(pw),
        "ip": host, "port": port or "554",
        "stream": parts.path.lstrip("/") or "stream2",
    }


def update(data: dict, cam_id: str, fields: Dict[str, Any]) -> bool:
    """Apply edits to a camera. Empty/absent fields are left unchanged,
    except `name` which is always set (empty clears it). For rtsp cameras a
    blank password keeps the existing one."""
    cam = find(data, cam_id)
    if cam is None:
        return False

    if "name" in fields:
        name = (fields.get("name") or "").strip()
        if name:
            cam["name"] = name
        else:
            cam.pop("name", None)
    if fields.get("mac") is not None and fields.get("mac") != "":
        cam["mac"] = fields["mac"]
    if fields.get("fps"):
        cam["fps"] = int(fields["fps"])
    if fields.get("mode") in ("motion", "dnn"):
        cam.setdefault("detect", {})["mode"] = fields["mode"]

    src = cam.get("source")
    if isinstance(src, str) and src.startswith("rtsp"):
        cur = _split_rtsp(src)
        user = fields.get("username") or cur["user"]
        pw = fields.get("password") or cur["pass"]  # blank keeps existing
        ip = fields.get("ip") or cur["ip"]
        stream = fields.get("stream") or cur["stream"]
        cam["source"] = build_rtsp(user, pw, ip, stream, cur["port"])
    elif fields.get("device") not in (None, ""):
        # USB webcam: change the /dev/video index.
        cam["source"] = int(fields["device"])

    return True
