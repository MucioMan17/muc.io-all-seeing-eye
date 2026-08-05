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
import json
import logging
import os
import time
from urllib.parse import quote, unquote, urlsplit
from typing import Dict

from fastapi import Body, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import __version__, settings
from .camera import CameraWorker
from .config import AppConfig
from .discover import scan_rtsp_hosts
from .recorder import EventLog

log = logging.getLogger(__name__)

WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")
MJPEG_BOUNDARY = "aseframe"

# Written by install.sh with the installed commit; absent in dev checkouts.
BUILD_FILE = os.path.join(os.path.dirname(WEB_DIR), "BUILD")


def build_stamp() -> str:
    try:
        with open(BUILD_FILE) as f:
            return f.read().strip() or "dev"
    except OSError:
        return "dev"


def state_dir_for(cfg: AppConfig) -> str:
    """The engine's writable state dir (parent of the recordings dir)."""
    return os.path.dirname(os.path.abspath(cfg.recording.dir))


def create_app(cfg: AppConfig, workers: Dict[str, CameraWorker], events: EventLog,
               face_manager=None, face_workers=None) -> FastAPI:
    app = FastAPI(title="All-Seeing Eye", version=__version__)
    face_workers = face_workers or {}

    def worker_or_404(cam_id: str) -> CameraWorker:
        worker = workers.get(cam_id)
        if worker is None:
            raise HTTPException(404, f"unknown camera {cam_id!r}")
        return worker

    def faces_or_404():
        if face_manager is None:
            raise HTTPException(404, "face recognition is disabled")
        return face_manager

    @app.get("/api/health")
    def health():
        return {"ok": True, "version": __version__, "site": cfg.site_name}

    # The engine can't (and shouldn't) run the root updater itself. The UI's
    # update button drops a request flag here; the allseeingeye-update.path
    # systemd unit watches for it and runs the updater with the right
    # privileges, then reports back through update.status.
    state_dir = state_dir_for(cfg)

    @app.post("/api/update")
    def request_update():
        os.makedirs(state_dir, exist_ok=True)
        with open(os.path.join(state_dir, "update.request"), "w") as f:
            f.write(str(time.time()))
        return {"requested": True, "build": build_stamp()}

    @app.get("/api/update/status")
    def update_status():
        try:
            with open(os.path.join(state_dir, "update.status")) as f:
                parts = f.read().split()
            return {
                "state": parts[0],
                "commit": parts[1] if len(parts) > 1 else "-",
                "ts": float(parts[2]) if len(parts) > 2 else 0,
            }
        except (OSError, ValueError, IndexError):
            return {"state": "none", "commit": "-", "ts": 0}

    @app.post("/api/cameras/{cam_id}/sensitivity")
    def set_sensitivity(cam_id: str, body: dict = Body(...)):
        worker = worker_or_404(cam_id)
        try:
            value = max(0, min(100, int(body.get("value"))))
        except (TypeError, ValueError):
            raise HTTPException(400, "value must be an integer 0-100")
        worker.set_motion_sensitivity(value)
        settings.set(state_dir, cam_id, "sensitivity", value)
        return {"ok": True, "sensitivity": value}

    @app.get("/api/cameras/scan")
    async def scan_cameras():
        """Find RTSP cameras on the LAN (open port 554)."""
        hosts = await asyncio.to_thread(scan_rtsp_hosts)
        return {"cameras": hosts}

    def queue_camera_request(req: dict) -> None:
        """Drop a camera add/delete/update request for the privileged helper,
        clearing any stale status first so the UI polls the fresh outcome."""
        os.makedirs(state_dir, exist_ok=True)
        try:
            os.remove(os.path.join(state_dir, "addcamera.status"))
        except OSError:
            pass
        with open(os.path.join(state_dir, "addcamera.request"), "w") as f:
            json.dump(req, f)

    @app.post("/api/cameras")
    def add_camera(body: dict = Body(...)):
        """Queue a new camera. Accepts a full `rtsp` URL, or
        `username`/`password`/`ip` (+ optional `stream`) to build one."""
        rtsp = (body.get("rtsp") or "").strip()
        if not rtsp:
            ip = (body.get("ip") or "").strip()
            if not ip:
                raise HTTPException(400, "provide either rtsp or ip")
            user = quote(str(body.get("username", "")), safe="")
            pw = quote(str(body.get("password", "")), safe="")
            stream = (body.get("stream") or "stream2").strip().lstrip("/")
            creds = f"{user}:{pw}@" if user or pw else ""
            rtsp = f"rtsp://{creds}{ip}:554/{stream}"

        existing = set(workers.keys())
        cam_id = (body.get("id") or "").strip()
        if not cam_id or cam_id in existing:
            i = 2
            while f"cam{i}" in existing:
                i += 1
            cam_id = f"cam{i}"

        queue_camera_request({
            "action": "add",
            "id": cam_id,
            "name": (body.get("name") or "").strip(),
            "rtsp": rtsp,
            "mac": (body.get("mac") or "").strip(),
            "fps": int(body.get("fps") or 10),
            "mode": body.get("mode") if body.get("mode") in ("motion", "dnn") else "dnn",
        })
        return {"requested": True, "id": cam_id}

    @app.get("/api/cameras/add-status")
    def add_camera_status():
        try:
            with open(os.path.join(state_dir, "addcamera.status")) as f:
                parts = f.read().split()
            return {"state": parts[0], "id": parts[1] if len(parts) > 1 else "-",
                    "ts": float(parts[2]) if len(parts) > 2 else 0}
        except (OSError, ValueError, IndexError):
            return {"state": "none", "id": "-", "ts": 0}

    # Parameterized routes come after the fixed ones (scan, add-status) so
    # they don't shadow them.
    @app.get("/api/cameras/{cam_id}")
    def get_camera(cam_id: str):
        """Editable fields for one camera (password never included)."""
        cam = next((c for c in cfg.cameras if c.id == cam_id), None)
        if cam is None:
            raise HTTPException(404, f"unknown camera {cam_id!r}")
        out = {
            "id": cam.id, "name": cam.name if cam.name != cam.id else "",
            "mode": cam.detect.mode, "fps": cam.fps, "mac": cam.mac or "",
        }
        if isinstance(cam.source, str) and cam.source.startswith("rtsp"):
            parts = urlsplit(cam.source)
            userinfo, _, hostport = parts.netloc.rpartition("@")
            out["type"] = "rtsp"
            out["username"] = unquote(userinfo.split(":")[0]) if userinfo else ""
            out["ip"] = hostport.split(":")[0]
            out["stream"] = parts.path.lstrip("/") or "stream2"
        else:
            out["type"] = "usb"
            out["device"] = cam.source
        return out

    @app.patch("/api/cameras/{cam_id}")
    def edit_camera(cam_id: str, body: dict = Body(...)):
        if cam_id not in workers:
            raise HTTPException(404, f"unknown camera {cam_id!r}")
        req = {k: body.get(k) for k in
               ("name", "username", "password", "ip", "mac", "stream", "mode", "fps", "device")}
        req.update({"action": "update", "id": cam_id})
        queue_camera_request(req)
        return {"requested": True, "id": cam_id}

    @app.delete("/api/cameras/{cam_id}")
    def remove_camera(cam_id: str):
        if cam_id not in workers:
            raise HTTPException(404, f"unknown camera {cam_id!r}")
        queue_camera_request({"action": "delete", "id": cam_id})
        return {"requested": True, "id": cam_id}

    @app.get("/api/state")
    def state():
        return {
            "site": cfg.site_name,
            "version": __version__,
            "build": build_stamp(),
            "faces_enabled": face_manager is not None,
            "cameras": [
                {
                    "id": c.id,
                    "name": c.name,
                    "online": workers[c.id].online,
                    "mode": "dnn" if workers[c.id].dnn else "motion",
                    "ignore": c.detect.ignore,
                    "sensitivity": workers[c.id].motion_sensitivity,
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

    @app.delete("/api/events/{event_id}")
    def delete_event(event_id: str):
        if not events.remove(event_id):
            raise HTTPException(404, f"unknown event {event_id!r}")
        return {"deleted": True}

    @app.delete("/api/events")
    def clear_events():
        return {"deleted": events.clear()}

    # ---- faces ----

    @app.get("/api/faces")
    def list_faces(day: str | None = None):
        """Known identities with today's (or `day`'s) sighting counts."""
        fm = faces_or_404()
        return {"faces": fm.faces_with_counts(day), "enabled": True}

    @app.get("/api/faces/{identity_id}/thumb")
    def face_thumb(identity_id: str):
        fm = faces_or_404()
        ident = fm.store.by_id(identity_id)
        if not ident or not ident.get("thumb"):
            raise HTTPException(404, "no thumbnail")
        root = os.path.realpath(fm.store.thumbs_dir)
        full = os.path.realpath(os.path.join(root, ident["thumb"]))
        if not full.startswith(root + os.sep) or not os.path.isfile(full):
            raise HTTPException(404, "not found")
        return FileResponse(full, media_type="image/jpeg")

    @app.patch("/api/faces/{identity_id}")
    def rename_face(identity_id: str, body: dict = Body(...)):
        fm = faces_or_404()
        name = (body.get("name") or "").strip()
        if not name:
            raise HTTPException(400, "name required")
        if not fm.store.rename(identity_id, name):
            raise HTTPException(404, f"unknown identity {identity_id!r}")
        return {"ok": True, "id": identity_id, "name": name}

    @app.delete("/api/faces/{identity_id}")
    def delete_face(identity_id: str):
        fm = faces_or_404()
        if not fm.store.remove(identity_id):
            raise HTTPException(404, f"unknown identity {identity_id!r}")
        return {"deleted": True, "id": identity_id}

    @app.get("/api/sightings")
    def list_sightings(limit: int = 100, identity: str | None = None,
                       day: str | None = None, status: str | None = None):
        fm = faces_or_404()
        return {"sightings": fm.log.list(min(limit, 1000), identity, day, status)}

    @app.get("/api/sightings/summary")
    def sightings_summary(day: str | None = None):
        fm = faces_or_404()
        return {"summary": fm.log.summary(day)}

    @app.get("/api/alerts")
    def face_alerts(limit: int = 20):
        fm = faces_or_404()
        return {"alerts": fm.alerts(limit)}

    @app.get("/api/faces-live/{cam_id}")
    def faces_live(cam_id: str):
        """Current recognized faces (boxes + names) for overlaying on a feed."""
        fw = face_workers.get(cam_id)
        if fw is None:
            return {"ts": 0, "camera": cam_id, "faces": []}
        return fw.faces_payload()

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

    @app.middleware("http")
    async def revalidate_ui(request, call_next):
        """Make browsers re-check UI files on every load (cheap 304s when
        unchanged) so an updated install is never masked by a stale cache."""
        resp = await call_next(request)
        path = request.url.path
        if path == "/" or path.startswith("/static"):
            resp.headers["Cache-Control"] = "no-cache"
        return resp

    return app
