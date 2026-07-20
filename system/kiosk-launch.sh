#!/bin/bash
# Kiosk launcher: cage (minimal Wayland compositor) running Chromium
# fullscreen on the local console UI. Kept as a script so the systemd unit
# survives OS differences (chromium-browser vs chromium binary name).

CHROMIUM=""
for bin in chromium-browser chromium; do
    if command -v "$bin" >/dev/null 2>&1; then
        CHROMIUM="$bin"
        break
    fi
done
if [[ -z "$CHROMIUM" ]]; then
    echo "kiosk: no chromium/chromium-browser binary found" >&2
    exit 1
fi

exec /usr/bin/cage -d -s -- "$CHROMIUM" \
    --kiosk http://127.0.0.1:8080 \
    --ozone-platform=wayland \
    --noerrdialogs --disable-infobars --disable-session-crashed-bubble \
    --autoplay-policy=no-user-gesture-required \
    --check-for-update-interval=31536000
