"""Add an IP camera to the config file and restart the engine.

Text-based (no YAML dependency, so it runs under the system python3): the
camera block is inserted right after the top-level `cameras:` line, and the
operation is idempotent by camera id. Usage:

  sudo python3 -m allseeingeye.addcamera \
      --id cam2 \
      --rtsp "rtsp://user:pass@HOST:554/stream2" \
      --mac 0C:EF:15:12:3A:18

Optional: --fps, --mode {motion,dnn}, --name, --config, --no-restart.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time

DEFAULT_CONFIG = "/etc/allseeingeye/config.yml"
STATUS_FILE = "/var/lib/allseeingeye/addcamera.status"


def write_status(state: str, cam_id: str = "-") -> None:
    """Record the outcome so the UI can poll /api/cameras/add-status."""
    try:
        with open(STATUS_FILE, "w") as f:
            f.write(f"{state} {cam_id} {int(time.time())}")
    except OSError:
        pass


def build_block(cam_id: str, rtsp: str, mac: str | None, fps: int,
                mode: str, name: str | None, indent: str = "  ") -> str:
    lines = [f"{indent}- id: {cam_id}"]
    if name:
        lines.append(f'{indent}  name: "{name}"')
    lines.append(f'{indent}  source: "{rtsp}"')
    if mac:
        lines.append(f'{indent}  mac: "{mac}"')
    lines += [
        f"{indent}  fps: {fps}",
        f"{indent}  detect:",
        f"{indent}    mode: {mode}",
    ]
    return "\n".join(lines) + "\n"


def add_camera(text: str, cam_id: str, block: str) -> str | None:
    """Insert `block` after the `cameras:` line. Returns new text, or None if
    a camera with this id already exists."""
    if re.search(rf'(?m)^\s*-\s*id:\s*{re.escape(cam_id)}\s*$', text):
        return None
    new, n = re.subn(r'(?m)^cameras:[ \t]*$', "cameras:\n" + block, text, count=1)
    if n == 0:
        raise SystemExit("no top-level 'cameras:' line found in the config")
    return new


def apply(config: str, cam_id: str, rtsp: str, mac, fps: int, mode: str,
          name, restart: bool, status: bool = False) -> int:
    """Insert the camera and optionally restart the engine. Shared by the
    CLI and the UI request path."""
    if not os.path.exists(config):
        print(f"config not found: {config}", file=sys.stderr)
        if status:
            write_status("error", cam_id)
        return 1

    with open(config) as f:
        text = f.read()

    block = build_block(cam_id, rtsp, mac, fps, mode, name)
    new = add_camera(text, cam_id, block)
    if new is None:
        print(f"camera '{cam_id}' already in {config} — no change")
        if status:
            write_status("exists", cam_id)
        return 0

    with open(config, "w") as f:
        f.write(new)
    print(f"added camera '{cam_id}' to {config}")
    if status:
        write_status("added", cam_id)

    if restart:
        try:
            subprocess.run(["systemctl", "restart", "allseeingeye"], check=True)
            print("restarted allseeingeye — the camera will appear in the grid shortly")
        except (OSError, subprocess.CalledProcessError) as e:
            print(f"could not restart automatically ({e});"
                  " run: sudo systemctl restart allseeingeye")
    return 0


def _from_request(path: str, config: str) -> int:
    """Add a camera described by a JSON request file dropped by the UI."""
    try:
        with open(path) as f:
            req = json.load(f)
    except (OSError, ValueError) as e:
        print(f"bad request file {path}: {e}", file=sys.stderr)
        write_status("error")
        return 1
    finally:
        try:
            os.remove(path)  # consume the request either way
        except OSError:
            pass
    if not req.get("id") or not req.get("rtsp"):
        write_status("error", req.get("id", "-"))
        return 1
    return apply(
        config, req["id"], req["rtsp"], req.get("mac") or None,
        int(req.get("fps") or 10), req.get("mode") or "dnn",
        req.get("name") or None, restart=True, status=True,
    )


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python3 -m allseeingeye.addcamera")
    p.add_argument("--id", help="short camera id (e.g. cam2)")
    p.add_argument("--rtsp", help="rtsp:// URL")
    p.add_argument("--mac", default=None, help="camera MAC (follows IP changes)")
    p.add_argument("--fps", type=int, default=10)
    p.add_argument("--mode", default="dnn", choices=["motion", "dnn"])
    p.add_argument("--name", default=None)
    p.add_argument("--config", default=DEFAULT_CONFIG)
    p.add_argument("--no-restart", action="store_true")
    p.add_argument("--from-request", metavar="FILE",
                   help="apply a camera described by a JSON request file (UI path)")
    args = p.parse_args(argv)

    if args.from_request:
        return _from_request(args.from_request, args.config)

    if not (args.id and args.rtsp):
        p.error("--id and --rtsp are required (or use --from-request)")
    return apply(args.config, args.id, args.rtsp, args.mac, args.fps,
                 args.mode, args.name, restart=not args.no_restart)


if __name__ == "__main__":
    raise SystemExit(main())
