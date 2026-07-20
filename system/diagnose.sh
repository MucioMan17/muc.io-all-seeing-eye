#!/bin/bash
# One-shot health report for support/debugging: run `sudo bash system/diagnose.sh`
# and read (or photograph) the output.

section() { echo; echo "===== $1 ====="; }

section "OS"
head -2 /etc/os-release
uname -m

section "engine service (allseeingeye)"
systemctl --no-pager --full status allseeingeye 2>&1 | head -12

section "kiosk service (allseeingeye-kiosk)"
systemctl --no-pager --full status allseeingeye-kiosk 2>&1 | head -12

section "engine log — last 20 lines this boot"
journalctl -b -u allseeingeye --no-pager 2>&1 | tail -20

section "kiosk log — last 20 lines this boot"
journalctl -b -u allseeingeye-kiosk --no-pager 2>&1 | tail -20

section "engine answering?"
if curl -fsS -m 3 http://127.0.0.1:8080/api/health; then
    echo " <- engine OK"
else
    echo "engine NOT answering on port 8080"
fi

section "binaries present"
for b in cage chromium-browser chromium python3; do
    printf "%-18s %s\n" "$b" "$(command -v "$b" || echo MISSING)"
done

section "display/GPU devices"
ls -l /dev/dri 2>/dev/null || echo "no /dev/dri (KMS driver not active?)"
