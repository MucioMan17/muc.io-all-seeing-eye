/* All-Seeing Eye — live console.
 *
 * Per camera: an MJPEG <img> is drawn onto a <canvas> every animation frame,
 * and detection payloads arriving over WebSocket are drawn on top:
 *   - bounding box + label for every tracked object
 *   - motion trail behind each object
 *   - a zoom inset per object (virtually zoomed crop of the live frame)
 *     with a tracer line from the object's center to its inset
 *   - click an object to LOCK it: one big zoom panel follows that object
 *     until you click empty space / press Esc / the object disappears.
 */

"use strict";

const LABEL_COLORS = {
  motion: "#35e0a1",
  person: "#ffb020",
  car: "#4da3ff",
  bus: "#4da3ff",
  motorbike: "#4da3ff",
  bicycle: "#b085ff",
  dog: "#ff8fd8",
  cat: "#ff8fd8",
};
const LOCK_COLOR = "#ff5964";
const LOCK_LOST_MS = 3000; // auto-release a lock if the object vanishes this long
const MAX_INSETS = 4;      // unlocked mode shows insets for at most this many objects

const cameras = new Map(); // id -> CameraView

class CameraView {
  constructor(info) {
    this.id = info.id;
    this.name = info.name;
    this.payload = { objects: [], frame_w: 1280, frame_h: 720 };
    this.payloadAt = 0;
    this.lockId = null;
    this.lockLastSeen = 0;
    this.ignoreZones = info.ignore || [];

    const tpl = document.getElementById("camera-template");
    this.root = tpl.content.firstElementChild.cloneNode(true);
    this.root.querySelector(".cam-name").textContent = info.name;
    this.statusEl = this.root.querySelector(".cam-status");
    this.canvas = this.root.querySelector(".cam-canvas");
    this.ctx = this.canvas.getContext("2d");
    document.getElementById("grid").appendChild(this.root);

    this.img = new Image();
    this.img.src = `/api/stream/${this.id}?t=${Date.now()}`;

    this.canvas.addEventListener("click", (e) => this.onClick(e));
    this.connectWS();
  }

