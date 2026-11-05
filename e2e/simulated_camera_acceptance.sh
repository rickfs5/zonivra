#!/bin/bash
# FINAL-ENGINEERING-15-R1 isolated browser acceptance: loopback server (detector factory fails once, then a test double that sees nothing), headless Chrome with a CANVAS-BASED FAKE camera
# (SIMULATED_DEVICE_TEST: no physical camera, no model, no provider, no network). Output: logs/cam15/. Usage: [MG_CAM15_PORT=8799] bash e2e/simulated_camera_acceptance.sh
cd "$(dirname "$0")/.." || exit 1
PORT="${MG_CAM15_PORT:-8799}"; L=logs/cam15; mkdir -p $L
if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN > /dev/null 2>&1; then echo "port $PORT already in use; refusing"; exit 8; fi
export MG_RUN_OWNER="cam15-$$"
python3 scripts/mg_proc.py start $L/server.record.json --log $L/server.log -- .venv/bin/python -W ignore e2e/serve_no_model.py "$PORT" 1 > /dev/null 2>&1
trap 'python3 scripts/mg_proc.py stop $L/server.record.json > /dev/null 2>&1' EXIT
for i in $(seq 1 60); do curl -sf -o /dev/null http://127.0.0.1:$PORT/api/health && break; sleep 1; done
node e2e/simulated_camera_acceptance.mjs http://127.0.0.1:$PORT $L > $L/browser_run.log 2>&1; RC=$?; echo "browser exit=$RC"
python3 scripts/mg_proc.py stop $L/server.record.json; echo "server stop exit=$? listeners_on_port=$(lsof -nP -iTCP:$PORT -sTCP:LISTEN | wc -l | tr -d ' ')"
trap - EXIT
exit $RC
