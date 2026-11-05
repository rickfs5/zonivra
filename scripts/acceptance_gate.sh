#!/bin/bash
# Complete acceptance gate (RC-1S). Strictly sequential: one inference-using process at a time, never overlapping backend tests,
# browser E2E and the fresh-restart check. Requires a clean committed tree (the transcript is bound to HEAD).
#
# Output: logs/acceptance_runs/<UTC stamp>/
#   transcript.partial                 while running (never complete, never renamed on kill -9)
#   transcript.PASS.txt | .FAIL.txt    atomic rename after the final AGGREGATE line, plus <name>.sha256
#   transcript.INCOMPLETE.txt          interrupted or aborted (resource-critical, signal, early error): AGGREGATE=INCOMPLETE
#   NN_<step>.log                      full output of each step (sha256 recorded in the transcript)
# Exit status contract (MG-RC1-012): 0 ONLY for a complete AGGREGATE=PASS whose transcript and sha256 were written. AGGREGATE=FAIL exits 1;
# INCOMPLETE (abort, interruption, early error, evidence not writable) exits nonzero (the original code, 130 for a signal, or 3/4).
# Process control (MG-PROC-001): servers are started and stopped only through scripts/mg_proc.py, which signals a pid only after verifying its
# recorded identity (pid + start time + exact command + working directory + this run's owner token). No pkill/killall by name anywhere.
# Every run gets its own directory, so failed and interrupted attempts are preserved as separate records.
cd "$(dirname "$0")/.." || exit 1
ROOT=$(pwd)
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
DIR=logs/acceptance_runs/$STAMP
mkdir -p "$DIR" || exit 1
PART=$DIR/transcript.partial
: > "$PART"
PY=.venv/bin/python
FAIL=0; COMPLETE=0; N=0; ABORT="unexpected exit before the final step"; SERVER_PID=""
export MG_RUN_OWNER="$STAMP-$$-$(python3 -c 'import secrets; print(secrets.token_hex(8))')"   # ownership token inherited by every process this run starts
export MG_PID_DIR="$DIR"; SERVER_REC="$DIR/gate_server.record.json"
BACKEND_MODULES="test_schema_validation test_adapter_policy test_timebase_canon test_integration_real test_rc1_closeout test_rc1s_closeout test_cycle7_findings test_cycle8_findings test_risk_kernel test_risk_kernel_mutations test_vs02_webcam_risk test_vs02_replay_mp4 test_vs02_integrity test_privacy test_session_deletion test_night_review1 test_night_review2 test_night_review3 test_night_review4 test_night_review5 test_vertical2_wcam test_vertical2_feeds test_vertical2_alerts test_vertical2_dashboards test_vertical2_security test_vertical2_track test_gate_preconditions test_acceptance_counts test_cycle24_alerts test_cycle24_store test_cycle24_feed test_cycle24_wcam test_cycle25_findings test_vertical04_state test_vertical05_provenance test_doc_consistency test_risk_gap_closure test_oaf028_verdict test_overlap_history test_alert_delivery029 test_final_remediation031 test_product_vertical06 test_product_vertical07 test_costgov_base test_costgov_hardening test_unified_explain_unit test_unified_providers_explain test_meta_sam31 test_meta_pipeline test_meta_transport test_meta_provenance test_meta_containment test_muse_open test_meta_invariants"

say() { echo "$*" >> "$PART"; echo "$*"; }
mem_free() { memory_pressure | awk '/free percentage/{gsub("%","",$5); print $5}'; }
swap_used() { sysctl -n vm.swapusage | awk '{print $6}'; }
res() { say "RES $1 free=$(mem_free)% swap_used=$(swap_used)"; }

stop_server() {  # stops only the server this gate started, after identity verification; a failed verification signals nothing and fails the gate
  [ -f "$SERVER_REC" ] || return 0
  out=$(python3 scripts/mg_proc.py stop "$SERVER_REC" 2>&1); r=$?
  mv "$SERVER_REC" "$SERVER_REC.stopped" 2>/dev/null
  echo "  server stop: rc=$r $out" >> "$PART"
  [ "$r" -eq 0 ] || FAIL=1
  SERVER_PID=""
}

