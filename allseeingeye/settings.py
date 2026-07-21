"""Persisted per-camera runtime overrides (e.g. motion sensitivity).

Stored in the engine's writable state dir (next to recordings), so the
engine can save changes directly — no root privilege or config rewrite
needed. Applied over the YAML config at startup and updated live from the UI.
"""

from __future__ import annotations

import json
import os
import threading
from typing import Any, Dict

_lock = threading.Lock()
FILENAME = "overrides.json"


def _path(state_dir: str) -> str:
    return os.path.join(state_dir, FILENAME)


def load(state_dir: str) -> Dict[str, Dict[str, Any]]:
    try:
        with open(_path(state_dir)) as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def get(state_dir: str, cam_id: str, key: str, default: Any) -> Any:
    return load(state_dir).get(cam_id, {}).get(key, default)


def set(state_dir: str, cam_id: str, key: str, value: Any) -> None:
    with _lock:
        data = load(state_dir)
        data.setdefault(cam_id, {})[key] = value
        os.makedirs(state_dir, exist_ok=True)
        tmp = _path(state_dir) + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f)
        os.replace(tmp, _path(state_dir))
