#!/bin/sh
# Launch the VS-01 app: http://127.0.0.1:8000 (loopback only). Requires: .venv, frontend/dist, test-assets/*.mp4, models/rf-detr-nano.pth
cd "$(dirname "$0")/.." || exit 1
MG_WRITE_TOKEN_FILE=1 exec .venv/bin/python -W ignore -m uvicorn mineguard.app:app --app-dir backend --host 127.0.0.1 --port 8000
