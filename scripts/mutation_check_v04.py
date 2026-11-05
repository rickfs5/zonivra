#!/usr/bin/env python3
"""Mutation check for the cycle-25 corrections and the V04 state publication (stdlib). Each mutation is applied to a COPY of the sources (the working tree is never touched); the
selected tests must FAIL (pytest exit 1 / vitest failed tests, not a collection or import error) and a clean unmutated copy must PASS first.
Usage: scripts/mutation_check_v04.py <result.json>   (heavy tests are not used; every selected test uses the fake detector)"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = str(ROOT / ".venv" / "bin" / "python")
BE = [
    ("MG-ALERT-024 merge capacity guard removed", "backend/mineguard/alerts.py", 'and len(self.alerts[last[0]]["occurrences"]) < MAX_OCCURRENCES):', "):", ["backend/tests/test_cycle25_findings.py", "-k", "attributable or unknown_diagnostics"]),
    ("MG-STATE-025 current_risk returns the last state", "backend/mineguard/feeds.py", 'return last if feed_state == "playing" else "UNKNOWN"', "return last", ["backend/tests/test_cycle25_findings.py", "backend/tests/test_vertical04_state.py", "-k", "inactive or no_non_playing"]),
    ("MG-STATE-025 per-zone current state ignores the lifecycle reason", "backend/mineguard/feeds.py", '"state": ("UNKNOWN" if reason else current_risk(self.state, s.get("state", "UNKNOWN")))', '"state": s.get("state", "UNKNOWN")', ["backend/tests/test_vertical04_state.py", "-k", "lifecycle_matrix or failed_feed"]),
    ("V04 stale playing feed keeps a current state", "backend/mineguard/feeds.py", "if age_s is not None and age_s > STALE_OBSERVATION_S:", "if False:", ["backend/tests/test_vertical04_state.py", "-k", "stale"]),
    ("MG-TREND-025 severity series counts simulations", "backend/mineguard/monitor.py", "if not sim and not restored:", "if True:", ["backend/tests/test_cycle25_findings.py", "-k", "trend"]),
    ("MG-API-025 unknown members ignored", "backend/mineguard/api_vs02.py", 'model_config = ConfigDict(extra="forbid")', 'model_config = ConfigDict(extra="ignore")', ["backend/tests/test_cycle25_findings.py", "-k", "unknown_members"]),
    ("MG-API-025 threshold bounds and finiteness removed (allow_inf_nan alone is an equivalent mutant: the range check also rejects NaN)", "backend/mineguard/api_vs02.py", "Field(default=0.30, ge=-10.0, le=10.0, allow_inf_nan=False)", "Field(default=0.30)", ["backend/tests/test_cycle25_findings.py", "-k", "non_finite"]),
    ("MG-API-025 body limit disabled", "backend/mineguard/security.py", "limit: int = 65_536", "limit: int = 10**12", ["backend/tests/test_cycle25_findings.py", "-k", "oversize"]),
    ("V04 OAF record accepted for another commit", "backend/mineguard/buildinfo.py", 'if d.get("candidate_commit") != commit:', "if False:", ["backend/tests/test_vertical04_state.py", "-k", "another_commit"]),
]
FE = [
    ("MG-TRUST-025 restored alerts shown as real", "frontend/src/lib.ts", "if (x.loaded_from_store) return \"unverified\";", "", ["src/lib.test.ts"]),
    ("V04 default source names lose their number", "frontend/src/lib.ts", "return `${b} ${k}`;", "return b;", ["src/lib.test.ts"]),
]


def run(cmd, cwd, timeout=900):
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    return p.returncode, (p.stdout + p.stderr)[-8000:]


def workdir():
    d = Path(tempfile.mkdtemp(prefix="mgmut-", dir=ROOT / "logs" / "v04"))
    shutil.copytree(ROOT / "backend", d / "backend", ignore=shutil.ignore_patterns("__pycache__"))
    for n in ("pytest.ini", "test-assets", "models", "docs", "logs", "oaf"):
        if (ROOT / n).exists():
            os.symlink(ROOT / n, d / n)
    shutil.copytree(ROOT / "frontend" / "src", d / "frontend" / "src")
    for n in ("package.json", "tsconfig.json", "vite.config.ts", "node_modules", "index.html"):
        if (ROOT / "frontend" / n).exists():
            os.symlink(ROOT / "frontend" / n, d / "frontend" / n)
    return d


def be_test(d, args):
    return run([PY, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", *args], d)


def fe_test(d, args):
    return run(["npx", "vitest", "run", *args], d / "frontend")


def main():
    out = {"controls": {}, "mutations": []}
    d = workdir()
    try:
        rc, tail = be_test(d, [a for a in ["backend/tests/test_cycle25_findings.py", "backend/tests/test_vertical04_state.py"]])
        out["controls"]["backend"] = {"rc": rc, "tail": tail[-200:]}
        rc2, tail2 = fe_test(d, ["src/lib.test.ts"])
        out["controls"]["frontend"] = {"rc": rc2, "tail": tail2[-200:]}
        if rc != 0 or rc2 != 0:
            print("CONTROL FAILED", json.dumps(out["controls"], indent=1)); Path(sys.argv[1]).write_text(json.dumps(out, indent=1)); return 2
    finally:
        shutil.rmtree(d, ignore_errors=True)
    for name, rel, old, new, args in BE + FE:
        d = workdir()
        try:
            f = d / rel
            src = f.read_text()
            if src.count(old) != 1:
                out["mutations"].append({"name": name, "status": "INVALID_ANCHOR", "count": src.count(old)}); continue
            if old == new:
                out["mutations"].append({"name": name, "status": "SKIPPED_NOOP"}); continue
            f.write_text(src.replace(old, new))
            rc, tail = (be_test if rel.startswith("backend") else fe_test)(d, args)
            killed = rc == 1 and bool(re.search(r"\d+ failed", tail) or re.search(r"Tests\s+\d+ failed", tail))   # a real test failure; a collection / import error (rc 2) never counts
            out["mutations"].append({"name": name, "status": "KILLED" if killed else "SURVIVED_OR_ERROR", "rc": rc, "tail": tail[-300:]})
        finally:
            shutil.rmtree(d, ignore_errors=True)
        print(out["mutations"][-1]["status"], name)
    Path(sys.argv[1]).write_text(json.dumps(out, indent=1))
    bad = [m for m in out["mutations"] if m["status"] != "KILLED"]
    print(f"{len(out['mutations']) - len(bad)}/{len(out['mutations'])} killed; not killed: {[m['name'] for m in bad]}")
    return 1 if bad else 0


sys.exit(main())
