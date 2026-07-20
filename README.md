# 👁 All-Seeing Eye

An AI security camera OS for the Raspberry Pi 500. The Pi boots straight into
a fullscreen camera console; moving objects get a tracking box, a motion
trail, and a tracer line to a live zoomed inset — click any object to lock a
big zoom panel onto it. Motion events are recorded with pre-roll and browsable
from the UI. Runs one USB webcam or many WiFi cameras, and federates across
sites (two houses, one pane of glass).

Built for **your own property**: everything is processed and stored locally on
the Pi. No cloud, no accounts, no footage leaving your network.

## What it does

- **Boots into the console.** systemd starts the camera engine at boot and a
  minimal Wayland kiosk (cage + Chromium) puts the live UI on the attached
  display. No desktop, no login screen — the Pi *is* the camera OS.
- **Detects and tracks.** Background-subtraction motion detection out of the
  box; optional MobileNet-SSD object detection (person / car / dog / …) via
  one download script. A centroid tracker gives every object a stable ID and
  a motion trail.
- **The overlay you asked for.** Bounding box + corner ticks on every tracked
  object, a dashed tracer line from the box center to a per-object zoom inset
  showing a virtually zoomed live crop. Click an object to **lock**: one big
  zoom panel follows it (click again / Esc to release, auto-releases if the
  object leaves).
- **100% keyboard-drivable** — built for the Pi 500's built-in keyboard, no
  mouse needed: `1–9`/arrows switch cameras, `Tab` cycles objects, `Enter`
  locks, `F` fullscreens, `E` browses events, `H` shows all keys.
- **Records evidence.** Motion-triggered MP4 clips with seconds of pre-roll,
  JPEG snapshot thumbnails, an event timeline in the sidebar, retention +
  disk-ceiling cleanup.
- **Scales.** Cameras are entries in a YAML list — USB index or `rtsp://` URL.
  The UI is a responsive grid. A second Pi at the other house runs the same
  image; link the two over Tailscale/WireGuard and each site's UI shows a
  button to the other ([docs/MULTISITE.md](docs/MULTISITE.md)).

## Quick start (Pi 500)

Flash **Raspberry Pi OS Lite (64-bit)**, boot, SSH in:

```bash
git clone https://github.com/MucioMan17/muc.io-all-seeing-eye.git
cd muc.io-all-seeing-eye
sudo bash system/install.sh
sudo reboot
```

The display now boots into the console, and the same UI is at
`http://<pi-address>:8080` from any phone/laptop on your network.

- Cameras + settings: `/etc/allseeingeye/config.yml`
  (see [config/config.example.yml](config/config.example.yml)), then
  `sudo systemctl restart allseeingeye`.
- Labeled detection (person/car/…): `bash scripts/download-models.sh`, set
  `detect.mode: dnn`, restart.
- Building a flash-and-go .img instead: [os/README.md](os/README.md).

**Important — Pi 500 has no camera ribbon connector.** Use USB webcams or
WiFi/RTSP cameras. Recommendations in [docs/CAMERAS.md](docs/CAMERAS.md).

## Updating

Installs keep themselves current: a systemd timer checks the repo's branch
every 15 minutes and, when there are new commits, pulls and re-runs the
installer automatically (config and recordings are never touched; the
on-screen console restarts only when UI files changed). Useful commands:

```bash
sudo systemctl start allseeingeye-update       # check right now
journalctl -u allseeingeye-update -n 20        # see what the updater did
sudo systemctl disable --now allseeingeye-update.timer   # opt out
```

Manual `git pull && sudo bash system/install.sh` still works any time.
Auto-update skips (and says so in the journal) if the repo has local
uncommitted changes, rather than overwriting them.

## Try it with zero hardware

```bash
bash scripts/run-dev.sh    # synthetic camera with moving objects
# open http://localhost:8080
```

## Architecture

```
 USB webcam ─────┐
 RTSP cam (WiFi) ┼─▶ CameraWorker ─▶ detector ─▶ tracker ─▶ recorder
 synthetic test ─┘   (per camera)   (motion/DNN)  (IDs+trails)  (pre-roll MP4)
                          │
                          ▼ latest JPEG + track payload
                    FastAPI server ──▶ MJPEG stream  /api/stream/{cam}
                                  ──▶ WebSocket     /api/ws/{cam}
                                  ──▶ events/media  /api/events, /api/media
                          │
              ┌───────────┴────────────┐
        kiosk console            any browser on
        (boots on HDMI)          LAN / VPN (other house)
```

All detection drawing (boxes, trails, tracer lines, zoom insets, lock panel)
happens client-side on a canvas, so overlays cost the Pi nothing per viewer
and recorded clips stay clean.

## Repo layout

| Path | What |
|---|---|
| `allseeingeye/` | Python engine: capture, detect, track, record, serve |
| `web/` | The live console UI (vanilla JS canvas overlay) |
| `system/` | Installer + systemd units (engine + kiosk boot) |
| `os/` | pi-gen stage for building a flashable image |
| `config/` | Example + dev configuration |
| `scripts/` | Model download, dev runner |
| `docs/` | Camera hardware guide, multi-site (two houses) guide |
| `tests/` | Unit tests for tracker/detector/recorder/API |

## Roadmap

- [ ] Per-camera detection zones (ignore the street, watch the driveway)
- [ ] Hailo-8L AI HAT+ support for heavier models at full frame rate
- [ ] Push notifications (ntfy.sh) on person detection
- [ ] Timeline scrubber over recorded clips
- [ ] Authentication for exposure beyond trusted LAN/VPN
