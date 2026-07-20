"""Entry point: python -m allseeingeye --config /etc/allseeingeye/config.yml"""

from __future__ import annotations

import argparse
import logging

import uvicorn

from .app import create_app
from .camera import CameraWorker
from .config import load_config
from .recorder import EventLog, start_retention_thread


def main() -> None:
    parser = argparse.ArgumentParser(prog="allseeingeye", description="All-Seeing Eye camera engine")
    parser.add_argument("--config", default="/etc/allseeingeye/config.yml",
                        help="path to YAML config (default: %(default)s)")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    cfg = load_config(args.config)

    events = EventLog(cfg.recording.dir)
    start_retention_thread(events, cfg.recording)

    workers = {}
    for cam in cfg.cameras:
        worker = CameraWorker(cam, cfg.recording, events, cfg.models_dir)
        worker.start()
        workers[cam.id] = worker

    app = create_app(cfg, workers, events)
    try:
        uvicorn.run(app, host=cfg.server.host, port=cfg.server.port, log_level="warning")
    finally:
        for worker in workers.values():
            worker.stop()


if __name__ == "__main__":
    main()
