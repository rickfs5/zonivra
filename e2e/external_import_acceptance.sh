#!/bin/bash
# MG-EVIDENCE-10 isolated browser acceptance: EMPTY isolated storage (never <project>/runs), loopback server, headless Chrome, only the bundled SYNTHETIC fixture and files derived from it.
# No provider call, no model run, no camera. Output: logs/ext10/ . Usage: [MG_EXT10_PORT=8796] bash e2e/external_import_acceptance.sh
cd "$(dirname "$0")/.." || exit 1
PORT="${MG_EXT10_PORT:-8796}"; L=logs/ext10; mkdir -p $L
if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN > /dev/null 2>&1; then echo "port $PORT already in use; refusing"; exit 8; fi
export MG_RUN_OWNER="ext10-$$"
OUT=$(python3 scripts/mg_runs.py create) || exit 4; DIR=${OUT% *}; TOK=${OUT##* }
FIX=$(mktemp -d "${TMPDIR:-/tmp}/mg-ext10-fix.XXXXXX")
cleanup() { python3 scripts/mg_proc.py stop $L/server.record.json > /dev/null 2>&1; python3 scripts/mg_runs.py remove "$DIR" "$TOK" > $L/storage_remove.txt 2>&1; echo "isolated storage remove exit=$?"; rm -rf "$FIX"; }
trap cleanup EXIT
python3 - "$FIX" <<'PY'
import hashlib, json, sys, pathlib
fix = pathlib.Path(sys.argv[1]); d = pathlib.Path("backend/mineguard/demo_external"); stem = "SYNTHETIC_FIXTURE_sam31"
res, man, img = (d / f"{stem}.sse").read_bytes(), json.loads((d / f"{stem}.manifest.json").read_text()), (d / f"{stem}.png").read_bytes()
w = lambda n, b: (fix / n).write_bytes(b if isinstance(b, bytes) else json.dumps(b, sort_keys=True).encode())
w("result.sse", res); w("image.png", img); w("manifest.json", (d / f"{stem}.manifest.json").read_bytes())      # byte-identical to the bundled manifest, so the file form reproduces the demo import id
w("tampered.sse", res.replace(b"0.91", b"0.99"))
tr = res[:-1]; w("truncated.sse", tr); w("manifest_truncated.json", {**man, "response": {**man["response"], "sha256": hashlib.sha256(tr).hexdigest(), "bytes": len(tr)}})
w("manifest_badmodel.json", {**man, "model": "sam-3"})
real = {k: v for k, v in man.items() if k != "fixture_label"}; real.update(provenance_class="DECLARED_EXTERNAL_API_RESPONSE", request_body_sha256="cd" * 32); w("manifest_declared_real.json", real)
PY
echo "STORAGE_PRE_FILES $(find "$DIR" -type f | wc -l | tr -d ' ')"
export MG_RUNS_DIR=$DIR
MG_WRITE_TOKEN_FILE=1 python3 scripts/mg_proc.py start $L/server.record.json --log $L/server.log -- .venv/bin/python -W ignore -m uvicorn mineguard.app:app --app-dir backend --host 127.0.0.1 --port "$PORT" > /dev/null
for i in $(seq 1 40); do curl -sf -o /dev/null http://127.0.0.1:$PORT/api/health && break; sleep 1; done
node e2e/external_import_acceptance.mjs http://127.0.0.1:$PORT $L "$FIX" > $L/browser_run.log 2>&1; RC=$?; echo "browser exit=$RC"
echo "STORAGE_POST_FILES $(find "$DIR" -type f | wc -l | tr -d ' ')  external_results_dirs=$(ls "$DIR/external_results" 2>/dev/null | wc -l | tr -d ' ')"
python3 scripts/mg_proc.py stop $L/server.record.json; echo "server stop exit=$? listeners_on_port=$(lsof -nP -iTCP:$PORT -sTCP:LISTEN | wc -l | tr -d ' ')"
exit $RC
