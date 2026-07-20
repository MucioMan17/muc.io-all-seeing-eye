#!/bin/bash
# pi-gen chroot step: bake All-Seeing Eye into the image.
set -euo pipefail

git clone --depth 1 https://github.com/MucioMan17/muc.io-all-seeing-eye.git /tmp/ase
bash /tmp/ase/system/install.sh
rm -rf /tmp/ase

# First boot on real hardware re-checks services; nothing else to do here.