  connectWS() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    this.ws = new WebSocket(`${proto}://${location.host}/api/ws/${this.id}`);
    this.ws.onmessage = (e) => {
      this.payload = JSON.parse(e.data);
      this.payloadAt = performance.now();
    };
    this.ws.onclose = () => setTimeout(() => this.connectWS(), 2000);
    this.ws.onerror = () => this.ws.close();
  }

  setStatus(online) {
    const stale = performance.now() - this.payloadAt > 4000;
    if (!online) {
      this.statusEl.textContent = "OFFLINE";
      this.statusEl.className = "cam-status offline";
    } else if (stale) {
      this.statusEl.textContent = "CONNECTING…";
      this.statusEl.className = "cam-status";
    } else {
      const n = this.payload.objects.length;
      this.statusEl.textContent = n ? `● TRACKING ${n}` : "● LIVE";
      this.statusEl.className = "cam-status online";
    }
  }

  frameSize() {
    const w = this.payload.frame_w || this.img.naturalWidth || 1280;
    const h = this.payload.frame_h || this.img.naturalHeight || 720;
    return [w, h];
  }

  // Map a mouse event to frame-pixel coordinates.
  eventToFrame(e) {
    const rect = this.canvas.getBoundingClientRect();
    const [fw, fh] = this.frameSize();
    return [
      ((e.clientX - rect.left) / rect.width) * fw,
      ((e.clientY - rect.top) / rect.height) * fh,
    ];
  }

  onClick(e) {
    const [fx, fy] = this.eventToFrame(e);
    // Smallest box containing the click wins (innermost object).
    let hit = null;
    for (const o of this.payload.objects) {
      if (fx >= o.x && fx <= o.x + o.w && fy >= o.y && fy <= o.y + o.h) {
        if (!hit || o.w * o.h < hit.w * hit.h) hit = o;
      }
    }
    if (hit) {
      this.lockId = this.lockId === hit.id ? null : hit.id; // click again to release
      this.lockLastSeen = performance.now();
    } else {
      this.lockId = null;
    }
  }

  releaseLock() {
    this.lockId = null;
  }

  // Zoom source rectangle for an object: its box padded out, clamped to frame.
  zoomRect(o, padFactor) {
    const [fw, fh] = this.frameSize();
    const cx = o.x + o.w / 2, cy = o.y + o.h / 2;
    let w = Math.max(o.w * padFactor, 64);
    let h = Math.max(o.h * padFactor, 48);
    // Keep the crop inside the frame.
    w = Math.min(w, fw); h = Math.min(h, fh);
    let x = cx - w / 2, y = cy - h / 2;
    x = Math.max(0, Math.min(x, fw - w));
    y = Math.max(0, Math.min(y, fh - h));
    return [x, y, w, h];
  }

  draw() {
    const [fw, fh] = this.frameSize();
    if (this.canvas.width !== fw || this.canvas.height !== fh) {
      this.canvas.width = fw;
      this.canvas.height = fh;
    }
    const ctx = this.ctx;
    if (this.img.complete && this.img.naturalWidth > 0) {
      ctx.drawImage(this.img, 0, 0, fw, fh);
    } else {
      ctx.fillStyle = "#000";
      ctx.fillRect(0, 0, fw, fh);
    }

    // Muted zones: faint hatch so you can see what the detector ignores.
    for (const z of this.ignoreZones) {
      const zx = z.x * fw, zy = z.y * fh, zw = z.w * fw, zh = z.h * fh;
      ctx.save();
      ctx.fillStyle = "rgba(255, 89, 100, 0.07)";
      ctx.fillRect(zx, zy, zw, zh);
      ctx.strokeStyle = "rgba(255, 89, 100, 0.35)";
      ctx.setLineDash([8, 6]);
      ctx.lineWidth = 1.5;
      ctx.strokeRect(zx, zy, zw, zh);
      ctx.setLineDash([]);
      ctx.font = `${Math.max(10, fw / 90)}px monospace`;
      ctx.fillStyle = "rgba(255, 89, 100, 0.5)";
      ctx.fillText("MUTED", zx + 6, zy + Math.max(14, fw / 75));
      ctx.restore();
    }

    const objects = this.payload.objects || [];
    const locked = objects.find((o) => o.id === this.lockId) || null;
    const now = performance.now();

    if (this.lockId !== null) {
      if (locked) this.lockLastSeen = now;
      else if (now - this.lockLastSeen > LOCK_LOST_MS) this.lockId = null;
    }

    // --- trails + boxes ---
    const px = Math.max(2, Math.round(fw / 640)); // stroke scale for hi-res frames
    for (const o of objects) {
      const color = o.id === this.lockId ? LOCK_COLOR : (LABEL_COLORS[o.label] || "#35e0a1");

      if (o.trail && o.trail.length > 1) {
        ctx.lineWidth = px;
        for (let i = 1; i < o.trail.length; i++) {
          ctx.globalAlpha = 0.15 + 0.55 * (i / o.trail.length);
          ctx.strokeStyle = color;
          ctx.beginPath();
          ctx.moveTo(o.trail[i - 1][0], o.trail[i - 1][1]);
          ctx.lineTo(o.trail[i][0], o.trail[i][1]);
          ctx.stroke();
        }
        ctx.globalAlpha = 1;
      }

      ctx.lineWidth = o.id === this.lockId ? px * 2 : px;
      ctx.strokeStyle = color;
      ctx.strokeRect(o.x, o.y, o.w, o.h);

      // Corner ticks give it that surveillance-HUD look.
      const t = Math.min(14 * px / 2, o.w / 3);
      ctx.lineWidth = px * 1.5;
      for (const [cx, cy, dx, dy] of [
        [o.x, o.y, 1, 1], [o.x + o.w, o.y, -1, 1],
        [o.x, o.y + o.h, 1, -1], [o.x + o.w, o.y + o.h, -1, -1],
      ]) {
        ctx.beginPath();
        ctx.moveTo(cx + dx * t, cy);
        ctx.lineTo(cx, cy);
        ctx.lineTo(cx, cy + dy * t);
        ctx.stroke();
      }

      const label = `#${o.id} ${o.label.toUpperCase()}` +
        (o.label !== "motion" ? ` ${(o.conf * 100) | 0}%` : "");
      const fs = 11 * px;
      ctx.font = `${fs}px monospace`;
      const tw = ctx.measureText(label).width;
      const ly = Math.max(fs + 4, o.y - 4);
      ctx.fillStyle = "rgba(0,0,0,0.65)";
      ctx.fillRect(o.x - 1, ly - fs - 2, tw + 8, fs + 6);
      ctx.fillStyle = color;
      ctx.fillText(label, o.x + 3, ly);
    }

    // --- zoom insets ---
    if (locked) {
      this.drawZoomPanel(locked, true);
    } else {
      const withInsets = [...objects]
        .sort((a, b) => b.w * b.h - a.w * a.h)
        .slice(0, MAX_INSETS);
      let slot = 0;
      for (const o of withInsets) this.drawInset(o, slot++);
    }

    ctx.setLineDash([]);
  }

  // Small per-object zoom inset stacked along the right edge, with the
  // tracer line from the object's center to the inset.
  drawInset(o, slot) {
    const [fw, fh] = this.frameSize();
    const ctx = this.ctx;
    const iw = Math.round(fw * 0.17);
    const ih = Math.round(iw * 0.75);
    const margin = Math.round(fw * 0.012);
    const ix = fw - iw - margin;
    const iy = margin + slot * (ih + margin);
    if (iy + ih > fh) return; // out of vertical space

    this.drawZoomBox(o, ix, iy, iw, ih, 2.2, false);
  }

  drawZoomPanel(o, big) {
    const [fw, fh] = this.frameSize();
    const iw = Math.round(fw * 0.42);
    const ih = Math.round(iw * 0.75);
    const margin = Math.round(fw * 0.015);
    const cx = o.x + o.w / 2;
    // Put the big panel in the corner farthest from the object.
    const ix = cx > fw / 2 ? margin : fw - iw - margin;
    const iy = fh - ih - margin;
    this.drawZoomBox(o, ix, iy, iw, ih, 1.6, true);
  }

  drawZoomBox(o, ix, iy, iw, ih, padFactor, isLock) {
    const ctx = this.ctx;
    const [fw] = this.frameSize();
    const px = Math.max(2, Math.round(fw / 640));
    const color = isLock ? LOCK_COLOR : (LABEL_COLORS[o.label] || "#35e0a1");
    const [sx, sy, sw, sh] = this.zoomRect(o, padFactor);

    // Tracer line: object center -> inset center.
    const ocx = o.x + o.w / 2, ocy = o.y + o.h / 2;
    const icx = ix + iw / 2, icy = iy + ih / 2;
    ctx.save();
    ctx.strokeStyle = color;
    ctx.globalAlpha = 0.8;
    ctx.lineWidth = px;
    ctx.setLineDash([6 * px, 4 * px]);
    ctx.beginPath();
    ctx.moveTo(ocx, ocy);
    ctx.lineTo(icx, icy);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.globalAlpha = 1;

    ctx.beginPath();
    ctx.arc(ocx, ocy, px * 2.5, 0, Math.PI * 2);
    ctx.fillStyle = color;
    ctx.fill();

    // The zoomed crop itself, from the live frame.
    ctx.fillStyle = "#000";
    ctx.fillRect(ix, iy, iw, ih);
    if (this.img.complete && this.img.naturalWidth > 0) {
      ctx.drawImage(this.img, sx, sy, sw, sh, ix, iy, iw, ih);
    }
    ctx.lineWidth = isLock ? px * 2 : px;
    ctx.strokeStyle = color;
    ctx.strokeRect(ix, iy, iw, ih);

    const fs = 10 * px;
    ctx.font = `${fs}px monospace`;
    const tag = isLock
      ? `LOCKED #${o.id} ${o.label.toUpperCase()}`
      : `#${o.id} ×${(iw / sw).toFixed(1)}`;
    ctx.fillStyle = "rgba(0,0,0,0.65)";
    ctx.fillRect(ix, iy + ih - fs - 6, ctx.measureText(tag).width + 10, fs + 6);
    ctx.fillStyle = color;
    ctx.fillText(tag, ix + 4, iy + ih - 5);
    ctx.restore();
  }
}

