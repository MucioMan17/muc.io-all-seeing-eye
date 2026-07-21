#!/bin/bash
# All-Seeing Eye installer.
#
# Turns a fresh Raspberry Pi OS Lite (64-bit, Bookworm or newer) install into
# a camera appliance: engine on boot + fullscreen kiosk console on the
# attached display. Run from a checkout of this repo:
#
#   sudo bash system/install.sh
#
# Re-running is safe; it updates code and restarts services. Your config at
# /etc/allseeingeye/config.yml is never overwritten.

set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "run as root: sudo bash system/install.sh" >&2
    exit 1
fi

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_DIR=/opt/allseeingeye
DATA_DIR=/var/lib/allseeingeye
CONF_DIR=/etc/allseeingeye

echo "==> Installing packages"
apt-get update
apt-get install -y --no-install-recommends \
    python3 python3-venv python3-opencv python3-numpy \
    ffmpeg cage curl rsync \
    fonts-dejavu-core
# Browser package name differs across Raspberry Pi OS releases.
apt-get install -y --no-install-recommends chromium-browser \
    || apt-get install -y --no-install-recommends chromium

echo "==> Creating service user + directories"
id -u ase &>/dev/null || useradd --system --create-home --home-dir /home/ase \
    --groups video,render,input --shell /usr/sbin/nologin ase
mkdir -p "$APP_DIR" "$APP_DIR/bin" "$DATA_DIR/recordings" "$DATA_DIR/models" "$CONF_DIR"
chown -R ase:ase "$DATA_DIR"

echo "==> Installing application to $APP_DIR"
rsync -a --delete "$REPO_DIR/allseeingeye" "$REPO_DIR/web" "$APP_DIR/"
install -m 0755 "$REPO_DIR/system/wait-for-engine.sh" "$APP_DIR/bin/wait-for-engine.sh"
install -m 0755 "$REPO_DIR/system/kiosk-launch.sh" "$APP_DIR/bin/kiosk-launch.sh"
install -m 0755 "$REPO_DIR/system/update.sh" "$APP_DIR/bin/update.sh"

echo "==> Python environment"
# --system-site-packages picks up the apt-built OpenCV/NumPy (fast ARM builds
# from Raspberry Pi OS) instead of compiling wheels for hours.
if [[ ! -d "$APP_DIR/venv" ]]; then
    python3 -m venv --system-site-packages "$APP_DIR/venv"
fi
"$APP_DIR/venv/bin/pip" install --upgrade pip -q
"$APP_DIR/venv/bin/pip" install -q -r "$REPO_DIR/requirements.txt"

echo "==> Config"
if [[ ! -f "$CONF_DIR/config.yml" ]]; then
    install -m 0644 "$REPO_DIR/config/config.example.yml" "$CONF_DIR/config.yml"
    echo "    wrote default config to $CONF_DIR/config.yml — edit it for your cameras"
else
    echo "    keeping existing $CONF_DIR/config.yml"
fi

echo "==> systemd services"
install -m 0644 "$REPO_DIR/system/allseeingeye.service" /etc/systemd/system/
install -m 0644 "$REPO_DIR/system/allseeingeye-kiosk.service" /etc/systemd/system/
install -m 0644 "$REPO_DIR/system/allseeingeye-update.service" /etc/systemd/system/
install -m 0644 "$REPO_DIR/system/allseeingeye-update.path" /etc/systemd/system/
install -m 0644 "$REPO_DIR/system/allseeingeye-addcamera.service" /etc/systemd/system/
install -m 0644 "$REPO_DIR/system/allseeingeye-addcamera.path" /etc/systemd/system/
# Retire the old periodic auto-update timer in favor of on-demand updates.
systemctl disable --now allseeingeye-update.timer 2>/dev/null || true
rm -f /etc/systemd/system/allseeingeye-update.timer
systemctl daemon-reload
systemctl enable allseeingeye.service

echo "==> On-demand updates (UPDATE button / U key in the UI)"
BRANCH=$(git -C "$REPO_DIR" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")
if [[ -n "$BRANCH" && "$BRANCH" != "HEAD" ]]; then
    cat > "$CONF_DIR/update.conf" <<EOF
REPO_DIR=$REPO_DIR
BRANCH=$BRANCH
EOF
    # The updater runs as root against a repo usually owned by another
    # user; git requires the path be marked safe in the system config.
    if ! git config --system --get-all safe.directory 2>/dev/null | grep -qx "$REPO_DIR"; then
        git config --system --add safe.directory "$REPO_DIR"
    fi
    git -C "$REPO_DIR" rev-parse --short HEAD > "$APP_DIR/BUILD" 2>/dev/null || echo unknown > "$APP_DIR/BUILD"
    systemctl enable --now allseeingeye-update.path
    echo "    tracking origin/$BRANCH, updates run when requested from the UI"
else
    echo "    not a git checkout — UI updates disabled"
fi

# Add-camera requests from the UI (works regardless of git state).
systemctl enable --now allseeingeye-addcamera.path

if [[ -e /dev/tty1 ]]; then
    # The kiosk owns tty1; getty must not fight it for the terminal.
    systemctl disable --now getty@tty1.service 2>/dev/null || true
    systemctl enable allseeingeye-kiosk.service
    systemctl set-default graphical.target
fi

echo "==> Starting engine"
systemctl restart allseeingeye.service
systemctl restart allseeingeye-kiosk.service 2>/dev/null || true

IP=$(hostname -I 2>/dev/null | awk '{print $1}')
cat <<EOF

All-Seeing Eye installed.

  Console:   attached display boots straight into the camera UI
  Browser:   http://${IP:-<pi-address>}:8080  (any device on your network)
  Config:    $CONF_DIR/config.yml   (then: sudo systemctl restart allseeingeye)
  Logs:      journalctl -u allseeingeye -f
  AI labels: set 'detect: { mode: dnn }' on a camera and restart — the
             model (~24 MB) downloads itself on first use

EOF
