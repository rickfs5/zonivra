#!/bin/sh
# Fresh-restart reproducibility transcript (criterion 11). Writes logs/fresh_restart.txt, bound to the current git commit.
# Rebuilds the Python env from the freeze (--no-deps), rebuilds the frontend, starts a FRESH server, runs two analyses,
# verifies, restarts the server, re-verifies the first run, then runs the Chrome acceptance. Run on a clean tree.
cd "$(dirname "$0")/.." || exit 1
LOG=logs/fresh_restart.txt; : > "$LOG"; FAIL=0
VENV=$(mktemp -d)/venv
say() { echo "$*" | tee -a "$LOG"; }
step() { name="$1"; shift; echo "\$ $*" >> "$LOG"; "$@" >> "$LOG" 2>&1; rc=$?; say "[exit $rc] $name"; [ $rc -ne 0 ] && FAIL=1; return $rc; }
say "commit: $(git rev-parse HEAD)"; say "tree_clean: $( [ -z "$(git status --porcelain)" ] && echo yes || echo NO )"; say "started: $(date -u +%FT%TZ)"
# MG-PROC-001: no process is ever terminated by name. A MineGuard server that this check did not start is not ours to stop: refuse to run.
if pgrep -f "uvicorn mineguard.app" > /dev/null; then say "[FAIL] a MineGuard server is already running and was not started by this check; refusing to run (it is never terminated by name)"; echo "FRESH_RESTART_EXIT=1" | tee -a "$LOG"; exit 2; fi
: "${MG_RUN_OWNER:=standalone-$$-$(date +%s)}"; export MG_RUN_OWNER   # ownership token: inherited by the server this script starts
PIDF="${MG_PID_DIR:-logs}/fresh_server.json"
# Abnormal end (interrupt, signal, early exit): stop the server THIS script started, by verified identity (a normal end leaves server #2 for the caller to stop; MG-GATE-014).
FRESH_NORMAL=0
fresh_cleanup() { [ "$FRESH_NORMAL" = 1 ] && return; [ -f "$PIDF" ] && python3 scripts/mg_proc.py stop "$PIDF" > /dev/null 2>&1; }
trap fresh_cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
# MG-PROC-002: isolated storage. This check never deletes runs in <project>/runs; its server and browser test use a fresh test-owned directory,
# whose owner record is left for the caller (the gate removes it after stopping the server; standalone use: scripts/mg_runs.py remove <dir> <token>).
RUNS_OUT=$(python3 scripts/mg_runs.py create) || { say "[FAIL] cannot create the isolated runs directory"; echo "FRESH_RESTART_EXIT=1" | tee -a "$LOG"; exit 2; }
MG_RUNS_DIR=${RUNS_OUT% *}; export MG_RUNS_DIR
( umask 077; echo "$RUNS_OUT" > "${MG_PID_DIR:-logs}/fresh_runs.owner" )
say "isolated runs dir: $MG_RUNS_DIR"
step "create venv from freeze (--no-deps)" sh -c "uv venv --python 3.13 '$VENV' -q && uv pip install --no-deps --python '$VENV/bin/python' -r backend/requirements.freeze.txt -q"
step "prohibited packages absent" "$VENV/bin/python" -c "import importlib.util as u,sys;sys.exit(any(u.find_spec(m) for m in ('av','cv2','ultralytics','rfdetr_plus')))"
# One pytest process per module, sequentially (keeps peak memory at one model instance), over EVERY module in backend/tests (MG-RC1-013):
# the module list comes from the directory, and the per-module totals must equal the whole-suite collection, so no module can be omitted silently.
MODS=$(cd backend/tests && ls test_*.py | sed 's/\.py$//')
OUT=$(mktemp); TC=0; TP=0
for m in $MODS; do
  c=$("$VENV/bin/python" -m pytest --collect-only -q "backend/tests/$m.py" -p no:cacheprovider 2>/dev/null | grep -c "::")
  say "FRESH_INVENTORY $m collected=$c"
  echo "\$ pytest -q backend/tests/$m.py (fresh venv)" >> "$LOG"
  "$VENV/bin/python" -m pytest -q "backend/tests/$m.py" -p no:cacheprovider > "$OUT" 2>&1; rc=$?; cat "$OUT" >> "$LOG"
  p=$(grep -Eo '[0-9]+ passed' "$OUT" | tail -1 | awk '{print $1}'); p=${p:-0}
  f=$(grep -Eo '[0-9]+ failed' "$OUT" | tail -1 | awk '{print $1}'); f=${f:-0}
  say "[exit $rc] backend pytest in fresh venv: $m"
  say "FRESH_TESTS $m passed=$p failed=$f collected=$c"
  [ $rc -ne 0 ] && FAIL=1
  { [ "$p" = "$c" ] && [ "$f" = 0 ]; } || FAIL=1
  TC=$((TC + c)); TP=$((TP + p))
