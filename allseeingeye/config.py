"""Configuration loading.

The whole system is driven by one YAML file (see config/config.example.yml).
Every field has a sane default so a bare `cameras:` list is enough to start.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, List, Optional, Union

import yaml


@dataclass
class DetectConfig:
    # "motion" = background-subtraction motion detection (no model needed).
    # "dnn"    = MobileNet-SSD object detection (run scripts/download-models.sh),
    #            falls back to motion if the model files are missing.
    mode: str = "motion"
    # Ignore motion blobs smaller than this many pixels (at full frame size).
    min_area: int = 600
    # Run the (more expensive) DNN pass every Nth frame; tracker coasts between.
    dnn_interval: int = 3
    # Minimum DNN confidence to report an object.
    confidence: float = 0.5
    # Only report these classes in dnn mode (empty = all COCO/VOC classes).
    classes: List[str] = field(default_factory=lambda: ["person", "car", "dog", "cat", "bicycle", "motorbike", "bus"])
    # Dead zones where detections are discarded (trees, street, neighbor's
    # yard). Rectangles in normalized 0..1 coordinates of the frame:
    #   ignore: [{x: 0.0, y: 0.0, w: 1.0, h: 0.35}]  # mute the top 35%
    ignore: List[dict] = field(default_factory=list)


@dataclass
class CameraConfig:
    id: str
    name: str = ""
    # int = USB/V4L2 device index, "rtsp://..." = IP camera, "synthetic" = test pattern.
    source: Union[int, str] = 0
    # Optional hardware MAC of an IP camera (e.g. "AA:BB:CC:DD:EE:FF").
    # When set, the current IP is discovered on the LAN and substituted into
    # the source URL — no DHCP reservation / static IP needed.
    mac: Optional[str] = None
    width: int = 1280
    height: int = 720
    fps: int = 15
    detect: DetectConfig = field(default_factory=DetectConfig)

    def __post_init__(self) -> None:
        if not self.name:
            self.name = self.id


@dataclass
class RecordingConfig:
    enabled: bool = True
    dir: str = "/var/lib/allseeingeye/recordings"
    pre_seconds: float = 3.0
    post_seconds: float = 6.0
    # Split clips this long even if motion continues, so events index promptly
    # and no single file grows unbounded.
    max_seconds: float = 120.0
    retention_days: int = 14
    # Don't start new recordings once the disk holding `dir` is this full (%).
    max_disk_percent: int = 90


@dataclass
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 8080


@dataclass
class SiteLink:
    """Another All-Seeing Eye node (e.g. the other house), reachable over VPN/LAN."""

    name: str
    url: str


@dataclass
class AppConfig:
    site_name: str = "All-Seeing Eye"
    server: ServerConfig = field(default_factory=ServerConfig)
    cameras: List[CameraConfig] = field(default_factory=list)
    recording: RecordingConfig = field(default_factory=RecordingConfig)
    remote_sites: List[SiteLink] = field(default_factory=list)
    models_dir: str = "/opt/allseeingeye/models"


def _build(cls, data: dict) -> Any:
    """Construct a dataclass from a dict, ignoring unknown keys."""
    fields = {f for f in cls.__dataclass_fields__}
    return cls(**{k: v for k, v in data.items() if k in fields})


def load_config(path: Optional[str]) -> AppConfig:
    data: dict = {}
    if path and os.path.exists(path):
        with open(path) as f:
            data = yaml.safe_load(f) or {}

    cfg = AppConfig()
    cfg.site_name = data.get("site_name", cfg.site_name)
    cfg.models_dir = data.get("models_dir", cfg.models_dir)
    if "server" in data:
        cfg.server = _build(ServerConfig, data["server"])
    if "recording" in data:
        cfg.recording = _build(RecordingConfig, data["recording"])
    for cam in data.get("cameras", []):
        cam = dict(cam)
        detect = cam.pop("detect", {})
        cc = _build(CameraConfig, cam)
        if detect:
            cc.detect = _build(DetectConfig, detect)
        cfg.cameras.append(cc)
    for site in data.get("remote_sites", []):
        cfg.remote_sites.append(_build(SiteLink, site))

    if not cfg.cameras:
        # No config: start with one synthetic demo camera so the UI is
        # explorable before any hardware is plugged in.
        cfg.cameras.append(
            CameraConfig(id="demo", name="Demo (synthetic)", source="synthetic")
        )
    return cfg
