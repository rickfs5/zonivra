#!/bin/bash
# MG-PRODUCT-VERTICAL-07 local demonstration: ONE server with ISOLATED, test-owned storage on a free port, kept running until Ctrl-C, then stopped by verified identity and its storage removed.
# Usage: MG_DEMO_PORT=8780 bash scripts/demo_v07.sh   (then open the printed URL; walkthrough: the MG PRODUCT VERTICAL 07 operator experience document, section 3)
# It never touches <project>/runs or any owner session; it never starts a camera; no process is stopped by name. The guided, automated version of the same journey is e2e/product07_acceptance.mjs.
cd "$(dirname "$0")/.." || exit 1
REC=logs/demo_v07/server.record.json; mkdir -p logs/demo_v07
RUNS_DIR_OWNED=""; RUNS_TOKEN=""; SERVER_STARTED=0
cleanup() {
  rc=$?
  trap - EXIT INT TERM
  if [ "$SERVER_STARTED" = 1 ] && [ -f "$REC" ]; then python3 scripts/mg_proc.py stop "$REC" > /dev/null 2>&1; fi
  if [ -n "$RUNS_DIR_OWNED" ]; then python3 scripts/mg_runs.py remove "$RUNS_DIR_OWNED" "$RUNS_TOKEN" > /dev/null 2>&1; fi
  echo "demo server stopped and its isolated storage removed"
  exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
PORT="${MG_DEMO_PORT:-8780}"
if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN > /dev/null 2>&1; then echo "port $PORT is already in use by another process; refusing (set MG_DEMO_PORT to a free port)"; exit 8; fi
export MG_RUN_OWNER="demo-v07-$$-$(date +%s)"
RUNS_OUT=$(python3 scripts/mg_runs.py create) || exit 4
RUNS_DIR_OWNED=${RUNS_OUT% *}; RUNS_TOKEN=${RUNS_OUT##* }; export MG_RUNS_DIR=$RUNS_DIR_OWNED
MG_WRITE_TOKEN_FILE=1 python3 scripts/mg_proc.py start "$REC" --log logs/demo_v07/server.log -- .venv/bin/python -W ignore -m uvicorn mineguard.app:app --app-dir backend --host 127.0.0.1 --port "$PORT" > /dev/null || exit 6
SERVER_STARTED=1
for i in $(seq 1 "${MG_E2E_HEALTH_WAIT:-60}"); do curl -sf -o /dev/null "http://127.0.0.1:$PORT/api/health" && break; sleep 1; done
curl -sf -o /dev/null "http://127.0.0.1:$PORT/api/health" || { echo "the server did not become healthy"; exit 7; }
python3 - "$REC" "$PORT" <<'PYEOF' || { echo "the listener on port $PORT is not the server started by this script"; exit 9; }
import json, subprocess, sys
pid = json.load(open(sys.argv[1]))["pid"]
out = subprocess.run(["lsof", "-nP", f"-iTCP:{sys.argv[2]}", "-sTCP:LISTEN", "-Fp"], capture_output=True, text=True).stdout
sys.exit(0 if f"p{pid}" in out.split() else 1)
PYEOF
echo "MineGuard demo ready: http://127.0.0.1:$PORT  (isolated storage; Ctrl-C stops it). Use the registered nonpersonal MP4 only; do NOT start the personal camera."
while sleep 2; do curl -sf -o /dev/null "http://127.0.0.1:$PORT/api/health" || break; done
