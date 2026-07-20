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
    this.selectedId = null; // keyboard-highlighted object (Tab/arrows)

    const tpl = document.getElementById("camera-template");
    this.root = tpl.content.firstElementChild.cloneNode(true);
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
  order: [],          // camera ids in display order
  activeIdx: 0,       // which camera keyboard input applies to
  solo: false,        // fullscreen the active camera
  zone: "grid",       // "grid" | "topbar" | "events"
  topbarIdx: 0,
  eventsSel: 0,
  eventsCache: [],
  confirmClear: false, // "delete ALL events?" pending confirmation
  updating: false,
};

// WASD acts exactly like the arrow pad on a remote.
const NAV_KEYS = {
  w: "up", a: "left", s: "down", d: "right",
  ArrowUp: "up", ArrowLeft: "left", ArrowDown: "down", ArrowRight: "right",
};

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

function activeCam() {
  return cameras.get(ui.order[ui.activeIdx]);
}

function setActiveCamera(i) {
  if (!ui.order.length) return;
  ui.activeIdx = ((i % ui.order.length) + ui.order.length) % ui.order.length;
  ui.order.forEach((id, idx) => {
    cameras.get(id).root.classList.toggle("active", idx === ui.activeIdx);
  });
}

function toggleSolo() {
  ui.solo = !ui.solo;
  document.getElementById("grid").classList.toggle("solo", ui.solo);
}

function topbarFocusables() {
  return [...document.querySelectorAll("#sites a"), document.getElementById("update-btn")];
}

function setTopbarFocus(i) {
  const els = topbarFocusables();
  ui.topbarIdx = Math.max(0, Math.min(els.length - 1, i));
  els.forEach((el, idx) => el.classList.toggle("focused", idx === ui.topbarIdx));
}

function setZone(zone) {
  ui.zone = zone;
  if (zone !== "events") setConfirmClear(false);
  document.getElementById("sidebar").classList.toggle("focused", zone === "events");
  if (zone === "topbar") {
    setTopbarFocus(topbarFocusables().length - 1); // land on UPDATE
  } else {
    topbarFocusables().forEach((el) => el.classList.remove("focused"));
  }
  renderEvents();
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

function moveEventSel(d) {
  if (!ui.eventsCache.length) return;
  ui.eventsSel = Math.max(0, Math.min(ui.eventsCache.length - 1, ui.eventsSel + d));
  renderEvents();
}

// ---------- bootstrap ----------

async function loadState() {
  const res = await fetch("/api/state");
  const state = await res.json();

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
    if (!cameras.has(cam.id)) {
      cameras.set(cam.id, new CameraView(cam));
      ui.order.push(cam.id);
      setActiveCamera(ui.activeIdx); // keep the active highlight applied
    }
    const view = cameras.get(cam.id);
    view.ignoreZones = cam.ignore || [];
    view.mode = cam.mode;
    view.setStatus(cam.online);
  }
}

async function loadEvents() {
  const res = await fetch("/api/events?limit=30");
  const { events } = await res.json();
  ui.eventsCache = events;
  ui.eventsSel = Math.min(ui.eventsSel, Math.max(0, events.length - 1));
  renderEvents();
}