done
ALL=$("$VENV/bin/python" -m pytest --collect-only -q backend/tests -p no:cacheprovider 2>/dev/null | grep -c "::")
say "FRESH_TOTAL passed=$TP collected=$TC whole_suite=$ALL"
{ [ "$TP" = "$TC" ] && [ "$TC" = "$ALL" ] && [ "$ALL" -gt 0 ]; } || { say "[FAIL] fresh-environment test inventory is incomplete"; FAIL=1; }
rm -f "$OUT"
step "frontend npm ci + build" sh -c "cd frontend && npm ci --ignore-scripts --no-audit --no-fund && npm run build"
start_server() { MG_WRITE_TOKEN_FILE=1 python3 scripts/mg_proc.py start "$PIDF" --log logs/server.log -- "$VENV/bin/python" -W ignore -m uvicorn mineguard.app:app --app-dir backend --host 127.0.0.1 --port 8000 > /dev/null || return 1; for i in $(seq 1 60); do curl -s -o /dev/null http://127.0.0.1:8000/api/health && return 0; sleep 1; done; return 1; }
stop_server_verified() { out=$(python3 scripts/mg_proc.py stop "$PIDF" 2>&1); r=$?; say "server stop by verified identity: rc=$r $out"; [ $r -eq 0 ] || FAIL=1; }
TOK() { cat logs/session_token; }  # per-process session token written by the server (mode 0600)
J() { "$VENV/bin/python" -I -c "import sys,json;d=json.load(sys.stdin);print(d$1)"; }
analyze() {
  vid=$(curl -s -H "X-MG-Token: $(TOK)" http://127.0.0.1:8000/api/videos | J "[0]['video_id']")
  job=$(curl -s -X POST -H "X-MG-Token: $(TOK)" -H 'Content-Type: application/json' -d '{"stride":3,"threshold":0.3}' "http://127.0.0.1:8000/api/videos/$vid/analyze" | J "['job_id']")
  for i in $(seq 1 120); do st=$(curl -s -H "X-MG-Token: $(TOK)" "http://127.0.0.1:8000/api/jobs/$job" | J "['status']"); [ "$st" = done ] && break; [ "$st" = failed ] && return 1; sleep 2; done
  curl -s -H "X-MG-Token: $(TOK)" "http://127.0.0.1:8000/api/jobs/$job" | J "['run_id']"
}
step "start fresh server #1" start_server
A=$(analyze); say "analysis 1 run_id: $A"
B=$(analyze); say "analysis 2 run_id: $B"
[ -n "$A" ] && [ "$A" = "$B" ] && say "[ok] two separate analyses produced the identical run_id" || { say "[FAIL] run ids differ or empty"; FAIL=1; }
V1=$(curl -s -H "X-MG-Token: $(TOK)" "http://127.0.0.1:8000/api/runs/$A/verify" | J "['ok']"); say "verify #1 (deep): $V1"; [ "$V1" = True ] || FAIL=1
stop_server_verified; sleep 2
step "restart server #2" start_server
# the browser runs first: right after the restart the on-disk run must be shown as UNVERIFIED, then the UI verifies it
step "browser acceptance against restarted server" node e2e/browser_acceptance.mjs
step "run on disk was UNVERIFIED when the restarted server was first loaded in the browser" grep -q "API trust=unverified" logs/acceptance/results.json
V2=$(curl -s -H "X-MG-Token: $(TOK)" "http://127.0.0.1:8000/api/runs/$A/verify" | J "['ok']"); say "verify after restart (deep): $V2"; [ "$V2" = True ] || FAIL=1
say "finished: $(date -u +%FT%TZ)"; say "FRESH_RESTART_EXIT=$FAIL"
FRESH_NORMAL=1
exit $FAIL
