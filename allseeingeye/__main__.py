"""Entry point: python -m allseeingeye --config config/local.yml [--lite]

Normal mode runs the full engine plus the web UI. `--lite` is "night watch":
record motion/object events with NO face recognition and NO web server — a
fraction of the CPU, safe to leave running 24/7 in the background.
"""

from __future__ import annotations

import argparse
import logging
import os
import threading
import time

from . import settings
from .camera import CameraWorker
from .config import load_config
from .faceworker import FaceRecognizer, FaceWorker
from .recorder import EventLog, start_retention_thread


def main() -> None:
    parser = argparse.ArgumentParser(prog="allseeingeye", description="All-Seeing Eye camera engine")
    parser.add_argument("--config", default="/etc/allseeingeye/config.yml",
                        help="path to YAML config (default: %(default)s)")
    parser.add_argument("--lite", action="store_true",
                        help="night-watch mode: record events only, no face recognition and no "
                             "web server — much lighter on the CPU, safe to leave running 24/7")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    log = logging.getLogger("allseeingeye")
    cfg = load_config(args.config)

    # Store data under the project's own run/ folder (never the Raspberry Pi
    # default /var/lib, which on Windows lands in C:\var\lib away from the app
    # and the assistant). Absolute so it's independent of the launch directory.
    project = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg.recording.dir = os.path.join(project, "run", "recordings")

    if args.lite:
        cfg.faces.enabled = False          # skip InsightFace — the heavy part
        log.info("NIGHT-WATCH (lite): recording events only, face recognition OFF")

    events = EventLog(cfg.recording.dir)
    start_retention_thread(events, cfg.recording)

    state_dir = os.path.dirname(os.path.abspath(cfg.recording.dir))
    workers = {}
    for cam in cfg.cameras:
        worker = CameraWorker(cam, cfg.recording, events, cfg.models_dir)
        # Apply any saved per-camera overrides (e.g. motion sensitivity).
        saved = settings.get(state_dir, cam.id, "sensitivity", None)
        if saved is not None:
            worker.set_motion_sensitivity(saved)
        worker.start()
        workers[cam.id] = worker

    # Warn when a camera drops offline or comes back — so a silent failure is
    # visible (a silent failure is exactly why last night went unrecorded).
    def _offline_watch():
        seen: dict = {}
        while True:
            for cid, w in workers.items():
                on = bool(w.online)
                if cid not in seen:
                    seen[cid] = on
                elif on != seen[cid]:
                    seen[cid] = on
                    if on:
                        log.info("camera %s is BACK ONLINE", cid)
                    else:
                        log.warning("camera %s went OFFLINE", cid)
            time.sleep(2.0)
    threading.Thread(target=_offline_watch, daemon=True).start()

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

    if args.lite:
        # No web server in night-watch mode — just keep the engine running and
        # recording. (Skips importing fastapi/uvicorn entirely.)
        log.info("Night watch active — %d camera(s) recording. Press Ctrl+C to stop.",
                 len(workers))
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            for worker in workers.values():
                worker.stop()
        return

    # Normal mode: serve the web UI (imported lazily so --lite needs neither).
    import uvicorn
    from .app import create_app
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
