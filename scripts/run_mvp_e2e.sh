#!/bin/bash
# Runs a browser acceptance script (default e2e/mvp_acceptance.mjs) against ONE server started with isolated storage and stopped by verified identity.
# Usage: scripts/run_mvp_e2e.sh [script]. Never touches <project>/runs; no process is stopped by name. An idempotent EXIT/INT/TERM trap stops the server THIS script
# started (verified identity record) and removes exactly the directory THIS script created (ownership token), preserving the original exit status.
cd "$(dirname "$0")/.." || exit 1
REC=logs/mvp_e2e/server.record.json; mkdir -p logs/mvp_e2e
RUNS_DIR_OWNED=""; RUNS_TOKEN=""; SERVER_STARTED=0
cleanup() {
  rc=$?
  trap - EXIT INT TERM
  if [ "$SERVER_STARTED" = 1 ] && [ -f "$REC" ]; then python3 scripts/mg_proc.py stop "$REC" > /dev/null 2>&1; SRC=$?; SERVER_STARTED=0; fi
  if [ -n "$RUNS_DIR_OWNED" ]; then python3 scripts/mg_runs.py remove "$RUNS_DIR_OWNED" "$RUNS_TOKEN" > /dev/null 2>&1; CRC=$?; RUNS_DIR_OWNED=""; fi
  [ -n "$FINAL_LINE" ] && echo "$FINAL_LINE"
  exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
PORT="${MG_E2E_PORT:-8000}"
# NEVER talk to a server this script did not start (a user's live server on the same port would receive the test's analyses, sessions and zone edits)
if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN > /dev/null 2>&1; then echo "port $PORT is already in use by another process; refusing to run (set MG_E2E_PORT to a free port)"; exit 8; fi
export MG_RUN_OWNER="mvp-e2e-$$-$(date +%s)"
[ -f logs/mvp_e2e/fake_camera.y4m ] || .venv/bin/python e2e/make_fake_camera.py logs/mvp_e2e/fake_camera.y4m 120 || exit 5   # BEFORE any storage or server exists
RUNS_OUT=$(python3 scripts/mg_runs.py create) || exit 4
RUNS_DIR_OWNED=${RUNS_OUT% *}; RUNS_TOKEN=${RUNS_OUT##* }; export MG_RUNS_DIR=$RUNS_DIR_OWNED
MG_WRITE_TOKEN_FILE=1 python3 scripts/mg_proc.py start "$REC" --log logs/mvp_e2e/server.log -- .venv/bin/python -W ignore -m uvicorn mineguard.app:app --app-dir backend --host 127.0.0.1 --port "$PORT" > /dev/null || exit 6
SERVER_STARTED=1
for i in $(seq 1 "${MG_E2E_HEALTH_WAIT:-60}"); do curl -sf -o /dev/null "http://127.0.0.1:$PORT/api/health" && break; sleep 1; done
curl -sf -o /dev/null "http://127.0.0.1:$PORT/api/health" || { echo "the server did not become healthy"; exit 7; }   # never run the browser suite against nothing
# the listener on the port must be the server recorded by THIS script (verified identity record)
python3 - "$REC" "$PORT" <<'PYEOF' || { echo "the listener on port $PORT is not the server started by this script"; exit 9; }
import json, subprocess, sys
pid = json.load(open(sys.argv[1]))["pid"]
out = subprocess.run(["lsof", "-nP", f"-iTCP:{sys.argv[2]}", "-sTCP:LISTEN", "-Fp"], capture_output=True, text=True).stdout
sys.exit(0 if f"p{pid}" in out.split() else 1)
PYEOF
MG_E2E_ISOLATED="$MG_RUN_OWNER" node "${1:-e2e/mvp_acceptance.mjs}" "http://127.0.0.1:$PORT"; RC=$?
python3 scripts/mg_proc.py stop "$REC"; SRC=$?; [ $SRC -eq 0 ] && SERVER_STARTED=0
sleep 1
python3 scripts/mg_runs.py remove "$RUNS_DIR_OWNED" "$RUNS_TOKEN"; CRC=$?; [ $CRC -eq 0 ] && RUNS_DIR_OWNED=""
FINAL_LINE="MVP_E2E_EXIT=$RC server_stop_rc=$SRC cleanup_rc=$CRC"
[ $RC -eq 0 ] && [ $SRC -eq 0 ] && [ $CRC -eq 0 ]
