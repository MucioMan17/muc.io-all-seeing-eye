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
import os
import re
import subprocess
import sys

DEFAULT_CONFIG = "/etc/allseeingeye/config.yml"


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


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python3 -m allseeingeye.addcamera")
    p.add_argument("--id", required=True, help="short camera id (e.g. cam2)")
    p.add_argument("--rtsp", required=True, help="rtsp:// URL")
    p.add_argument("--mac", default=None, help="camera MAC (follows IP changes)")
    p.add_argument("--fps", type=int, default=10)
    p.add_argument("--mode", default="dnn", choices=["motion", "dnn"])
    p.add_argument("--name", default=None)
    p.add_argument("--config", default=DEFAULT_CONFIG)
    p.add_argument("--no-restart", action="store_true")
    args = p.parse_args(argv)

    if not os.path.exists(args.config):
        print(f"config not found: {args.config}", file=sys.stderr)
        return 1

    with open(args.config) as f:
        text = f.read()

    block = build_block(args.id, args.rtsp, args.mac, args.fps, args.mode, args.name)
    new = add_camera(text, args.id, block)
    if new is None:
        print(f"camera '{args.id}' already in {args.config} — no change")
        return 0

    with open(args.config, "w") as f:
        f.write(new)
    print(f"added camera '{args.id}' to {args.config}")

    if not args.no_restart:
        try:
            subprocess.run(["systemctl", "restart", "allseeingeye"], check=True)
            print("restarted allseeingeye — the camera will appear in the grid shortly")
        except (OSError, subprocess.CalledProcessError) as e:
            print(f"could not restart automatically ({e});"
                  " run: sudo systemctl restart allseeingeye")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
