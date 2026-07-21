#!/bin/bash
# Launch the All-Seeing Eye console fullscreen inside the Raspberry Pi Desktop
# session. Run from the desktop user's XDG autostart so it lives in the
# session's Wayland output — which is what Raspberry Pi Connect screen-shares,
# giving remote access to the live console from anywhere.

# Wait for the engine to answer so the browser never opens on an error page.
/opt/allseeingeye/bin/wait-for-engine.sh || true

CHROMIUM=""
for bin in chromium-browser chromium; do
    if command -v "$bin" >/dev/null 2>&1; then
        CHROMIUM="$bin"
        break
    fi
done
if [[ -z "$CHROMIUM" ]]; then
    echo "kiosk-desktop: no chromium/chromium-browser binary found" >&2
    exit 1
fi

# No --ozone-platform override: let Chromium pick the backend the desktop
# provides (Wayland on Bookworm), so this works whether the session is
# Wayland or X. Connect captures the compositor output either way.
exec "$CHROMIUM" \
    --kiosk http://127.0.0.1:8080 \
    --start-fullscreen \
    --noerrdialogs --disable-infobars --disable-session-crashed-bubble \
    --disable-features=Translate \
    --autoplay-policy=no-user-gesture-required \
    --check-for-update-interval=31536000
