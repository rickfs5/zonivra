#!/bin/sh
# Convenience test gate (NOT acceptance evidence; see scripts/acceptance_gate.sh). Writes exact commands and exit codes to logs/test_run.txt.
# The browser step runs ONLY against a server started by scripts/run_mvp_e2e.sh with isolated storage on a free port; it never attaches to an existing server
# (cycle-16 MG-PROC-016: an unowned live server on :8000 would receive test analyses, evidence tampering and session deletions).
cd "$(dirname "$0")/.." || exit 1
LOG=logs/test_run.txt; : > "$LOG"; FAIL=0
step() { name="$1"; shift; echo "\$ $*" >> "$LOG"; "$@" >> "$LOG" 2>&1; rc=$?; echo "[exit $rc] $name" | tee -a "$LOG"; [ $rc -ne 0 ] && FAIL=1; }
step "backend pytest" .venv/bin/python -m pytest -q backend/tests
step "frontend typecheck" sh -c 'cd frontend && npx tsc --noEmit'
step "frontend vitest" sh -c 'cd frontend && npx vitest run'
step "frontend build" sh -c 'cd frontend && npm run build'
PORT=$(python3 -c "import socket;s=socket.socket();s.bind(('127.0.0.1',0));print(s.getsockname()[1])")
step "browser acceptance, MP4 (isolated server, port $PORT)" env MG_E2E_PORT="$PORT" bash scripts/run_mvp_e2e.sh e2e/browser_acceptance.mjs
echo "OVERALL_EXIT=$FAIL" | tee -a "$LOG"; exit $FAIL
