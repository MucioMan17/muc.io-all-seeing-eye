# Building a flashable All-Seeing Eye OS image

Two ways to get a Pi that boots straight into the camera console:

## Option A — installer over Raspberry Pi OS Lite (recommended to start)

1. Flash **Raspberry Pi OS Lite (64-bit)** with Raspberry Pi Imager.
   In the Imager's settings, set hostname, enable SSH, and configure WiFi.
2. Boot the Pi 500, SSH in, then:

   ```bash
   git clone https://github.com/MucioMan17/muc.io-all-seeing-eye.git
   cd muc.io-all-seeing-eye
   sudo bash system/install.sh
   ```

3. Reboot. The attached display now boots directly into the fullscreen
   camera console; the engine and web UI start automatically on every boot.

The installer is idempotent — re-run it after `git pull` to upgrade.

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
