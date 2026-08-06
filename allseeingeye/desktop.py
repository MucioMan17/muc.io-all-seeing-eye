"""Native desktop window for All-Seeing Eye — no browser, no server.

Runs the engine workers in-process (RTSP capture + motion/track + face
recognition) and renders the feed with tracking + face-name overlays, plus a
side panel of identities (times-seen-today), unknown-face alerts, and recent
sightings.

Launch:  python -m allseeingeye.desktop --config config/local.yml
"""

from __future__ import annotations

import argparse
import os
import time
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog
from urllib.parse import quote

import cv2
import numpy as np
import yaml
from PIL import Image, ImageTk

from . import recognizer
from .camera import CameraWorker
from .config import CameraConfig, load_config
from .faceworker import FaceRecognizer, FaceWorker
from .recorder import EventLog, start_retention_thread
# NB: deliberately do NOT import .app here — it pulls in fastapi/uvicorn, whose
# import chain breaks the macOS Tk re-exec when launched from a .app bundle.

# terminal-green console palette (matches the web UI's vibe)
BG = "#0a0f0a"
PANEL = "#0e150e"
LINE = "#1c2a1c"
GREEN = "#8ef7a6"
DIM = "#5f7a63"
KNOWN = (120, 247, 166)     # BGR-ish drawn as RGB below
UNKNOWN = (255, 140, 60)
TRACK = (90, 200, 120)
TEXT = "#d6f5dd"

VIDEO_W = 860


