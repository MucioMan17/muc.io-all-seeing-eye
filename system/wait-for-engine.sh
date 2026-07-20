#!/bin/bash
# Block until the engine answers, so the kiosk browser never shows a
# connection error on a slow boot.
for i in $(seq 1 60); do
    if curl -fsS -o /dev/null http://127.0.0.1:8080/api/health; then
        exit 0
    fi
    sleep 1
done
# Start the browser anyway; it will retry via the UI.
exit 0