// ---------- app bootstrap ----------

async function loadState() {
  const res = await fetch("/api/state");
  const state = await res.json();
  document.getElementById("site-name").textContent = state.site;
  document.title = `${state.site} — All-Seeing Eye`;

  const sites = document.getElementById("sites");
  sites.innerHTML = "";
  for (const s of state.remote_sites || []) {
    const a = document.createElement("a");
    a.href = s.url;
    a.textContent = s.name;
    a.target = "_blank";
    sites.appendChild(a);
  }

  for (const cam of state.cameras) {
    if (!cameras.has(cam.id)) cameras.set(cam.id, new CameraView(cam));
    const view = cameras.get(cam.id);
    view.ignoreZones = cam.ignore || [];
    view.setStatus(cam.online);
  }
}

async function loadEvents() {
  const res = await fetch("/api/events?limit=30");
  const { events } = await res.json();
  const list = document.getElementById("events");
  list.innerHTML = "";
  for (const ev of events) {
    const el = document.createElement("div");
    el.className = "event";
    const when = new Date(ev.start * 1000);
    el.innerHTML = `
      <img loading="lazy" src="/api/media/${ev.snapshot}" alt="">
      <div class="ev-meta">
        <span class="ev-cam"></span>
        <span class="ev-time"></span>
      </div>`;
    el.querySelector(".ev-cam").textContent = ev.camera;
    el.querySelector(".ev-time").textContent =
      when.toLocaleDateString() + " " + when.toLocaleTimeString();
    el.addEventListener("click", () => openModal(ev));
    list.appendChild(el);
  }
}

function openModal(ev) {
  const modal = document.getElementById("modal");
  const video = document.getElementById("modal-video");
  video.src = `/api/media/${ev.video}`;
  document.getElementById("modal-caption").textContent =
    `${ev.camera} — ${new Date(ev.start * 1000).toLocaleString()}`;
  modal.classList.remove("hidden");
}

document.getElementById("modal-close").addEventListener("click", () => {
  const video = document.getElementById("modal-video");
  video.pause();
  video.src = "";
  document.getElementById("modal").classList.add("hidden");
});
document.getElementById("modal").addEventListener("click", (e) => {
  if (e.target.id === "modal") document.getElementById("modal-close").click();
});

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    for (const cam of cameras.values()) cam.releaseLock();
    document.getElementById("modal-close").click();
  }
});

function tick() {
  for (const cam of cameras.values()) cam.draw();
  requestAnimationFrame(tick);
}

setInterval(() => {
  document.getElementById("clock").textContent = new Date().toLocaleString();
}, 1000);

loadState();
loadEvents();
setInterval(loadState, 5000);
setInterval(loadEvents, 5000);
requestAnimationFrame(tick);
