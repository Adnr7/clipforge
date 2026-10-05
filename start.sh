#!/bin/bash
# ClipForge local web launcher; build the frontend first (npm install && npm run build).
set -euo pipefail
cd "$(dirname "$0")"

if [ -z "${VIRTUAL_ENV:-}" ]; then
  source .venv/bin/activate
fi
exec python main.py --web "$@"
