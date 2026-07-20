# Camera hardware guide (Pi 500)

**The one thing to know:** unlike a regular Pi 5, the **Pi 500 has no CSI
camera ribbon connector**, so the official Raspberry Pi Camera Modules are
out. That leaves two (better, more scalable) options, both fully supported:

## Option 1 — USB webcam (your starting camera)

Plug into any USB port, set `source: 0` in the config, done. Good picks:

| Camera | Why |
|---|---|
| **Logitech C920/C920s** (~$60) | The default answer. 1080p30, solid low-light for a webcam, rock-solid Linux UVC support, easy to find used. |
| Logitech C270 (~$20) | Cheapest sane option, 720p. Fine for a first camera to develop against. |
| Arducam 1080p Day/Night USB | USB camera with IR LEDs + mechanical IR-cut — actual night vision on a USB budget. |

Any UVC-compliant webcam works (`ls /dev/video*` to check). USB is best for
a camera physically near the Pi 500 — e.g. watching the room/window where
the Pi sits.

## Option 2 — WiFi/PoE IP cameras via RTSP (how you scale)

For outdoor mounting and multiple locations you want IP cameras that expose
an **open RTSP stream** — the camera connects to your WiFi, and the Pi pulls
the stream. Add one `rtsp://` line per camera in the config; that's the whole
integration.

**The rule when buying: it must support RTSP or ONVIF without a cloud
account.** Recommended, all verified RTSP-friendly:

| Camera | Notes |
|---|---|
| **Reolink RLC-410W / W-series** (~$50–80) | Outdoor, night vision, dual-band WiFi, clean RTSP URLs (`.../h264Preview_01_sub`). The workhorse choice. |
| **Amcrest IP4M / IP5M WiFi** (~$60–90) | Excellent RTSP/ONVIF support, outdoor rated. |
| **TP-Link Tapo C121** (~$30) | 2K+ indoor/outdoor, IP66, color night vision, RTSP/ONVIF. Wired power + WiFi data. Verified pick — see the RTSP example in the config. |
| Reolink E1 Pro (~$40) | Cheap indoor pan/tilt with RTSP; good for testing the multi-camera path. |
| TP-Link Tapo C110/C120 (~$25–40) | Budget indoor; enable the RTSP "camera account" in the Tapo app. |
| Annke / Hikvision-based (various) | Fine if you prefer PoE — see note below. |

**Avoid for this project:** Ring, Nest, Arlo, Blink, and (ironically) actual
Flock hardware — they're cloud-locked and don't expose local streams, so
they can't feed your own AI.

### Practical tips

- **Use the sub-stream** for detection (e.g. 704×480 or 640×360 at 10–15fps).
  The Pi tracks on the sub-stream cheaply; the camera still records/serves
  its main stream if you ever want full-res.
- **WiFi cameras still need a power wire.** "Wireless" means wireless *data*.
  If you're running power anyway, consider PoE cameras + a $30 PoE switch —
  vastly more reliable outdoors, and the Pi 500's gigabit port is right there.
- Give cameras static IPs (DHCP reservations) so `rtsp://` URLs never break.
- Put cameras on your network but **block their internet access** in your
  router if you want true local-only operation — the Pi talks to them over
  the LAN regardless.

## Capacity on a Pi 500

The Pi 500 (same silicon as Pi 5, 4×A76 @ 2.4GHz, 8GB) comfortably handles:

- **Motion mode:** 6–10 sub-stream cameras.
- **DNN mode** (MobileNet-SSD every 3rd frame): 3–5 cameras.
- Later: the Hailo-8L AI HAT+ (on a Pi 5 node) or lowering `fps`/`dnn_interval`
  raises those ceilings. Second-house cameras cost the *remote* Pi, not this one.
