# Building a flashable All-Seeing Eye OS image

The installer auto-detects your OS and sets up the right kind of kiosk:

- **Raspberry Pi OS Desktop (64-bit)** → runs the console inside the desktop
  session and enables **Raspberry Pi Connect**, so you can view the Pi from
  anywhere at connect.raspberrypi.com. **Choose this for remote access / the
  two-house setup.** (Screen sharing needs Desktop; it does not work on Lite.)
- **Raspberry Pi OS Lite (64-bit)** → runs a lightweight bare kiosk. Great for
  a local-only appliance, but no Connect screen sharing.

Override auto-detection with `ASE_KIOSK=desktop` or `ASE_KIOSK=cage` if needed.

## Option A — installer over Raspberry Pi OS (recommended to start)

1. Flash **Raspberry Pi OS (64-bit) — the full Desktop version** with
   Raspberry Pi Imager (pick "Raspberry Pi OS (64-bit)", not Lite, for remote
   access). In the Imager's settings set hostname, username/password, and WiFi.
2. Boot the Pi 500. On the desktop, open a terminal (or SSH in) and run:

   ```bash
   git clone https://github.com/MucioMan17/muc.io-all-seeing-eye.git
   cd muc.io-all-seeing-eye
   sudo bash system/install.sh
   ```

3. Enable remote access once: `rpi-connect signin`, open the printed link, and
   log in with your Raspberry Pi ID.
4. Reboot. The display boots into the fullscreen console automatically, and the
   Pi is reachable from anywhere at https://connect.raspberrypi.com.

The installer is idempotent — re-run it after `git pull` to upgrade. For a
headless Pi (no monitor), run the install with `ASE_HEADLESS=1 sudo -E bash
system/install.sh` so Connect still has a screen to share.

## Option B — build a real .img with pi-gen

For "flash card → done" provisioning of additional Pis (e.g. the node at the
second house), build a custom image with Raspberry Pi's official
[pi-gen](https://github.com/RPi-Distro/pi-gen) tool and the stage in this
directory:

```bash
git clone https://github.com/RPi-Distro/pi-gen.git
cd pi-gen

# Add our stage on top of the lite stages.
cp -r ../muc.io-all-seeing-eye/os/pi-gen/stage-ase .
cat > config <<'EOF'
IMG_NAME=allseeingeye
DEPLOY_COMPRESSION=xz
STAGE_LIST="stage0 stage1 stage2 stage-ase"
ENABLE_SSH=1
EOF

# Needs Docker (or a Debian host); takes a while.
./build-docker.sh
```

The finished image in `deploy/` boots any Pi straight into All-Seeing Eye.
Per-device settings (WiFi country/credentials, hostname) can still be applied
at flash time with Raspberry Pi Imager's customization screen.
