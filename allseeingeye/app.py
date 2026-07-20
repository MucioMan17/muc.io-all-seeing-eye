"""HTTP/WebSocket server.

Serves the web UI plus:
  GET  /api/state                 site + camera inventory (UI bootstrap)
  GET  /api/stream/{cam}          live MJPEG stream
  WS   /api/ws/{cam}              detection payloads (boxes, labels, trails)
  GET  /api/snapshot/{cam}        single JPEG frame
  GET  /api/events                recent motion events (newest first)
  GET  /api/media/{path}          recorded clips + snapshots
  GET  /api/health                liveness for monitoring/federation
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Dict

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .camera import CameraWorker
from .config import AppConfig
from .recorder import EventLog

log = logging.getLogger(__name__)

WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")
MJPEG_BOUNDARY = "aseframe"


def create_app(cfg: AppConfig, workers: Dict[str, CameraWorker], events: EventLog) -> FastAPI:
    app = FastAPI(title="All-Seeing Eye", version=__version__)

    def worker_or_404(cam_id: str) -> CameraWorker:
        worker = workers.get(cam_id)
        if worker is None:
            raise HTTPException(404, f"unknown camera {cam_id!r}")
        return worker

    @app.get("/api/health")
    def health():
        return {"ok": True, "version": __version__, "site": cfg.site_name}

    @app.get("/api/state")
    def state():
        return {
            "site": cfg.site_name,
            "version": __version__,
            "cameras": [
                {
                    "id": c.id,
                    "name": c.name,
                    "online": workers[c.id].online,
                    "mode": "dnn" if workers[c.id].dnn else "motion",
                    "ignore": c.detect.ignore,
                }
                for c in cfg.cameras
            ],
            "remote_sites": [{"name": s.name, "url": s.url} for s in cfg.remote_sites],
        }

    @app.get("/api/stream/{cam_id}")
    async def stream(cam_id: str):
        worker = worker_or_404(cam_id)

        async def frames():
            seq = -1
            while True:
                seq, jpeg = await asyncio.to_thread(worker.wait_jpeg, seq)
                if jpeg is None:
                    await asyncio.sleep(0.2)
                    continue
                yield (
                    f"--{MJPEG_BOUNDARY}\r\n"
                    f"Content-Type: image/jpeg\r\nContent-Length: {len(jpeg)}\r\n\r\n"
                ).encode() + jpeg + b"\r\n"

        return StreamingResponse(
            frames(),
            media_type=f"multipart/x-mixed-replace; boundary={MJPEG_BOUNDARY}",
            headers={"Cache-Control": "no-store"},
        )

    @app.websocket("/api/ws/{cam_id}")
    async def detections(ws: WebSocket, cam_id: str):
        worker = workers.get(cam_id)
        await ws.accept()
        if worker is None:
            await ws.close(code=4004)
            return
        try:
            last_ts = 0.0
            while True:
                payload = worker.tracks_payload()
                if payload["ts"] != last_ts:
                    last_ts = payload["ts"]
                    await ws.send_json(payload)
                await asyncio.sleep(1.0 / max(1, worker.cfg.fps))
        except WebSocketDisconnect:
            pass

    @app.get("/api/snapshot/{cam_id}")
    def snapshot(cam_id: str):
        jpeg = worker_or_404(cam_id).snapshot_jpeg()
        if jpeg is None:
            raise HTTPException(503, "no frame yet")
        return Response(jpeg, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.get("/api/events")
    def list_events(limit: int = 50, camera: str | None = None):
        return {"events": events.list(limit=min(limit, 500), camera=camera)}

    @app.get("/api/media/{path:path}")
    def media(path: str):
        root = os.path.realpath(cfg.recording.dir)
        full = os.path.realpath(os.path.join(root, path))
        # Confine to the recordings dir — path is user input.
        if not full.startswith(root + os.sep) or not os.path.isfile(full):
            raise HTTPException(404, "not found")
        return FileResponse(full)

    @app.get("/")
    def index():
        return FileResponse(os.path.join(WEB_DIR, "index.html"))

    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
    return app
