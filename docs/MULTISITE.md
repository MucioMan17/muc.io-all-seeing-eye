# Two houses, one system

Design: **each house runs its own All-Seeing Eye node** (a Pi + its local
cameras), and the sites are joined over an encrypted VPN so either UI can pull
up the other house live. Detection, recording, and storage stay local to each
house — the VPN only carries the viewing traffic you ask for, and nothing is
exposed to the public internet.

```
 House A (Pi 500)                      House B (any Pi 4/5)
 ┌─────────────────────┐               ┌─────────────────────┐
 │ All-Seeing Eye node │◀── VPN ─────▶│ All-Seeing Eye node │
 │ + local cameras     │  (Tailscale/ │ + local cameras     │
 │ + kiosk display     │   WireGuard) │ (headless is fine)  │
 └─────────────────────┘               └─────────────────────┘
        ▲ 
   your phone/laptop (also on the VPN) can view either site from anywhere
```

## Step 1 — VPN between the sites

**Tailscale (recommended — zero config, free tier is plenty):**

```bash
# on each Pi:
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```

Each Pi gets a stable name (e.g. `mainhouse-pi`, `lakehouse-pi`) reachable
from every device on your tailnet, through NAT, with no router port
forwarding. Install the Tailscale app on your phone and you can open either
site's UI from anywhere. Prefer self-hosted? Plain WireGuard between the two
routers works identically — the nodes just need to reach each other's port
8080.

**Never port-forward 8080 to the internet.** The UI currently has no
authentication; the VPN *is* the security boundary.

## Step 2 — link the sites in the UI

On the main house Pi, `/etc/allseeingeye/config.yml`:

```yaml
site_name: "Main House"
remote_sites:
  - name: "Lake House"
    url: "http://lakehouse-pi:8080"
```

And the mirror on the lake house Pi. Restart the engines. Each site's header
now shows a button that opens the other site's full live console.

## Alternative — one Pi pulls remote cameras directly

For a "second house" that's just 1–2 cameras and no Pi, you can point the
main Pi at remote RTSP URLs over the VPN:

```yaml
  - id: lake-drive
    name: "Lake House Driveway"
    source: "rtsp://user:pass@lakehouse-cam:554/h264Preview_01_sub"
    fps: 8
```

Works, but detection now rides on the inter-house link (~1–2 Mbit/s per
sub-stream, constantly) and dies with the WAN. A $60 Pi Zero 2/Pi 4 running a
node at the second house is the better architecture — that's why the whole
OS installs from one script (or flashes from one image, see `os/README.md`).
