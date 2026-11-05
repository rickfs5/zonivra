#!/usr/bin/env python3
"""Mutation check for the MG-PRODUCT-VERTICAL-06 and -07 invariants only (incident record, export, tile facts, deletion containment, component check, idle-duplicate rule). Same rules as mutation_check_v05:
a mutation counts as killed only if a selected test actually FAILS (rc 1 with "N failed"); collection errors and timeouts never count; the clean control must pass first; anchors must occur exactly once.
Usage: scripts/mutation_check_p06.py <result.json>"""
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
BE = [('MG-DELETE-035 the destructive session routes are enabled again', 'backend/mineguard/api_vs02.py', 'SESSION_DELETE_ENABLED = False ', 'SESSION_DELETE_ENABLED = True ', ['backend/tests/test_product_vertical06.py', '-k', 'destructive_session_routes']),
    ('MG-RISK-035 a run whose components disagree is accepted as one result', 'backend/mineguard/api_vs02.py', 'if bundle.get("observation_set") != obs or bundle.get("source") != source or bundle.get("manifest") != manifest or obsset_digest(obs) != manifest["digests"]["obsset"]:', 'if False:', ['backend/tests/test_product_vertical06.py', '-k', 'components_disagree']),
    ('MG-PROV-035 an idle duplicate counts as an overlapping launch', 'backend/mineguard/feeds.py', 'does not make a single active playback look like simultaneous monitoring\n            return any(g is not self and g.video["video_id"] == self.video["video_id"] and g.state in ("playing", "paused") for', 'does not make a single active playback look like simultaneous monitoring\n            return any(g is not self and g.video["video_id"] == self.video["video_id"] for', ['backend/tests/test_product_vertical06.py', '-k', 'idle_duplicate']),
    ('MG-PROV-035 an inactive feed is not distinguished from an active one', 'backend/mineguard/overlap.py', 'if state not in ("playing", "paused"):\n        return "inactive"', 'if False:\n        return "inactive"', ['backend/tests/test_product_vertical06.py', '-k', 'duplicate_status']),
    ('P06 the incident lets AI observations decide severity', 'backend/mineguard/incident.py', '"ai_observations_decide_severity": False', '"ai_observations_decide_severity": True', ['backend/tests/test_product_vertical06.py', '-k', 'simulation_incident_is_grounded']),
    ('P06 UNKNOWN intervals are read from the wrong state', 'backend/mineguard/incident.py', 'if i.get("state") == "UNKNOWN"', 'if i.get("state") == "GREEN"', ['backend/tests/test_product_vertical06.py', '-k', 'unknown_intervals_are']),
    ('P06 local consistency is claimed without a coherent stored run', 'backend/mineguard/incident.py', 'local = "pass" if run and run.get("consistent") else ("fail" if run is not None else "not_checked")', 'local = "pass"', ['backend/tests/test_product_vertical06.py', '-k', 'components_disagree or missing_run']),
    ('P06 a restored alert is classified as a live incident', 'backend/mineguard/incident.py', 'if alert.get("loaded_from_store"):\n        return "restored"', 'if False:\n        return "restored"', ['backend/tests/test_product_vertical06.py', '-k', 'restored_alert']),
    ('P06 tile counts are carried over from an older sample', 'backend/mineguard/monitor.py', 'if obs["fresh"] and v["latest"].get("index") is not None and v["state"] in ("playing", "paused"):', 'if True:', ['backend/tests/test_product_vertical06.py', '-k', 'tile_facts']),
    ('P06 the export is not the incident the screen shows', 'backend/mineguard/api_mvp2.py', 'body = {"schema": "mg.incident.export/1", "incident": inc,', 'body = {"schema": "mg.incident.export/1", "incident": {**inc, "risk": {**inc["risk"], "rule_id": None}},', ['backend/tests/test_product_vertical06.py', '-k', 'json_export']),
    ('MG-INCIDENT-036 a repeated-source incident is classified as scripted', 'backend/mineguard/incident.py', 'if prov == "repeated_source_simulation":\n        return "repeated_source_simulation"', 'if prov == "repeated_source_simulation":\n        return "synthetic"', ['backend/tests/test_product_vertical06.py', '-k', 'provider_and_classification']),
    ('MG-INCIDENT-036 a webcam alert is labelled a scripted simulation', 'backend/mineguard/api_mvp2.py', 'provider = {"kind": "webcam_model_inference", "model_id": mid}', 'provider = {"kind": "scripted_simulation", "model_id": None}', ['backend/tests/test_product_vertical06.py', '-k', 'webcam_alert']),
    ('MG-INCIDENT-036 a restored incident claims the running engine version', 'backend/mineguard/incident.py', '"semantics_version": None if restored else versions["semantics_version"]', '"semantics_version": versions["semantics_version"]', ['backend/tests/test_product_vertical06.py', '-k', 'restored_incident_does_not']),
    ('MG-INCIDENT-037 consistency passes although the episode is not in the stored evaluation', 'backend/mineguard/incident.py', 'if run is not None and run.get("consistent") and not event_matches_run(alert, run["evaluation"]):', 'if False:', ['backend/tests/test_product_vertical06.py', '-k', 'event_is_not_in_the_stored']),
    ('MG-INCIDENT-037 a nested event may differ from the alert identity on load', 'backend/mineguard/alerts.py', '(not isinstance(a["event"], dict) or a["event"].get("event_id") != a["event_id"])', 'False', ['backend/tests/test_product_vertical06.py', '-k', 'nested_event_differs']),
    ('MG-DELETE-036 the disabled routes parse the body before refusing', 'backend/mineguard/api_vs02.py', 'refuse_if_delete_disabled()                                  # MG-DELETE-036: BEFORE the body is read, parsed or validated\n        return await run_in_threadpool(delete_sessions_impl, await parse_delete_body(request, DeleteReq))', 'return await run_in_threadpool(delete_sessions_impl, await parse_delete_body(request, DeleteReq))', ['backend/tests/test_product_vertical06.py', '-k', 'destructive_session_routes']),
    ('P07 a scripted/lifecycle-less incident claims a recorded source lifecycle', 'backend/mineguard/incident.py', 'has_life = bool(lifecycle and lifecycle.get("events"))', 'has_life = True', ['backend/tests/test_product_vertical07.py', '-k', 'scripted_simulation_has_no_source_lifecycle or restored_or_lifecycle_less']),
    ('P07 same-second alert facts are ordered alphabetically instead of causally', 'backend/mineguard/incident.py', 'else _CAUSAL.get(k, 3))', 'else 3)', ['backend/tests/test_product_vertical07.py', '-k', 'ordering_is_by_instant']),
    ('P07 an old incident borrows the CURRENT run lifecycle of a restarted feed', 'backend/mineguard/api_mvp2.py', 'life = feeds.life_for(a["feed_id"], src["source_id"].split(":", 1)[1])', 'life = feeds.get(a["feed_id"]).life_doc()', ['backend/tests/test_product_vertical07.py', '-k', 'restarted_feed_keeps']),
    ('P07 presence is recorded at every sample instead of at its edge', 'backend/mineguard/feeds.py', 'if n and not self._seen[lab]:', 'if n:', ['backend/tests/test_product_vertical07.py', '-k', 'records_start_pause_resume_stop']),
    ('P07 the export drops the lifecycle declaration of the projection', 'backend/mineguard/incident.py', '"recorded": has_life, "run_uid"', '"recorded": True, "run_uid"', ['backend/tests/test_product_vertical07.py', '-k', 'scripted_simulation_has_no_source_lifecycle']),
]
FE = []


def run(cmd, cwd, timeout=300):
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    except subprocess.TimeoutExpired:
        return 124, "TIMEOUT: a mutation that hangs is NOT a kill (no assertion failed)"      # reported as SURVIVED_OR_ERROR, never as KILLED, and never aborts the other mutations
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
        rc, tail = be_test(d, ["backend/tests/test_product_vertical06.py", "backend/tests/test_product_vertical07.py"])
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
