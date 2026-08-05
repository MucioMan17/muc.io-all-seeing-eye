"""Entry point: python -m allseeingeye --config /etc/allseeingeye/config.yml"""

from __future__ import annotations

import argparse
import logging
import os

import uvicorn

from . import settings
from .app import create_app, state_dir_for
from .camera import CameraWorker
from .config import load_config
from .faceworker import FaceRecognizer, FaceWorker
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

    state_dir = state_dir_for(cfg)
    workers = {}
    for cam in cfg.cameras:
        worker = CameraWorker(cam, cfg.recording, events, cfg.models_dir)
        # Apply any saved per-camera overrides (e.g. motion sensitivity).
        saved = settings.get(state_dir, cam.id, "sensitivity", None)
        if saved is not None:
            worker.set_motion_sensitivity(saved)
        worker.start()
        workers[cam.id] = worker

    # Face recognition (own thread per camera; off the capture path).
    face_manager = None
    face_workers = {}
    if cfg.faces.enabled:
        faces_dir = cfg.faces.dir or os.path.join(state_dir, "faces")
        face_manager = FaceRecognizer(faces_dir, cfg.faces)
        for cam_id, worker in workers.items():
            fw = FaceWorker(worker, face_manager, cfg.faces)
            fw.start()
            face_workers[cam_id] = fw

    app = create_app(cfg, workers, events, face_manager, face_workers)
    try:
        uvicorn.run(app, host=cfg.server.host, port=cfg.server.port, log_level="warning")
    finally:
        for worker in workers.values():
            worker.stop()
        for fw in face_workers.values():
            fw.stop()


if __name__ == "__main__":
    main()