function renderEvents() {
  const list = document.getElementById("events");
  list.innerHTML = "";
  if (!ui.eventsCache.length) {
    const empty = document.createElement("div");
    empty.className = "events-empty";
    empty.textContent = "no events";
    list.appendChild(empty);
    return;
  }
  const focused = ui.zone === "events";
  ui.eventsCache.forEach((ev, idx) => {
    const el = document.createElement("div");
    el.className = "event";
    if (focused && idx === ui.eventsSel) el.classList.add("selected");
    const when = new Date(ev.start * 1000);
    el.innerHTML = `
      <img loading="lazy" src="/api/media/${ev.snapshot}" alt="">
      <div class="ev-meta">
        <span class="ev-time"></span>
      </div>
      <button class="ev-del" title="delete this event">&times;</button>`;
    el.querySelector(".ev-time").textContent =
      when.toLocaleDateString() + " " + when.toLocaleTimeString();
    el.addEventListener("click", () => openModal(ev));
    el.querySelector(".ev-del").addEventListener("click", (e) => {
      e.stopPropagation();
      deleteEvent(ev);
    });
    list.appendChild(el);
    if (focused && idx === ui.eventsSel) el.scrollIntoView({ block: "nearest" });
  });
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
// Remote model: WASD/arrows move the focus, Enter = OK, Esc = back.
// Zones checked in order: video player -> events -> topbar -> grid.
// All bindings are listed in the hint bar under each camera feed.

function gridColumns() {
  const cols = getComputedStyle(document.getElementById("grid"))
    .gridTemplateColumns.split(" ").length;
  return Math.max(1, cols);
}

document.addEventListener("keydown", (e) => {
  const key = e.key;
  const low = key.toLowerCase();
  const nav = NAV_KEYS[key] || NAV_KEYS[low];

  const modalOpen = !document.getElementById("modal").classList.contains("hidden");
  if (modalOpen) {
    const video = document.getElementById("modal-video");
    if (key === "Escape" || key === "Backspace") closeModal();
    else if (key === " ") {
      video.paused ? video.play() : video.pause();
      e.preventDefault();
    } else if (nav === "right") video.currentTime += 5;
    else if (nav === "left") video.currentTime -= 5;
    if (nav) e.preventDefault();
    return;
  }

  if (ui.zone === "events") {
    if (ui.confirmClear) {
      if (key === "Enter") {
        setConfirmClear(false);
        clearAllEvents();
      } else {
        setConfirmClear(false); // any other key cancels
      }
      e.preventDefault();
      return;
    }
    if (nav === "down" || key === "Tab") {
      moveEventSel(key === "Tab" && e.shiftKey ? -1 : 1);
      e.preventDefault();
    } else if (nav === "up") {
      moveEventSel(-1);
      e.preventDefault();
    } else if (nav === "left") {
      setZone("grid"); // back out of the sidebar toward the cameras
      e.preventDefault();
    } else if (key === "Enter") {
      const ev = ui.eventsCache[ui.eventsSel];
      if (ev) openModal(ev);
    } else if (key === "Delete" || key === "Backspace" || low === "x") {
      const ev = ui.eventsCache[ui.eventsSel];
      if (ev) deleteEvent(ev);
      e.preventDefault();
    } else if (low === "c") {
      if (ui.eventsCache.length) setConfirmClear(true);
    } else if (key === "Escape" || low === "e") {
      setZone("grid");
    }
    return;
  }

  if (ui.zone === "topbar") {
    if (nav === "left") setTopbarFocus(ui.topbarIdx - 1);
    else if (nav === "right") setTopbarFocus(ui.topbarIdx + 1);
    else if (nav === "down" || key === "Escape") setZone("grid");
    else if (key === "Enter") {
      const el = topbarFocusables()[ui.topbarIdx];
      if (el?.id === "update-btn") startUpdate();
      else el?.click();
    }
    if (nav) e.preventDefault();
    return;
  }

  // Grid zone.
  const cam = activeCam();
  const cols = gridColumns();
  const count = ui.order.length;
  if (key >= "1" && key <= "9") {
    setActiveCamera(+key - 1);
  } else if (nav === "right") {
    if (ui.activeIdx >= count - 1) setZone("events"); // off the right edge -> sidebar
    else setActiveCamera(ui.activeIdx + 1);
    e.preventDefault();
  } else if (nav === "left") {
    if (ui.activeIdx > 0) setActiveCamera(ui.activeIdx - 1);
    e.preventDefault();
  } else if (nav === "up") {
    if (ui.activeIdx < cols) setZone("topbar"); // off the top row -> UPDATE button
    else setActiveCamera(ui.activeIdx - cols);
    e.preventDefault();
  } else if (nav === "down") {
    if (ui.activeIdx + cols < count) setActiveCamera(ui.activeIdx + cols);
    e.preventDefault();
  } else if (key === "Tab") {
    cam?.cycleSelect(e.shiftKey ? -1 : 1);
    e.preventDefault();
  } else if (key === "Enter") {
    cam?.toggleLockSelected();
  } else if (low === "f") {
    toggleSolo();
  } else if (low === "e") {
    setZone("events");
  } else if (low === "u") {
    setZone("topbar"); // lands on UPDATE; Enter confirms
  } else if (key === "Escape") {
    for (const c of cameras.values()) c.releaseLock();
    if (ui.solo) toggleSolo();
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