user_runs_digest() { (cd runs 2>/dev/null && find . -mindepth 1 -maxdepth 1 \( -name 'run_*' -o -name 'session_*' \) | sort | python3 -c "import hashlib,sys;l=sys.stdin.read();print(len(l.split()), hashlib.sha256(l.encode()).hexdigest()[:16])") || echo "0 none"; }
cleanup_runs() {  # MG-PROC-002: removes ONLY an isolated test-owned runs directory, after mg_runs.py proves ownership; a refusal fails the gate and deletes nothing
  [ -n "$1" ] || return 0
  out=$(python3 scripts/mg_runs.py remove "$1" "$2" 2>&1); r=$?
  echo "  isolated runs dir cleanup: rc=$r $out" >> "$PART"
  [ "$r" -eq 0 ] || FAIL=1
}
finalize() {
  rc=$?
  stop_server
  if [ -f "$MG_PID_DIR/fresh_server.json" ]; then   # an interrupted fresh-restart step may have left ITS server running: stop it by verified identity BEFORE its storage is removed (idempotent: a gone pid is success)
    out=$(python3 scripts/mg_proc.py stop "$MG_PID_DIR/fresh_server.json" 2>&1); r=$?
    echo "  fresh-restart server stop in finalize: rc=$r $out" >> "$PART"
    [ "$r" -eq 0 ] || FAIL=1
  fi
  if [ -f "$MG_PID_DIR/fresh_runs.owner" ]; then cleanup_runs $(cat "$MG_PID_DIR/fresh_runs.owner"); mv "$MG_PID_DIR/fresh_runs.owner" "$MG_PID_DIR/fresh_runs.owner.done"; fi
  cleanup_runs "$MG_RUNS_DIR" "$MG_RUNS_TOKEN"
  if [ -n "$MG_RUNS_DIR" ]; then echo "  user runs after: $(user_runs_digest) (before: $USER_RUNS_BEFORE)" >> "$PART"; [ "$(user_runs_digest)" = "$USER_RUNS_BEFORE" ] || FAIL=1; fi
  if [ "$COMPLETE" = 1 ]; then
    if [ "$FAIL" = 0 ]; then AGG=PASS; else AGG=FAIL; fi
    echo "AGGREGATE=$AGG" >> "$PART"
  else
    AGG=INCOMPLETE
    echo "ABORTED: $ABORT (shell exit code $rc, after step $N)" >> "$PART"
    echo "AGGREGATE=INCOMPLETE" >> "$PART"
  fi
  WROTE=1
  mv "$PART" "$DIR/transcript.$AGG.txt" || WROTE=0          # atomic: same filesystem
  (cd "$DIR" && shasum -a 256 "transcript.$AGG.txt" > "transcript.$AGG.sha256") || WROTE=0
  echo "transcript: $DIR/transcript.$AGG.txt"; cat "$DIR/transcript.$AGG.sha256"
  # exit status contract (MG-RC1-012)
  if [ "$WROTE" != 1 ]; then echo "evidence could not be written" >&2; exit 4; fi
  if [ "$AGG" = PASS ]; then exit 0; fi
  if [ "$AGG" = FAIL ]; then exit 1; fi
  if [ "$rc" -ne 0 ]; then exit "$rc"; fi
  exit 3
}
trap finalize EXIT
USER_RUNS_BEFORE=$(user_runs_digest)
RUNS_OUT=$(python3 scripts/mg_runs.py create) || { ABORT="cannot create the isolated runs directory"; exit 4; }
MG_RUNS_DIR=${RUNS_OUT% *}; MG_RUNS_TOKEN=${RUNS_OUT##* }; export MG_RUNS_DIR   # the server and the browser test store runs here, never in <project>/runs
trap 'ABORT="interrupted by signal"; exit 130' INT TERM

need_resources() {  # refuse to start an inference-using step under critical memory pressure
  f=$(mem_free)
  if [ "${f:-0}" -lt 25 ]; then ABORT="resource-critical before step '$1': free memory ${f}%"; exit 3; fi
}

# step <name> <heavy 0|1> <command...> : runs one command, records command, exit code and log hash. Sets LAST_RC and LAST_LOG.
step() {
  name=$1; heavy=$2; shift 2
  N=$((N + 1)); LAST_LOG=$(printf '%s/%02d_%s.log' "$DIR" "$N" "$name")
  [ "$heavy" = 1 ] && { need_resources "$name"; res "before $name"; }
  say "STEP $N $name"; say "  command: $*"
  start=$(date +%s)
  # the guard signals only the process it starts, so it is used for single processes (pytest); the E2E driver and the fresh-restart
  # script spawn their own children and rely on the pre-step memory check instead
  if [ "$heavy" = 1 ] && [ "$1" != "node" ] && [ "$1" != "scripts/fresh_restart_check.sh" ] && [ "$1" != "scripts/run_mvp_e2e.sh" ]; then
    guard=$(python3 scripts/guarded_run.py "$LAST_LOG" 6000 -- "$@"); LAST_RC=$(echo "$guard" | sed -n 's/.*GUARD rc=\([0-9]*\).*/\1/p'); say "  $guard"
  else
    "$@" > "$LAST_LOG" 2>&1; LAST_RC=$?
  fi
  say "  exit=$LAST_RC secs=$(( $(date +%s) - start )) log=$LAST_LOG sha256=$(shasum -a 256 "$LAST_LOG" | cut -c1-64)"
  [ "$heavy" = 1 ] && res "after $name"
  [ "${LAST_RC:-1}" -ne 0 ] && FAIL=1
  return 0
}

say "RC-1S ACCEPTANCE TRANSCRIPT (bound to the commit below; append-only, atomically finalized)"
say "started_utc=$(date -u +%FT%TZ)"
say "commit=$(git rev-parse HEAD)"
TREE=$( [ -z "$(git status --porcelain)" ] && echo yes || echo NO )
say "tree_clean=$TREE"
say "host=$(uname -srm) python=$($PY --version 2>&1) node=$(node --version) colima=$(colima list 2>/dev/null | tail -1 | awk '{print $2}')"
res "preflight"
if [ "$TREE" != yes ]; then ABORT="working tree is not clean: the transcript could not be bound to a commit"; exit 2; fi
if pgrep -f "uvicorn mineguard.app" > /dev/null; then ABORT="a MineGuard server is already running; refusing to overlap with it"; exit 2; fi

# ---------------- backend: inventory, then one process per module
say "== BACKEND INVENTORY (pytest --collect-only, per module)"
TOTAL_COLLECTED=0
for m in $BACKEND_MODULES; do
  c=$($PY -m pytest --collect-only -q "backend/tests/$m.py" -p no:cacheprovider 2>/dev/null | grep -c "::")
  say "INVENTORY $m collected=$c"; eval "COLLECTED_$m=$c"; TOTAL_COLLECTED=$((TOTAL_COLLECTED + c))
done
ALL=$($PY -m pytest --collect-only -q backend/tests -p no:cacheprovider 2>/dev/null | grep -c "::")
say "INVENTORY total_collected_by_modules=$TOTAL_COLLECTED total_collected_whole_suite=$ALL"
[ "$TOTAL_COLLECTED" = "$ALL" ] || { say "INVENTORY MISMATCH: a test file is not covered by the module list"; FAIL=1; }
say "== BACKEND (isolation: one pytest process per module, sequential; same inventory as one monolithic run)"
TOTAL_PASSED=0
for m in $BACKEND_MODULES; do
  step "backend_$m" 1 $PY -m pytest -q "backend/tests/$m.py" -p no:cacheprovider
  passed=$(grep -Eo '[0-9]+ passed' "$LAST_LOG" | tail -1 | awk '{print $1}'); passed=${passed:-0}
  failed=$(grep -Eo '[0-9]+ failed' "$LAST_LOG" | tail -1 | awk '{print $1}'); failed=${failed:-0}
  eval "want=\$COLLECTED_$m"
  say "  tests passed=$passed failed=$failed collected=$want"
  [ "$passed" = "$want" ] && [ "$failed" = 0 ] || FAIL=1
  TOTAL_PASSED=$((TOTAL_PASSED + passed))
done
say "BACKEND_TOTAL passed=$TOTAL_PASSED collected=$ALL"
[ "$TOTAL_PASSED" = "$ALL" ] || FAIL=1

# ---------------- frontend
say "== FRONTEND"
step frontend_typecheck 0 sh -c 'cd frontend && npx tsc --noEmit'
step frontend_vitest 0 sh -c 'cd frontend && npx vitest run'
vt=$(grep -E "Tests " "$LAST_LOG" | tail -1); say "  vitest: $vt"
echo "$vt" | grep -Eq "Tests +[0-9]+ passed \(([0-9]+)\)" && [ "$(echo "$vt" | grep -c failed)" = 0 ] || FAIL=1
step frontend_build 0 sh -c 'cd frontend && npm run build'

# ---------------- browser E2E: exactly one server, stopped afterwards
say "== BROWSER E2E (one server, started and stopped by this script)"
say "isolated runs dir: $MG_RUNS_DIR (the user's runs in runs/ are never touched; before: $USER_RUNS_BEFORE)"
need_resources server
SERVER_PID=$(MG_WRITE_TOKEN_FILE=1 python3 scripts/mg_proc.py start "$SERVER_REC" --log "$DIR/server.log" -- .venv/bin/python -W ignore -m uvicorn mineguard.app:app --app-dir backend --host 127.0.0.1 --port 8000 2>"$DIR/server.start.err") \
  || { say "server could not be started with a verified identity: $(cat "$DIR/server.start.err")"; FAIL=1; SERVER_PID=none; }
for i in $(seq 1 60); do curl -s -o /dev/null http://127.0.0.1:8000/api/health && break; sleep 1; done
say "server pid=$SERVER_PID health=$(curl -s http://127.0.0.1:8000/api/health)"
step browser_e2e 1 node e2e/browser_acceptance.mjs
say "  browser: $(grep -E 'checks passed' "$LAST_LOG" | tail -1)"
grep -Eq "^([0-9]+)/\1 checks passed" "$LAST_LOG" || FAIL=1
stop_server; sleep 2
if pgrep -f "uvicorn mineguard.app" > /dev/null; then say "WARNING: a MineGuard server is still running after the E2E step"; FAIL=1; fi

# ---------------- MVP browser E2E (VS-02F): webcam with Chrome's FAKE capture device (a TEST SOURCE), zones, risk states, synthetic replay, export.
# It starts and stops its own server (verified identity) with its own isolated storage; sequential after the previous server is gone.
say "== MVP BROWSER E2E (webcam test source, zones, risk, replay; scripts/run_mvp_e2e.sh)"
need_resources mvp_e2e
step mvp_browser_e2e 1 scripts/run_mvp_e2e.sh e2e/mvp_acceptance.mjs
say "  mvp browser: $(grep -E 'checks passed' "$LAST_LOG" | tail -1)"
grep -Eq "^([0-9]+)/\1 checks passed" "$LAST_LOG" || FAIL=1
grep -q "MVP_E2E_EXIT=0 server_stop_rc=0 cleanup_rc=0" "$LAST_LOG" || { say "  mvp e2e server stop or cleanup was not clean"; FAIL=1; }
grep -E "^MEASURED live stats" "$LAST_LOG" | sed 's/^/  | /' >> "$PART"
say "PHYSICAL_CAMERA_ACCEPTANCE=PENDING (the webcam suite above used Chrome's fake capture device fed from a prerecorded clip: a controlled test source, never a physical camera)"
if pgrep -f "uvicorn mineguard.app" > /dev/null; then say "WARNING: a MineGuard server is still running after the MVP E2E step"; FAIL=1; fi

# ---------------- fresh restart (creates its own venv and servers; sequential after the E2E server is gone)
say "== FRESH RESTART"
step fresh_restart 1 scripts/fresh_restart_check.sh
cp logs/fresh_restart.txt "$DIR/fresh_restart.txt"
say "  fresh_restart.txt sha256=$(shasum -a 256 logs/fresh_restart.txt | cut -c1-64)"
grep -E "^commit:|^tree_clean:|FRESH_RESTART_EXIT|passed in|^FRESH_" logs/fresh_restart.txt | sed 's/^/  | /' >> "$PART"
# MG-RC1-013: the fresh environment must have run the WHOLE suite (every module), with collected == passed == whole-suite total
fl=$(grep -E "^FRESH_TOTAL " logs/fresh_restart.txt | tail -1); say "  fresh inventory: ${fl:-<missing>}"
echo "$fl" | grep -Eq "^FRESH_TOTAL passed=([0-9]+) collected=\1 whole_suite=\1$" || { say "  fresh-restart inventory is missing or incomplete"; FAIL=1; }
grep -q "tree_clean: yes" logs/fresh_restart.txt && grep -q "FRESH_RESTART_EXIT=0" logs/fresh_restart.txt || FAIL=1
[ "$(grep -c "^commit: $(git rev-parse HEAD)" logs/fresh_restart.txt)" = 1 ] || { say "  fresh-restart transcript is not bound to this commit"; FAIL=1; }
if [ -f "$MG_PID_DIR/fresh_server.json" ]; then   # the restarted server left running by the fresh-restart check: stop it by verified identity only
  out=$(python3 scripts/mg_proc.py stop "$MG_PID_DIR/fresh_server.json" 2>&1); r=$?
  say "  fresh-restart server stop: rc=$r $out"; [ "$r" -eq 0 ] || FAIL=1
fi
sleep 1
if pgrep -f "uvicorn mineguard.app" > /dev/null; then say "WARNING: a MineGuard server is still running after the fresh-restart step (not terminated by name; see the stop result above)"; FAIL=1; fi

res "final"
say "finished_utc=$(date -u +%FT%TZ)"
COMPLETE=1