class Console:
    def __init__(self, root: tk.Tk, cfg, config_path="config/local.yml"):
        self.root = root
        self.cfg = cfg
        self.config_path = config_path
        self.events = None
        # Engine is built AFTER the window exists (see _startup). Starting the
        # camera/face threads before tk.Tk() breaks the macOS Tk re-exec when
        # launched from a .app bundle.
        self.workers = {}
        self.fm = None
        self.face_workers = {}
        self.cam_ids = []
        self.view = "grid"          # "grid" = all cameras, or a cam_id for single view
        self.video_labels = {}      # cam_id -> the Label showing that feed
        self._cell_w = VIDEO_W
        self._thumb_cache = {}

        root.title("All-Seeing Eye")
        root.configure(bg=BG)
        root.geometry("1240x760")
        root.minsize(1000, 640)
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.running = True
        self._build_ui()
        self.root.after(80, self._startup)

    def _startup(self):
        """Build and start the engine once the window is up."""
        self.events, self.workers, self.fm, self.face_workers = build_engine(self.cfg)
        self.cam_ids = list(self.workers.keys())
        self.view = "grid"
        self._rebuild_view()
        self._build_cam_buttons()
        self._tick_video()
        self._tick_panel()

    # ---------- layout ----------
    def _build_ui(self):
        top = tk.Frame(self.root, bg=BG)
        top.pack(fill="x", padx=16, pady=(12, 4))
        tk.Label(top, text="◉  MUC.IO ALL-SEEING EYE", bg=BG, fg=GREEN,
                 font=("Menlo", 17, "bold")).pack(side="left")
        self.clock = tk.Label(top, text="", bg=BG, fg=DIM, font=("Menlo", 12))
        self.clock.pack(side="right")
        self.cam_label = tk.Label(top, text="", bg=BG, fg=DIM, font=("Menlo", 12))
        self.cam_label.pack(side="right", padx=16)

        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="both", expand=True, padx=16, pady=8)

        left = tk.Frame(body, bg=BG)
        # Video area — filled with a grid (or single feed) by _rebuild_view().
        self.stage = tk.Frame(left, bg=BG)
        self.stage.pack()
        tk.Label(self.stage, bg="#000", text="starting engine…", fg=DIM,
                 width=92, height=22, font=("Menlo", 13)).pack()
        self.cam_btn_row = tk.Frame(left, bg=BG)
        self.cam_btn_row.pack(fill="x", pady=(8, 0))

        right = tk.Frame(body, bg=BG, width=340)
        right.pack(side="right", fill="y", padx=(14, 0))
        right.pack_propagate(False)

        self._section(right, "⚠ ALERTS — unknown faces")
        self.alerts_box = tk.Frame(right, bg=PANEL)
        self.alerts_box.pack(fill="x", pady=(0, 10))

        self._section(right, "◆ FACES — seen today")
        self.faces_box = tk.Frame(right, bg=PANEL)
        self.faces_box.pack(fill="both", expand=True, pady=(0, 6))
        tk.Button(right, text="＋ Add person from a photo",
                  command=self._add_person_from_photo, relief="flat", bg=PANEL, fg=GREEN,
                  font=("Menlo", 11), cursor="hand2").pack(fill="x", pady=(0, 10))

        self._section(right, "▤ RECENT SIGHTINGS")
        self.sight_box = tk.Frame(right, bg=PANEL)
        self.sight_box.pack(fill="x")

        # Pack the left column last so the fixed-width panel keeps its width.
        left.pack(side="left", fill="both", expand=True)

    def _build_cam_buttons(self):
        for c in self.cam_btn_row.winfo_children():
            c.destroy()
        grid_on = self.view == "grid"
        tk.Button(self.cam_btn_row, text="▦ All", command=self._show_grid, relief="flat",
                  bg=(GREEN if grid_on else PANEL), fg=("#04120d" if grid_on else TEXT),
                  font=("Menlo", 11), cursor="hand2").pack(side="left", padx=(0, 6))
        for cid in self.cam_ids:
            name = next((c.name for c in self.cfg.cameras if c.id == cid), cid)
            fr = tk.Frame(self.cam_btn_row, bg=BG)
            fr.pack(side="left", padx=(0, 6))
            focused = cid == self.view
            tk.Button(fr, text=name, command=lambda c=cid: self._select(c), relief="flat",
                      bg=(GREEN if focused else PANEL), fg=("#04120d" if focused else TEXT),
                      activebackground=LINE, font=("Menlo", 11)).pack(side="left")
            tk.Button(fr, text="✕", command=lambda c=cid: self._remove_camera(c),
                      relief="flat", bg=PANEL, fg="#ff6b6b", font=("Menlo", 10),
                      cursor="hand2").pack(side="left")
        tk.Button(self.cam_btn_row, text="＋ Add camera", command=self._open_add_camera_dialog,
                  relief="flat", bg=PANEL, fg=GREEN, font=("Menlo", 11),
                  cursor="hand2").pack(side="left", padx=(6, 0))

    def _show_grid(self):
        self.view = "grid"
        self._rebuild_view()
        self._build_cam_buttons()

    def _rebuild_view(self):
        """(Re)build the video area as a split-screen grid or a single feed."""
        for c in self.stage.winfo_children():
            c.destroy()
        self.video_labels = {}
        cams = self.cam_ids if self.view == "grid" else [self.view]
        cams = [c for c in cams if c in self.workers]
        if not cams:
            tk.Label(self.stage, bg="#000", text="no cameras — add one below", fg=DIM,
                     width=92, height=22, font=("Menlo", 13)).pack()
            return
        n = len(cams)
        cols = 1 if n == 1 else (2 if n <= 4 else 3)
        self._cell_w = max(240, (VIDEO_W - (cols + 1) * 4) // cols)
        for i, cid in enumerate(cams):
            r, c = divmod(i, cols)
            name = next((cc.name for cc in self.cfg.cameras if cc.id == cid), cid)
            cell = tk.Frame(self.stage, bg="#000", highlightthickness=1,
                            highlightbackground=LINE)
            cell.grid(row=r, column=c, padx=2, pady=2)
            lbl = tk.Label(cell, bg="#000", text=f"{name}\nconnecting…", fg=DIM,
                           font=("Menlo", 11), compound="center")
            lbl.pack()
            self.video_labels[cid] = lbl

    def _add_person_from_photo(self):
        if self.fm is None:
            return
        path = filedialog.askopenfilename(
            title="Pick a clear photo of the person's face",
            filetypes=[("Images", "*.jpg *.jpeg *.png *.webp *.bmp")])
        if not path:
            return
        name = simpledialog.askstring("Name", "Who is this?", parent=self.root)
        if not name or not name.strip():
            return
        img = cv2.imread(path)
        if img is None:
            messagebox.showerror("Add person", "Couldn't read that image file.")
            return
        faces = recognizer.detect(img)
        if not faces:
            messagebox.showerror("Add person", "No face found in that photo — try a clearer, closer one.")
            return
        face = max(faces, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]))
        with self.fm._lock:
            crop = recognizer.crop_face(img, face)
            self.fm.store.add(face.normed_embedding, crop, name=name.strip(), auto=False)
        messagebox.showinfo("Add person",
                            f"Added {name.strip()}. The cameras will now recognize them by name.")

    # ---------- camera management ----------
    def _next_cam_id(self):
        i = 1
        while f"cam{i}" in self.workers:
            i += 1
        return f"cam{i}"

    def _save_config(self):
        try:
            with open(self.config_path) as f:
                data = yaml.safe_load(f) or {}
        except (OSError, yaml.YAMLError):
            data = {}
        data["cameras"] = [
            {"id": c.id, "name": c.name, "source": c.source, "fps": c.fps}
            for c in self.cfg.cameras
        ]
        tmp = self.config_path + ".tmp"
        with open(tmp, "w") as f:
            yaml.safe_dump(data, f, sort_keys=False)
        os.replace(tmp, self.config_path)

    def _add_camera(self, name, source_url):
        cam_id = self._next_cam_id()
        cc = CameraConfig(id=cam_id, name=name or cam_id, source=source_url, fps=12)
        w = CameraWorker(cc, self.cfg.recording, self.events, self.cfg.models_dir)
        w.start()
        self.workers[cam_id] = w
        if self.fm is not None:
            fw = FaceWorker(w, self.fm, self.cfg.faces)
            fw.start()
            self.face_workers[cam_id] = fw
        self.cfg.cameras.append(cc)
        self._save_config()
        self.cam_ids = list(self.workers.keys())
        self.view = "grid"
        self._rebuild_view()
        self._build_cam_buttons()

    def _remove_camera(self, cam_id):
        name = next((c.name for c in self.cfg.cameras if c.id == cam_id), cam_id)
        if not messagebox.askyesno("Remove camera", f"Remove '{name}'?"):
            return
        w = self.workers.pop(cam_id, None)
        if w:
            w.stop()
        fw = self.face_workers.pop(cam_id, None)
        if fw:
            fw.stop()
        self.cfg.cameras = [c for c in self.cfg.cameras if c.id != cam_id]
        self._save_config()
        self.cam_ids = list(self.workers.keys())
        if self.view == cam_id:
            self.view = "grid"
        self._rebuild_view()
        self._build_cam_buttons()

    def _open_add_camera_dialog(self):
        win = tk.Toplevel(self.root)
        win.title("Add camera")
        win.configure(bg=BG)
        win.geometry("460x380")
        win.transient(self.root)
        win.grab_set()

        tk.Label(win, text="Add TP-Link Tapo / RTSP camera", bg=BG, fg=GREEN,
                 font=("Menlo", 14, "bold")).pack(anchor="w", padx=16, pady=(14, 2))
        tk.Label(win, text="Use the camera's local Camera Account (Tapo app ▸ Advanced "
                 "Settings ▸ Camera Account) — not your TP-Link cloud login.",
                 bg=BG, fg=DIM, font=("Menlo", 10), wraplength=420,
                 justify="left").pack(anchor="w", padx=16)

        def field(label, default="", show=None):
            fr = tk.Frame(win, bg=BG)
            fr.pack(fill="x", padx=16, pady=4)
            tk.Label(fr, text=label, bg=BG, fg=TEXT, width=11, anchor="w",
                     font=("Menlo", 12)).pack(side="left")
            e = tk.Entry(fr, bg=PANEL, fg=TEXT, insertbackground=TEXT, show=show,
                         relief="flat", font=("Menlo", 12))
            e.insert(0, default)
            e.pack(side="left", fill="x", expand=True, ipady=3)
            return e

        e_name = field("Name", "Front")
        e_ip = field("IP address", "")
        e_user = field("Username", "")
        e_pass = field("Password", "", show="•")
        e_stream = field("Stream", "stream1")
        e_port = field("Port", "554")
        status = tk.Label(win, text="", bg=BG, fg="#ff6b6b", font=("Menlo", 10))
        status.pack(anchor="w", padx=16)

        def submit():
            ip = e_ip.get().strip()
            if not ip:
                status.config(text="IP address is required")
                return
            user = quote(e_user.get().strip(), safe="")
            pw = quote(e_pass.get(), safe="")
            stream = e_stream.get().strip() or "stream1"
            port = e_port.get().strip() or "554"
            creds = f"{user}:{pw}@" if (user or pw) else ""
            url = f"rtsp://{creds}{ip}:{port}/{stream}"
            try:
                self._add_camera(e_name.get().strip(), url)
            except Exception as ex:
                status.config(text=f"error: {ex}")
                return
            win.destroy()

        btns = tk.Frame(win, bg=BG)
        btns.pack(fill="x", padx=16, pady=14, side="bottom")
        tk.Button(btns, text="Add camera", command=submit, bg=GREEN, fg="#04120d",
                  relief="flat", font=("Menlo", 12, "bold"),
                  cursor="hand2").pack(side="right")
        tk.Button(btns, text="Cancel", command=win.destroy, bg=PANEL, fg=TEXT,
                  relief="flat", font=("Menlo", 12)).pack(side="right", padx=8)

    def _section(self, parent, title):
        tk.Label(parent, text=title, bg=BG, fg=GREEN, anchor="w",
                 font=("Menlo", 12, "bold")).pack(fill="x", pady=(4, 4))

    def _select(self, cid):
        self.view = cid
        self._rebuild_view()
        self._build_cam_buttons()

    # ---------- video ----------
    def _draw_overlays(self, frame, tracks, faces):
        OBJECT = (40, 190, 255)  # amber box for detected objects (BGR)
        for t in tracks:
            if t["label"] == "motion":
                continue  # motion detection removed — only labeled objects show
            x, y, w, h = t["x"], t["y"], t["w"], t["h"]
            cv2.rectangle(frame, (x, y), (x + w, y + h), OBJECT, 2, cv2.LINE_AA)
            tag = t["label"]            # person / car / dog / ...
            cv2.rectangle(frame, (x, max(0, y - 20)),
                          (x + max(70, len(tag) * 10), y), OBJECT, -1)
            cv2.putText(frame, tag, (x + 4, max(12, y - 5)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (10, 20, 10), 1, cv2.LINE_AA)
        for f in faces:
            x, y, w, h = f["bbox"]
            color = KNOWN if f["status"] == "known" else UNKNOWN
            cv2.rectangle(frame, (x, y), (x + w, y + h), color, 2, cv2.LINE_AA)
            tag = f["name"] if f["status"] == "known" else f"{f['name']} (NEW)"
            cv2.rectangle(frame, (x, y - 22), (x + max(90, len(tag) * 10), y), color, -1)
            cv2.putText(frame, tag, (x + 4, y - 6),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (10, 20, 10), 1, cv2.LINE_AA)
        return frame

    def _tick_video(self):
        if not self.running:
            return
        online = 0
        for cid, lbl in list(self.video_labels.items()):
            w = self.workers.get(cid)
            frame = w.latest_raw() if w else None
            if w and w.online:
                online += 1
            if frame is None:
                continue
            frame = frame.copy()
            tracks = w.tracks_payload().get("objects", [])
            fw = self.face_workers.get(cid)
            faces = fw.faces_payload().get("faces", []) if fw else []
            self._draw_overlays(frame, tracks, faces)
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            img = Image.fromarray(rgb)
            cw = self._cell_w
            h = int(img.height * (cw / img.width))
            img = img.resize((cw, h))
            imgtk = ImageTk.PhotoImage(img)
            lbl.imgtk = imgtk
            lbl.configure(image=imgtk, text="")
        self.clock.configure(text=time.strftime("%Y-%m-%d  %H:%M:%S"))
        n = len(self.video_labels)
        self.cam_label.configure(
            text=(f"GRID · {n} camera{'s' if n != 1 else ''} · {online} online"
                  if self.view == "grid" else f"{self.view} · {online} online"))
        self.root.after(50, self._tick_video)

    # ---------- side panel ----------
    def _thumb(self, ident):
        path = os.path.join(self.fm.store.thumbs_dir, ident.get("thumb", ""))
        key = (ident["id"], os.path.getmtime(path) if os.path.exists(path) else 0)
        if key in self._thumb_cache:
            return self._thumb_cache[key]
        if not os.path.exists(path):
            return None
        im = Image.open(path)
        im.thumbnail((44, 44))
        tkimg = ImageTk.PhotoImage(im)
        self._thumb_cache[key] = tkimg
        return tkimg

    def _tick_panel(self):
        if not self.running:
            return
        if self.fm is not None:
            self._refresh_alerts()
            self._refresh_faces()
            self._refresh_sightings()
        self.root.after(1000, self._tick_panel)

    def _refresh_alerts(self):
        for c in self.alerts_box.winfo_children():
            c.destroy()
        alerts = self.fm.alerts(4)
        if not alerts:
            tk.Label(self.alerts_box, text="none", bg=PANEL, fg=DIM,
                     font=("Menlo", 11)).pack(anchor="w", padx=10, pady=6)
            return
        for a in alerts:
            t = time.strftime("%H:%M:%S", time.localtime(a["ts"]))
            tk.Label(self.alerts_box, text=f"{t}  {a['name']}  ({a['camera']})",
                     bg=PANEL, fg="#ffb15c", anchor="w",
                     font=("Menlo", 11)).pack(fill="x", padx=10, pady=2)

    def _refresh_faces(self):
        for c in self.faces_box.winfo_children():
            c.destroy()
        faces = self.fm.faces_with_counts()
        if not faces:
            tk.Label(self.faces_box, text="no faces enrolled yet", bg=PANEL, fg=DIM,
                     font=("Menlo", 11)).pack(anchor="w", padx=10, pady=6)
            return
        for ident in faces[:8]:
            rowf = tk.Frame(self.faces_box, bg=PANEL)
            rowf.pack(fill="x", padx=8, pady=3)
            th = self._thumb(ident)
            if th:
                lbl = tk.Label(rowf, image=th, bg=PANEL)
                lbl.image = th
                lbl.pack(side="left")
            info = tk.Frame(rowf, bg=PANEL)
            info.pack(side="left", fill="x", expand=True, padx=8)
            tk.Label(info, text=ident["name"], bg=PANEL, fg=TEXT, anchor="w",
                     font=("Menlo", 12, "bold")).pack(fill="x")
            tk.Label(info, text=f"seen today: {ident['count_today']}"
                     + (f"  ({ident['first']}–{ident['last']})" if ident['first'] else ""),
                     bg=PANEL, fg=DIM, anchor="w", font=("Menlo", 10)).pack(fill="x")
            btns = tk.Frame(rowf, bg=PANEL)
            btns.pack(side="right")
            tk.Button(btns, text="✎", command=lambda i=ident: self._rename(i),
                      relief="flat", bg=PANEL, fg=GREEN, font=("Menlo", 11),
                      cursor="hand2").pack(side="left")
            tk.Button(btns, text="✕", command=lambda i=ident: self._delete(i),
                      relief="flat", bg=PANEL, fg="#ff6b6b", font=("Menlo", 11),
                      cursor="hand2").pack(side="left")

    def _refresh_sightings(self):
        for c in self.sight_box.winfo_children():
            c.destroy()
        for s in self.fm.log.list(limit=6):
            t = time.strftime("%H:%M:%S", time.localtime(s["ts"]))
            mark = "●" if s["status"] == "known" else "○"
            tk.Label(self.sight_box, text=f"{mark} {t}  {s['name']}  {s['camera']}",
                     bg=PANEL, fg=DIM, anchor="w",
                     font=("Menlo", 10)).pack(fill="x", padx=10, pady=1)

    def _rename(self, ident):
        name = simpledialog.askstring("Rename", f"Name for {ident['name']}:",
                                      parent=self.root)
        if name:
            self.fm.store.rename(ident["id"], name.strip())

    def _delete(self, ident):
        if messagebox.askyesno("Delete", f"Delete {ident['name']}?"):
            self.fm.store.remove(ident["id"])

    def on_close(self):
        self.running = False
        for w in self.workers.values():
            w.stop()
        for fw in self.face_workers.values():
            fw.stop()
        self.root.destroy()


def build_engine(cfg):
    events = EventLog(cfg.recording.dir)
    start_retention_thread(events, cfg.recording)
    # (same as app.state_dir_for, inlined to avoid importing fastapi/uvicorn)
    state_dir = os.path.dirname(os.path.abspath(cfg.recording.dir))
    workers, face_workers = {}, {}
    fm = None
    if cfg.faces.enabled:
        faces_dir = cfg.faces.dir or os.path.join(state_dir, "faces")
        fm = FaceRecognizer(faces_dir, cfg.faces)
    for cam in cfg.cameras:
        w = CameraWorker(cam, cfg.recording, events, cfg.models_dir)
        w.start()
        workers[cam.id] = w
        if fm is not None:
            fw = FaceWorker(w, fm, cfg.faces)
            fw.start()
            face_workers[cam.id] = fw
    return events, workers, fm, face_workers


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/dev.yml")
    args = ap.parse_args()
    cfg = load_config(args.config)

    # The desktop app always stores its data under the project's own run/ folder,
    # never the Raspberry Pi default (/var/lib/allseeingeye — which on Windows
    # lands in C:\var\lib, away from the app and the assistant). Absolute path so
    # it's independent of the working directory the app was launched from.
    project = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg.recording.dir = os.path.join(project, "run", "recordings")

    root = tk.Tk()
    Console(root, cfg, args.config)
    root.lift()
    root.attributes("-topmost", True)
    root.after(700, lambda: root.attributes("-topmost", False))
    root.focus_force()
    root.mainloop()


if __name__ == "__main__":
    main()
