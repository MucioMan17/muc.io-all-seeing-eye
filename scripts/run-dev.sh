#!/bin/bash
# Run the engine locally for development — no install, no hardware needed.
# With no config it starts one synthetic demo camera at http://localhost:8080

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

if [[ ! -d .venv ]]; then
    python3 -m venv .venv
    .venv/bin/pip install -r requirements.txt -r requirements-dev.txt
fi
exec .venv/bin/python -m allseeingeye --config "${1:-config/dev.yml}"
