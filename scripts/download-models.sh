#!/bin/bash
# Fetch the MobileNet-SSD model used for labeled object detection
# (person / car / dog / ...). Optional — the system runs fine on pure
# motion detection without it. After downloading, set `detect.mode: dnn`
# for your cameras and restart the engine.

set -euo pipefail

MODELS_DIR="${1:-/opt/allseeingeye/models}"
BASE="https://raw.githubusercontent.com/chuanqi305/MobileNet-SSD/master"

mkdir -p "$MODELS_DIR"
echo "Downloading MobileNet-SSD into $MODELS_DIR"
curl -fL -o "$MODELS_DIR/MobileNetSSD_deploy.prototxt" \
    "$BASE/deploy.prototxt"
curl -fL -o "$MODELS_DIR/MobileNetSSD_deploy.caffemodel" \
    "$BASE/mobilenet_iter_73000.caffemodel"

echo "Done. Set 'detect: { mode: dnn }' on your cameras in /etc/allseeingeye/config.yml"
echo "then: sudo systemctl restart allseeingeye"
