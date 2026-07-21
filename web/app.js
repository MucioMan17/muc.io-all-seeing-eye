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
  aeroplane: "#4da3ff",
  bicycle: "#b085ff",
  dog: "#ff8fd8",
  cat: "#ff8fd8",
  bird: "#ff8fd8",
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
    this.selectedId = null; // keyboard-highlighted object (Tab/arrows)

    const tpl = document.getElementById("camera-template");
    this.root = tpl.content.firstElementChild.cloneNode(true);
    this.root.dataset.cam = this.id;
    this.statusEl = this.root.querySelector(".cam-status");
    this.camView = this.root.querySelector(".cam-view"); // the focusable canvas area
    this.canvas = this.root.querySelector(".cam-canvas");
    this.ctx = this.canvas.getContext("2d");
    document.getElementById("grid").appendChild(this.root);

    // Per-camera edit / delete (delete arms on first press, confirms on second).
    this.editBtn = this.root.querySelector(".cam-edit");
    this.delBtn = this.root.querySelector(".cam-del");
    this.editBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      openCameraModal(this.id);
    });
    this.delBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      if (this.delBtn.classList.contains("armed")) {
        deleteCamera(this.id);
      } else {
        this.delBtn.classList.add("armed");
        this.delBtn.textContent = "SURE?";
        clearTimeout(this._delTimer);
        this._delTimer = setTimeout(() => {
          this.delBtn.classList.remove("armed");
          this.delBtn.innerHTML = "&times;";
        }, 3000);
      }
    });

    this.img = new Image();
    this.reloadStream();
    // The MJPEG connection drops when the engine restarts (updates, config
    // changes) — reconnect it instead of showing a frozen/broken frame.
    this.img.addEventListener("error", () => {
      clearTimeout(this._streamRetry);
      this._streamRetry = setTimeout(() => this.reloadStream(), 2000);
    });
    this._wasOnline = false;

    // Motion sensitivity slider (per camera, persisted server-side).
    this.slider = this.root.querySelector(".cc-slider");
    this.sliderValue = this.root.querySelector(".cc-value");
    this.setSensitivityUI(typeof info.sensitivity === "number" ? info.sensitivity : 60);
    this.slider.addEventListener("input", () => {
      this.sliderValue.textContent = this.slider.value === "0" ? "OFF" : this.slider.value;
    });
    this.slider.addEventListener("change", () => {
      this.postSensitivity(parseInt(this.slider.value, 10));
    });

    this.canvas.addEventListener("click", (e) => this.onClick(e));
    this.connectWS();
  }

  setSensitivityUI(v) {
    this.slider.value = v;
    this.sliderValue.textContent = v === 0 ? "OFF" : String(v);
  }

  async postSensitivity(v) {
    this.setSensitivityUI(v);
    try {
      await fetch(`/api/cameras/${encodeURIComponent(this.id)}/sensitivity`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ value: v }),
      });
    } catch {}
  }

  nudgeSensitivity(delta) {
    const v = Math.max(0, Math.min(100, parseInt(this.slider.value, 10) + delta));
    this.postSensitivity(v);
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

  reloadStream() {
    this.img.src = `/api/stream/${this.id}?t=${Date.now()}`;
  }

  setStatus(online) {
    // A camera that just came back online needs its stream reconnected.
    if (online && !this._wasOnline) this.reloadStream();
    this._wasOnline = online;
    const stale = performance.now() - this.payloadAt > 4000;
    if (!online) {
      this.statusEl.textContent = "OFFLINE";
      this.statusEl.className = "cam-status offline";
    } else if (stale) {
      this.statusEl.textContent = "CONNECTING…";
      this.statusEl.className = "cam-status";
    } else {
      const n = this.payload.objects.length;
      const ai = this.mode === "dnn" ? "AI " : "";
      this.statusEl.textContent = n ? `● ${ai}TRACKING ${n}` : `● ${ai}LIVE`;
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
    this.camView.focus(); // clicking a camera makes it the focused element
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

  sortedObjects() {
    return [...(this.payload.objects || [])].sort((a, b) => a.id - b.id);
  }

  // Keyboard: move the selection highlight to the next/previous object.
  cycleSelect(dir) {
    const objs = this.sortedObjects();
    if (!objs.length) {
      this.selectedId = null;
      return;
    }
    const i = objs.findIndex((o) => o.id === this.selectedId);
    const ni = i === -1 ? (dir > 0 ? 0 : objs.length - 1) : (i + dir + objs.length) % objs.length;
    this.selectedId = objs[ni].id;
  }

  // Keyboard: lock the selected object (or the biggest one if none selected).
  toggleLockSelected() {
    const objs = this.sortedObjects();
    if (!objs.length) {
      this.lockId = null;
      return;
    }
    let target = objs.find((o) => o.id === this.selectedId);
    if (!target) target = objs.reduce((a, b) => (b.w * b.h > a.w * a.h ? b : a));
    this.lockId = this.lockId === target.id ? null : target.id;
    this.lockLastSeen = performance.now();
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

      // Keyboard selection highlight: white dashed halo around the box.
      if (o.id === this.selectedId && o.id !== this.lockId) {
        ctx.save();
        ctx.strokeStyle = "#ffffff";
        ctx.lineWidth = px;
        ctx.setLineDash([5 * px, 3 * px]);
        ctx.strokeRect(o.x - 5 * px, o.y - 5 * px, o.w + 10 * px, o.h + 10 * px);
        ctx.setLineDash([]);
        ctx.restore();
      }

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

// ---------- app state & keyboard control ----------

// TV-remote navigation: one focus cursor, three zones. WASD (or arrows)
// move it around; Enter is "OK"; Esc is "back".
//   topbar : remote-site links + the UPDATE button
//   grid   : the cameras
//   events : the recordings sidebar
const ui = {
  order: [],           // camera ids in display order
  soloId: null,        // camera id shown fullscreen, or null
  eventsCache: [],
  confirmClear: false, // "delete ALL events?" pending confirmation
  updating: false,
  focusInit: false,
  stateOk: true,       // is the engine currently reachable?
  editingId: null,     // camera id being edited in the modal (null = adding)
};

// WASD mirrors the arrow pad; used for navigation only when not typing.
const WASD = { w: "up", a: "left", s: "down", d: "right" };
const ARROWS = { ArrowUp: "up", ArrowLeft: "left", ArrowDown: "down", ArrowRight: "right" };

async function deleteEvent(ev) {
  await fetch(`/api/events/${encodeURIComponent(ev.id)}`, { method: "DELETE" });
  await loadEvents();
}

async function clearAllEvents() {
  await fetch("/api/events", { method: "DELETE" });
  await loadEvents();
}

function setConfirmClear(on) {
  ui.confirmClear = on;
  document.getElementById("clear-confirm").classList.toggle("hidden", !on);
}

// ---- one focus cursor across every interactive element (.nav) ----

function isVisible(el) {
  return el && el.offsetParent !== null && el.getClientRects().length > 0;
}

function navElements() {
  return [...document.querySelectorAll(".nav")].filter(isVisible);
}

// Move focus to the nearest .nav element in a direction (spatial / TV-remote).
// Edge-based: a candidate must start beyond the current element's edge in the
// travel direction, so "right" reaches the next tile (not a child below it),
// and cross-axis offset is penalised so straight-ahead wins.
function spatialMove(dir) {
  const cur = document.activeElement;
  const list = navElements();
  if (!cur || !cur.classList || !cur.classList.contains("nav")) {
    list[0]?.focus();
    return;
  }
  const r = cur.getBoundingClientRect();
  const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
  let best = null, bestScore = Infinity;
  for (const el of list) {
    if (el === cur) continue;
    const b = el.getBoundingClientRect();
    const ex = b.left + b.width / 2, ey = b.top + b.height / 2;
    let primary, cross;
    if (dir === "right") {
      if (b.left < r.right - 4) continue;
      primary = b.left - r.right; cross = Math.abs(ey - cy);
    } else if (dir === "left") {
      if (b.right > r.left + 4) continue;
      primary = r.left - b.right; cross = Math.abs(ey - cy);
    } else if (dir === "down") {
      if (b.top < r.bottom - 4) continue;
      primary = b.top - r.bottom; cross = Math.abs(ex - cx);
    } else { // up
      if (b.bottom > r.top + 4) continue;
      primary = r.top - b.bottom; cross = Math.abs(ex - cx);
    }
    const score = Math.max(primary, 0) + cross * 2;
    if (score < bestScore) { bestScore = score; best = el; }
  }
  if (best) {
    best.focus();
    best.scrollIntoView({ block: "nearest", inline: "nearest" });
  }
}

function isCameraFocused() {
  const a = document.activeElement;
  return !!(a && a.classList && a.classList.contains("cam-view"));
}

// The camera associated with the current focus (tile itself, or its slider).
function currentCamera() {
  const a = document.activeElement;
  const tile = a && a.closest ? a.closest(".camera") : null;
  if (tile && tile.dataset.cam) return cameras.get(tile.dataset.cam);
  return cameras.get(ui.order[0]);
}

function focusedEvent() {
  const a = document.activeElement;
  const id = a && a.dataset ? a.dataset.evid : null;
  return id ? ui.eventsCache.find((e) => e.id === id) : null;
}

function toggleSolo() {
  const cam = currentCamera();
  if (!cam) return;
  ui.soloId = ui.soloId === cam.id ? null : cam.id;
  for (const id of ui.order) {
    cameras.get(id).root.classList.toggle("solo-cam", id === ui.soloId);
  }
  document.getElementById("grid").classList.toggle("solo", ui.soloId !== null);
}

// Modal (add-camera) focus ring — Tab / up / down cycle through its controls.
function modalFocusables() {
  return ["ac-name", "ac-user", "ac-pass", "ac-ip", "ac-scan", "ac-found",
          "ac-mac", "ac-delete", "ac-cancel", "ac-add"]
    .map((id) => document.getElementById(id))
    .filter(isVisible);
}

function moveModalFocus(delta) {
  const list = modalFocusables();
  const i = list.indexOf(document.activeElement);
  const ni = i === -1 ? 0 : (i + delta + list.length) % list.length;
  list[ni]?.focus();
}

// ---------- one-click update ----------

async function readUpdateStatus() {
  try {
    return await (await fetch("/api/update/status")).json();
  } catch {
    return null; // engine restarting mid-update — keep polling
  }
}

async function startUpdate() {
  if (ui.updating) return;
  ui.updating = true;
  const btn = document.getElementById("update-btn");
  btn.classList.add("updating");
  btn.textContent = "UPDATING…";

  const finish = (text) => {
    ui.updating = false;
    btn.classList.remove("updating");
    btn.textContent = text;
    setTimeout(() => { btn.textContent = "UPDATE"; }, 5000);
  };

  const before = await readUpdateStatus();
  const beforeTs = before ? before.ts : 0;
  try {
    await fetch("/api/update", { method: "POST" });
  } catch {
    finish("UPDATE FAILED");
    return;
  }

  const t0 = Date.now();
  const poll = setInterval(async () => {
    if (Date.now() - t0 > 5 * 60 * 1000) {
      clearInterval(poll);
      finish("UPDATE FAILED");
      return;
    }
    const st = await readUpdateStatus();
    if (!st || st.state === "none" || st.ts === beforeTs) return; // not done yet
    clearInterval(poll);
    if (st.state === "updated") {
      btn.textContent = "RESTARTING…";
      location.reload();
    } else if (st.state === "current") {
      finish("UP TO DATE");
    } else {
      finish("BLOCKED — SEE LOGS");
    }
  }, 3000);
}

document.getElementById("update-btn").addEventListener("click", startUpdate);

// ---------- add / edit / delete camera ----------

const $ = (id) => document.getElementById(id);

function openCameraModal(camId = null) {
  ui.editingId = camId;
  for (const id of ["ac-name", "ac-user", "ac-pass", "ac-ip", "ac-mac"]) $(id).value = "";
  $("ac-found").classList.add("hidden");
  $("ac-msg").textContent = "";
  $("ac-msg").className = "";
  $("ac-rtsp").classList.remove("hidden");
  const del = $("ac-delete");
  del.classList.remove("armed");
  del.textContent = "Delete";

  if (camId) {
    $("ac-title").textContent = "EDIT CAMERA";
    $("ac-add").textContent = "Save";
    $("ac-pass-note").textContent = "(leave blank to keep current)";
    $("ac-hint").textContent = "Change this camera's settings. Leave the password blank to keep the current one.";
    del.classList.remove("hidden");
    fetch(`/api/cameras/${encodeURIComponent(camId)}`)
      .then((r) => r.json())
      .then((cam) => {
        $("ac-name").value = cam.name || "";
        if (cam.type === "usb") {
          $("ac-rtsp").classList.add("hidden"); // USB webcam: only the name is editable here
        } else {
          $("ac-user").value = cam.username || "";
          $("ac-ip").value = cam.ip || "";
          $("ac-mac").value = cam.mac || "";
        }
      })
      .catch(() => {});
  } else {
    $("ac-title").textContent = "ADD CAMERA";
    $("ac-add").textContent = "Add camera";
    $("ac-pass-note").textContent = "";
    $("ac-hint").textContent = "For a WiFi/IP camera (e.g. Tapo). Enter the camera account, then Scan to find it — or type its IP.";
    del.classList.add("hidden");
  }
  $("addcam").classList.remove("hidden");
  setTimeout(() => $("ac-name").focus(), 50);
}

function openAddcam() { openCameraModal(null); }

function closeAddcam() {
  $("addcam").classList.add("hidden");
  $("addcam-btn").focus(); // return the cursor to where it was
}

// Poll the shared camera-op status file; reload the page when it completes.
function pollCameraOp(onFail) {
  const t0 = Date.now();
  const poll = setInterval(async () => {
    if (Date.now() - t0 > 90000) {
      clearInterval(poll);
      onFail("Timed out — check the camera and try again.");
      return;
    }
    let st;
    try {
      st = await (await fetch("/api/cameras/add-status")).json();
    } catch {
      return; // engine restarting mid-op — keep polling
    }
    if (!st || st.state === "none") return;
    clearInterval(poll);
    if (["added", "updated", "deleted"].includes(st.state)) {
      const msg = $("ac-msg");
      msg.className = "ok";
      msg.textContent = "Done! Reloading…";
      setTimeout(() => location.reload(), 1000);
    } else if (st.state === "exists") {
      onFail("That camera is already set up.");
    } else {
      onFail("Could not apply the change — check the details and try again.");
    }
  }, 2000);
}

async function deleteCamera(camId) {
  const msg = $("ac-msg");
  msg.className = "";
  msg.textContent = "Removing camera…";
  try {
    await fetch(`/api/cameras/${encodeURIComponent(camId)}`, { method: "DELETE" });
  } catch {
    return;
  }
  pollCameraOp((t) => { msg.className = "error"; msg.textContent = t; });
}

async function scanForCameras() {
  const btn = document.getElementById("ac-scan");
  const msg = document.getElementById("ac-msg");
  const sel = document.getElementById("ac-found");
  btn.disabled = true;
  btn.textContent = "Scanning…";
  msg.className = "";
  msg.textContent = "Scanning your network (up to a minute)…";
  try {
    const { cameras } = await (await fetch("/api/cameras/scan")).json();
    if (!cameras.length) {
      msg.className = "error";
      msg.textContent = "No cameras found. Make sure it's on the same WiFi, then Scan again.";
    } else {
      sel.innerHTML = '<option value="">— pick a found camera —</option>';
      for (const c of cameras) {
        const o = document.createElement("option");
        o.value = JSON.stringify(c);
        o.textContent = `${c.ip}   (${c.mac})`;
        sel.appendChild(o);
      }
      sel.classList.remove("hidden");
      msg.className = "ok";
      msg.textContent = `Found ${cameras.length} camera(s) — pick one below.`;
    }
  } catch {
    msg.className = "error";
    msg.textContent = "Scan failed.";
  } finally {
    btn.disabled = false;
    btn.textContent = "Scan";
  }
}

async function submitAddcam() {
  const msg = $("ac-msg");
  const addBtn = $("ac-add");
  const val = (id) => $(id).value.trim();
  const editing = ui.editingId;
  const usb = $("ac-rtsp").classList.contains("hidden");
  const body = {
    name: val("ac-name"),
    username: val("ac-user"),
    password: $("ac-pass").value,
    ip: val("ac-ip"),
    mac: val("ac-mac"),
  };
  // IP is required only when adding an IP camera (not editing, not USB).
  if (!editing && !usb && !body.ip) {
    msg.className = "error";
    msg.textContent = "Enter the camera's IP (or Scan to find it).";
    return;
  }

  const label = addBtn.textContent;
  addBtn.disabled = true;
  addBtn.textContent = editing ? "Saving…" : "Adding…";
  msg.className = "";
  msg.textContent = editing ? "Saving…" : "Adding camera…";

  const restore = (t) => {
    msg.className = "error";
    msg.textContent = t;
    addBtn.disabled = false;
    addBtn.textContent = label;
  };
  try {
    await fetch(editing ? `/api/cameras/${encodeURIComponent(editing)}` : "/api/cameras", {
      method: editing ? "PATCH" : "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch {
    restore("Request failed.");
    return;
  }
  pollCameraOp(restore);
}

document.getElementById("addcam-btn").addEventListener("click", openAddcam);
document.getElementById("addcam-close").addEventListener("click", closeAddcam);
document.getElementById("ac-cancel").addEventListener("click", closeAddcam);
document.getElementById("ac-scan").addEventListener("click", scanForCameras);
document.getElementById("ac-add").addEventListener("click", submitAddcam);
document.getElementById("ac-delete").addEventListener("click", () => {
  const b = $("ac-delete");
  if (b.classList.contains("armed")) {
    b.classList.remove("armed");
    deleteCamera(ui.editingId);
  } else {
    b.classList.add("armed");
    b.textContent = "Confirm delete";
    setTimeout(() => { b.classList.remove("armed"); b.textContent = "Delete"; }, 3000);
  }
});
document.getElementById("ac-found").addEventListener("change", (e) => {
  if (!e.target.value) return;
  const c = JSON.parse(e.target.value);
  $("ac-ip").value = c.ip;
  if (c.mac && c.mac !== "unknown") $("ac-mac").value = c.mac;
});
document.getElementById("addcam").addEventListener("click", (e) => {
  if (e.target.id === "addcam") closeAddcam();
});

// ---------- bootstrap ----------

async function loadState() {
  let state;
  try {
    state = await (await fetch("/api/state")).json();
  } catch {
    ui.stateOk = false; // engine unreachable (restarting / updating)
    return;
  }
  if (ui.stateOk === false) {
    // Engine just came back — MJPEG streams don't fire errors when the
    // connection drops, so reconnect every camera's stream explicitly.
    for (const c of cameras.values()) c.reloadStream();
  }
  ui.stateOk = true;

  const sites = document.getElementById("sites");
  sites.innerHTML = "";
  for (const s of state.remote_sites || []) {
    const a = document.createElement("a");
    a.href = s.url;
    a.textContent = s.name;
    a.target = "_blank";
    a.className = "nav";
    a.tabIndex = 0;
    sites.appendChild(a);
  }

  for (const cam of state.cameras) {
    if (!cameras.has(cam.id)) {
      cameras.set(cam.id, new CameraView(cam));
      ui.order.push(cam.id);
    }
    const view = cameras.get(cam.id);
    view.ignoreZones = cam.ignore || [];
    view.mode = cam.mode;
    view.setStatus(cam.online);
  }

  // Land the focus cursor on the first camera once, on first load.
  if (!ui.focusInit && ui.order.length) {
    ui.focusInit = true;
    cameras.get(ui.order[0]).camView.focus();
  }
}

async function loadEvents() {
  const res = await fetch("/api/events?limit=30");
  const { events } = await res.json();
  ui.eventsCache = events;
  renderEvents();
}

function renderEvents() {
  const list = document.getElementById("events");
  // Keep the cursor on the same event across the periodic re-render.
  const keepId = document.activeElement?.dataset?.evid;
  list.innerHTML = "";
  if (!ui.eventsCache.length) {
    const empty = document.createElement("div");
    empty.className = "events-empty";
    empty.textContent = "no events";
    list.appendChild(empty);
    return;
  }
  ui.eventsCache.forEach((ev) => {
    const el = document.createElement("div");
    el.className = "event nav";
    el.tabIndex = 0;
    el.dataset.evid = ev.id;
    const when = new Date(ev.start * 1000);
    el.innerHTML = `
      <img loading="lazy" src="/api/media/${ev.snapshot}" alt="">
      <div class="ev-meta">
        <span class="ev-time"></span>
      </div>
      <button class="ev-del" tabindex="-1" title="delete this event">&times;</button>`;
    el.querySelector(".ev-time").textContent =
      when.toLocaleDateString() + " " + when.toLocaleTimeString();
    el.addEventListener("click", () => openModal(ev));
    el.querySelector(".ev-del").addEventListener("click", (e) => {
      e.stopPropagation();
      deleteEvent(ev);
    });
    list.appendChild(el);
  });
  if (keepId) {
    const again = list.querySelector(`[data-evid="${CSS.escape(keepId)}"]`);
    again?.focus();
  }
}

function openModal(ev) {
  const modal = document.getElementById("modal");
  const video = document.getElementById("modal-video");
  video.src = `/api/media/${ev.video}`;
  document.getElementById("modal-caption").textContent =
    new Date(ev.start * 1000).toLocaleString();
  modal.classList.remove("hidden");
}

function closeModal() {
  const video = document.getElementById("modal-video");
  video.pause();
  video.src = "";
  document.getElementById("modal").classList.add("hidden");
}

document.getElementById("modal-close").addEventListener("click", closeModal);
document.getElementById("modal").addEventListener("click", (e) => {
  if (e.target.id === "modal") closeModal();
});

// ---------- the keymap ----------
// One focus cursor across everything. WASD/arrows move it spatially, Enter
// selects/OK, Esc backs out. Text fields type normally; a focused slider
// takes left/right to adjust and up/down to leave. All keys are context-free
// except where a focused element claims them.

document.addEventListener("keydown", (e) => {
  const key = e.key;
  const low = key.toLowerCase();
  const a = document.activeElement;
  const isText = a && (a.tagName === "TEXTAREA" ||
                       (a.tagName === "INPUT" && a.type !== "range"));
  const isSlider = a && a.classList && a.classList.contains("cc-slider");
  // WASD is navigation only when not typing into a text field.
  const dir = ARROWS[key] || (!isText ? WASD[low] : null);

  // --- add-camera modal: form-style keyboard (type + arrows/Tab to move) ---
  if (!document.getElementById("addcam").classList.contains("hidden")) {
    if (key === "Escape") { closeAddcam(); e.preventDefault(); return; }
    if (key === "Enter") {
      if (a && a.tagName === "BUTTON") a.click();
      else submitAddcam();
      e.preventDefault();
      return;
    }
    if (key === "Tab") { moveModalFocus(e.shiftKey ? -1 : 1); e.preventDefault(); return; }
    if (dir === "up") { moveModalFocus(-1); e.preventDefault(); return; }
    if (dir === "down") { moveModalFocus(1); e.preventDefault(); return; }
    return; // typing and left/right cursor pass through
  }

  // --- video playback modal ---
  if (!document.getElementById("modal").classList.contains("hidden")) {
    const video = document.getElementById("modal-video");
    if (key === "Escape" || key === "Backspace") closeModal();
    else if (key === " ") { video.paused ? video.play() : video.pause(); e.preventDefault(); }
    else if (dir === "right") { video.currentTime += 5; e.preventDefault(); }
    else if (dir === "left") { video.currentTime -= 5; e.preventDefault(); }
    return;
  }

  // --- clear-all confirmation (armed from a focused event) ---
  if (ui.confirmClear) {
    if (key === "Enter") { setConfirmClear(false); clearAllEvents(); }
    else setConfirmClear(false);
    e.preventDefault();
    return;
  }

  // --- focused slider: left/right adjust, up/down leave ---
  if (isSlider) {
    if (dir === "left") { currentCamera()?.nudgeSensitivity(-5); e.preventDefault(); return; }
    if (dir === "right") { currentCamera()?.nudgeSensitivity(5); e.preventDefault(); return; }
    if (dir === "up" || dir === "down") { spatialMove(dir); e.preventDefault(); return; }
    if (key === "Escape") { currentCamera()?.camView.focus(); e.preventDefault(); return; }
    // letter shortcuts still fall through below
  }

  // --- number keys jump straight to a camera ---
  if (key >= "1" && key <= "9") {
    const id = ui.order[+key - 1];
    if (id) { cameras.get(id).camView.focus(); e.preventDefault(); }
    return;
  }

  // --- spatial navigation (the TV remote) ---
  if (dir) { spatialMove(dir); e.preventDefault(); return; }

  // --- Enter = select / activate the focused thing ---
  if (key === "Enter") {
    if (isCameraFocused()) {
      currentCamera()?.toggleLockSelected();
    } else if (focusedEvent()) {
      openModal(focusedEvent());
    } else if (a && a.id === "update-btn") {
      startUpdate();
    } else if (a && (a.tagName === "BUTTON" || a.tagName === "A")) {
      a.click();
    }
    return;
  }

  // --- Tab cycles detected objects on the focused camera ---
  if (key === "Tab") {
    if (isCameraFocused()) {
      currentCamera()?.cycleSelect(e.shiftKey ? -1 : 1);
    }
    e.preventDefault();
    return;
  }

  // --- actions on a focused event ---
  if (focusedEvent()) {
    if (key === "Delete" || key === "Backspace" || low === "x") {
      deleteEvent(focusedEvent());
      e.preventDefault();
      return;
    }
    if (low === "c") { if (ui.eventsCache.length) setConfirmClear(true); return; }
  }

  // --- global letter shortcuts ---
  if (low === "f") {
    toggleSolo();
  } else if (low === "e") {
    document.querySelector(".event.nav")?.focus();
  } else if (low === "u") {
    document.getElementById("update-btn").focus(); // Enter then confirms
  } else if (key === "[") {
    currentCamera()?.nudgeSensitivity(-10);
  } else if (key === "]") {
    currentCamera()?.nudgeSensitivity(10);
  } else if (key === "Escape") {
    for (const c of cameras.values()) c.releaseLock();
    if (ui.soloId) toggleSolo();
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
