"""Cycle-4 review findings: event identity bound to the FULL canonical input (MG-EVENT-013), finalization failures of any kind (MG-WCAM-015),
exact half-up microseconds (MG-TIME-013), and the MVP runner's cleanup traps (MG-GATE-013)."""
from conftest import zones_rev
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from mineguard import webcam
from mineguard.risk import evaluation as ev
from mineguard.risk.kernel import Params
from mineguard.risk.tracker import TrackerParams
from mineguard.risk.zone_eval import make_zone
from test_vs02_webcam_risk import mk, post, run_session, scene, start, wait_decided  # noqa: F401  (fixtures and helpers of the webcam suite)

ROOT = Path(__file__).resolve().parents[2]


def records():
    out = []
    for i in range(40):
        objs = [{"obj_key": "o1", "cls": {"label": "person", "state": "known", "detector_label": "p", "detector_class_id": 1}, "bbox": {"x": 150, "y": 60, "w": 20, "h": 100},
                 "truncated": {"left": False, "right": False, "top": False, "bottom": False}, "confidence": {"permille": 900, "kind": "model_uncalibrated"}, "track_ref": None}]
        out.append({"index": i, "t_us": i * 100_000, "status": "analyzed", "objects": objs})
    return out


ZONE = make_zone("Z1", "restricted", [[100, 50], [300, 50], [300, 170], [100, 170]], 320, 180)
INPUTS = {"obsset_digest": "a" * 64, "source_digest": "b" * 64, "frame_table_digest": "c" * 64, "kind": "webcam"}


def evaluate(params=None, tparams=None, inputs=None, zone=ZONE):
    return ev.evaluate_records(records(), 4_000_000, [zone], params or Params(max_sample_gap_us=250_500), tparams or TrackerParams(), inputs or INPUTS, "model_inference")


def ids(doc):
    return [e["event_id"] for z in doc["zones"].values() for e in z["events"]]


def test_event_ids_are_bound_to_the_full_canonical_input_not_just_the_observation_digest(monkeypatch):
    base = evaluate()
    assert ids(base) and ids(evaluate()) == ids(base) and base["digest"] == evaluate()["digest"]
    variants = {
        "policy parameter (max_sample_gap)": evaluate(params=Params(max_sample_gap_us=200_000)),
        "policy parameter (debounce)": evaluate(params=Params(max_sample_gap_us=250_500, debounce_us=400_000)),
        "tracker parameter": evaluate(tparams=TrackerParams(confirm_hits=3)),
        "frame table digest": evaluate(inputs={**INPUTS, "frame_table_digest": "d" * 64}),
        "source digest": evaluate(inputs={**INPUTS, "source_digest": "e" * 64}),
        "run configuration": evaluate(inputs={**INPUTS, "run_config": "1" * 64}),
        "frame table provider": evaluate(inputs={**INPUTS, "frame_table_provider": {"id": "other-provider", "version": "1"}}),
        "any other stored input field": evaluate(inputs={**INPUTS, "future_field": "x"}),
        "observation digest": evaluate(inputs={**INPUTS, "obsset_digest": "f" * 64}),
        "zone geometry": evaluate(zone=make_zone("Z1", "restricted", [[100, 50], [300, 50], [300, 171], [100, 171]], 320, 180)),
    }
    monkeypatch.setattr(ev, "ENGINE_VERSION", "mg-risk-kernel/9.9.9")
    variants["engine version"] = evaluate()
    for name, d in variants.items():
        assert d["digest"] != base["digest"], name
        if name != "zone geometry":   # the zone is bound by its own digest inside the event id tuple, beside the input digest
            assert d["input_digest"] != base["input_digest"], name
        assert ids(d) and set(ids(d)).isdisjoint(ids(base)), f"event ids shared with a different evaluation: {name}"


# ------------------------------------------------------------------ MG-TIME-013
def test_last_frame_duration_uses_integer_half_up_not_ties_to_even(tmp_path):
    from mineguard.api_vs02 import risk_inputs
    d = tmp_path / "run_0123456789abcdef"
    d.mkdir()
    ft = {"time_base": {"num": 1, "den": 128}, "start_ticks": 0, "pts_ticks": [0, 1], "last_frame_duration_ticks": 1, "width": 10, "height": 10, "rotation": 0}
    (d / "observations.json").write_text(json.dumps({"producer": {"params": {}}}))
    (d / "source.json").write_text(json.dumps({"frame_table": ft}))
    (d / "manifest.json").write_text("{}")
    _, _, _, pts, end_us = risk_inputs(d)
    assert pts == [0, 7813] and end_us == 7813 + 7813      # 1/128 s = 7812.5 us -> 7813 (half up); Python round() would give 7812


# ------------------------------------------------------------------ MG-WCAM-015
@pytest.mark.parametrize("exc", [OSError("disk full"), PermissionError("denied"), RuntimeError("boom"), ValueError("bad")])
def test_any_finalization_failure_ends_failed_releases_the_lock_and_removes_staging(mk, monkeypatch, exc):
    def boom(self):
        raise exc
    monkeypatch.setattr(webcam.Session, "_finalize", boom)
    env = mk(scene(person=range(0, 40)))
    sid = start(env.c).json()["session_id"]
    for i in range(3):
        post(env.c, sid, i)
        wait_decided(env.c, sid, i + 1)
    r = env.c.post(f"/api/webcam/sessions/{sid}/stop")
    assert r.status_code == 500 and r.json()["detail"]["msg_id"] == "mg.refusal.session_failed"
    t = time.time()
    while time.time() - t < 5 and env.app.state.st.infer_lock.locked():
        time.sleep(0.05)
    assert not env.app.state.st.infer_lock.locked()
    assert not [p for p in env.runs.iterdir() if p.name.startswith(("_tmp_", "session_"))]
    assert start(env.c).status_code == 201                                   # the next session can start
    assert env.c.get(f"/api/webcam/sessions/{sid}").json()["state"] == "failed"


def test_idle_session_whose_finalization_raises_an_ordinary_error_does_not_kill_the_reaper(mk, monkeypatch):
    monkeypatch.setattr(webcam, "IDLE_FINALIZE_S", 0.3)
    calls = []
    orig = webcam.Session._finalize

    def flaky(self):
        calls.append(1)
        if len(calls) == 1:
            raise OSError("disk full")
        return orig(self)
    monkeypatch.setattr(webcam.Session, "_finalize", flaky)
    env = mk(scene(person=range(0, 40)))
    s1 = start(env.c).json()["session_id"]
    post(env.c, s1, 0)
    wait_decided(env.c, s1, 1)
    t = time.time()
    while time.time() - t < 6 and env.app.state.st.infer_lock.locked():
        time.sleep(0.1)
    assert not env.app.state.st.infer_lock.locked() and env.c.get(f"/api/webcam/sessions/{s1}").json()["state"] == "failed"
    s2 = start(env.c).json()["session_id"]                                    # and the reaper still works for the NEXT idle session
    post(env.c, s2, 0)
    wait_decided(env.c, s2, 1)
    t = time.time()
    while time.time() - t < 6 and not [p for p in env.runs.iterdir() if p.name.startswith("session_")]:
        time.sleep(0.1)
    assert len([p for p in env.runs.iterdir() if p.name.startswith("session_")]) == 1


# ------------------------------------------------------------------ MG-GATE-013: the MVP runner cleans up after itself on failure and on signals
def stable_python():
    """The interpreter binary a framework Python re-execs into (macOS), so a stub server's command line does not change after start (mg_proc verifies it strictly)."""
    p = Path(sys.base_prefix) / "Resources" / "Python.app" / "Contents" / "MacOS" / "Python"
    return str(p) if p.exists() else None


HEALTH_SERVER = """import http.server
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"{}")
    def log_message(self, *a): pass
import os
http.server.HTTPServer(("127.0.0.1", int(os.environ["MG_E2E_PORT"])), H).serve_forever()
"""


def sandbox(tmp_path, node_body, python_body=HEALTH_SERVER):
    """A throw-away project: the REAL runner, mg_proc.py and mg_runs.py, a stub `node` and a stub server interpreter. Nothing of the real project is touched."""
    pr = tmp_path / "proj"
    (pr / "scripts").mkdir(parents=True)
    (pr / "e2e").mkdir()
    (pr / "logs" / "acceptance_runs").mkdir(parents=True)
    (pr / ".venv" / "bin").mkdir(parents=True)
    for f in ("run_mvp_e2e.sh", "mg_proc.py", "mg_runs.py"):
        shutil.copy(ROOT / "scripts" / f, pr / "scripts" / f)
    (pr / "e2e" / "mvp_acceptance.mjs").write_text("")
    (pr / "logs" / "mvp_e2e").mkdir()
    (pr / "logs" / "mvp_e2e" / "fake_camera.y4m").write_text("x")
    py = pr / ".venv" / "bin" / "python"
    interp = stable_python()
    if interp is None:
        pytest.skip("no stable interpreter binary for a stub server on this platform")
    py.write_text(f"#!{interp}\n" + python_body)
    py.chmod(0o755)
    nb = tmp_path / "bin"
    nb.mkdir()
    (nb / "node").write_text("#!/bin/sh\n" + node_body)
    (nb / "node").chmod(0o755)
    return pr, nb


def owned_dirs(pr):
    return sorted(p.name for p in (pr / "logs" / "acceptance_runs").glob("runs_*"))


def server_alive(pr):
    rec = pr / "logs" / "mvp_e2e" / "server.record.json"
    if not rec.exists():
        return False
    pid = json.loads(rec.read_text())["pid"]
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def run_runner(pr, nb, sig=None, wait=1.0):
    env = {**os.environ, "PATH": f"{nb}:/usr/bin:/bin:/usr/sbin:/sbin", "MG_E2E_HEALTH_WAIT": "10", "MG_E2E_PORT": "8791"}
    def default_signals():  # a background parent (e.g. the acceptance gate) starts children with SIGINT ignored; the runner's own trap must be tested from the default disposition
        signal.signal(signal.SIGINT, signal.SIG_DFL)
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
    p = subprocess.Popen(["/bin/bash", str(pr / "scripts" / "run_mvp_e2e.sh")], cwd=pr, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True,
                         preexec_fn=default_signals)
    if sig is not None:
        time.sleep(wait)
        os.kill(p.pid, sig)
    out, _ = p.communicate(timeout=60)
    return p.returncode, out


def test_runner_happy_path_stops_its_server_and_removes_exactly_its_storage(tmp_path):
    pr, nb = sandbox(tmp_path, "exit 0\n")
    rc, out = run_runner(pr, nb)
    assert rc == 0 and "MVP_E2E_EXIT=0 server_stop_rc=0 cleanup_rc=0" in out, out
    assert owned_dirs(pr) == [] and not server_alive(pr)


def test_runner_preserves_a_failing_test_exit_status_and_still_cleans_up(tmp_path):
    pr, nb = sandbox(tmp_path, "exit 7\n")
    rc, out = run_runner(pr, nb)
    assert rc != 0 and "MVP_E2E_EXIT=7" in out and owned_dirs(pr) == [] and not server_alive(pr)


@pytest.mark.parametrize("sig,expected", [(signal.SIGTERM, 143), (signal.SIGINT, 130)])
def test_runner_interrupted_while_the_test_runs_stops_the_server_and_removes_storage(tmp_path, sig, expected):
    pr, nb = sandbox(tmp_path, "sleep 30\n")
    rc, out = run_runner(pr, nb, sig=sig, wait=3.0)
    assert rc == expected, (rc, out)
    assert owned_dirs(pr) == [] and not server_alive(pr), "server or storage leaked after an interruption"


def test_runner_failure_to_start_the_server_leaves_no_storage(tmp_path):
    pr, nb = sandbox(tmp_path, "exit 0\n", python_body="import sys\nsys.exit(1)\n")
    rc, out = run_runner(pr, nb)
    assert rc in (6, 7) and owned_dirs(pr) == [] and not server_alive(pr)   # 6: the helper refused to start an immediately dying process; 7: it died after launch


def test_runner_never_creates_storage_before_the_fake_camera_exists(tmp_path):
    pr, nb = sandbox(tmp_path, "exit 0\n", python_body="import sys\nsys.exit(1)\n")   # the generator (the stub interpreter) fails
    (pr / "logs" / "mvp_e2e" / "fake_camera.y4m").unlink()
    rc, out = run_runner(pr, nb)
    assert rc == 5 and owned_dirs(pr) == []


# ------------------------------------------------------------------ MG-GATE-014: an interrupted gate stops the fresh-restart server before removing its storage
def test_gate_finalizer_stops_a_fresh_restart_server_left_running_by_an_interrupted_step(tmp_path):
    from test_cycle7_findings import run_gate_scenario
    interp = stable_python()
    if interp is None:
        pytest.skip("no stable interpreter binary on this platform")
    scenario = (f'mkdir -p "$MG_RUNS_DIR"; python3 scripts/mg_proc.py start "$MG_PID_DIR/fresh_server.json" -- {interp} -c "import time; time.sleep(300)" > /dev/null; '
                'echo "$(python3 -c \'import json,sys;print(json.load(open(sys.argv[1]))["pid"])\' "$MG_PID_DIR/fresh_server.json")" > "$MG_PID_DIR/pid.txt"; exit 130')
    r, d = run_gate_scenario(tmp_path, scenario)
    pid = int((d / "pid.txt").read_text())
    time.sleep(0.5)
    try:
        os.kill(pid, 0)
        alive = True
    except ProcessLookupError:
        alive = False
    if alive:
        os.kill(pid, signal.SIGKILL)   # never leave the stub behind, even when the assertion below fails
    assert not alive, "the fresh-restart server survived the interrupted gate"
    assert r.returncode == 130 and "fresh-restart server stop in finalize: rc=0" in (d / "transcript.INCOMPLETE.txt").read_text()


def test_the_fresh_restart_script_traps_abnormal_exits_and_marks_a_normal_end():
    t = (ROOT / "scripts" / "fresh_restart_check.sh").read_text()
    assert "trap fresh_cleanup EXIT" in t and "trap 'exit 130' INT" in t and "trap 'exit 143' TERM" in t and "FRESH_NORMAL=1\nexit $FAIL" in t
    assert t.index("trap fresh_cleanup EXIT") < t.index("step \"start fresh server #1\"")      # armed before the first server is started


# ------------------------------------------------------------------ MG-ZONE-014: persisted zone sets are re-validated on every load
def _zones_path(env, run):
    return env.runs / run / "risk" / "zones.json"


def _edit_zones(env, run, edit):
    p = _zones_path(env, run)
    doc = json.loads(p.read_text())
    edit(doc)
    p.write_text(json.dumps(doc, indent=1))


@pytest.mark.parametrize("name,edit", [
    ("polygon edited, old digest kept", lambda d: d["zones"][0].update(polygon=[[100, 50], [300, 50], [300, 120], [100, 120]])),
    ("self-intersecting polygon", lambda d: d["zones"][0].update(polygon=[[0, 0], [100, 100], [100, 0], [0, 100]])),
    ("digest forged", lambda d: d["zones"][0].update(digest="0" * 64)),
    ("wrong dimensions", lambda d: d["zones"][0].update(width=999)),
    ("extra member", lambda d: d["zones"][0].update(extra=1)),
    ("duplicate ids", lambda d: d["zones"].append(dict(d["zones"][0]))),
    ("bad kind", lambda d: d["zones"][0].update(kind="evil")),
    ("revision not an integer", lambda d: d.update(revision="7")),
    ("zone set shape", lambda d: d.update(unexpected=True)),
    ("too many zones", lambda d: d.update(zones=[dict(d["zones"][0], zone_id=f"Z{i}") for i in range(9)])),
])
def test_a_tampered_or_malformed_persisted_zone_set_is_refused_before_evaluation(mk, name, edit):
    env = mk(scene(person=range(3, 32)))
    _, run = run_session(env, 34)
    _edit_zones(env, run, edit)
    for url in (f"/api/sessions/{run}/zones", f"/api/sessions/{run}/risk", f"/api/sessions/{run}/risk/export"):
        r = env.c.get(url)
        assert r.status_code == 409 and r.json()["detail"]["msg_id"] == "mg.refusal.zones_inconsistent", (name, url, r.text[:120])


def test_unparsable_zone_file_is_refused_and_a_valid_put_repairs_it_with_the_next_revision(mk):
    env = mk(scene(person=range(3, 32)))
    _, run = run_session(env, 34)
    _zones_path(env, run).write_text("{not json")
    assert env.c.get(f"/api/sessions/{run}/risk").status_code == 409
    ok = {"zone_id": "Z7", "polygon": [[10, 10], [60, 10], [60, 60], [10, 60]]}
    r = env.c.put(f"/api/sessions/{run}/zones", json={"expected_revision": zones_rev(env.c, f"/api/sessions/{run}/zones"), "zones": [ok]})
    assert r.status_code == 200 and r.json()["revision"] == 1
    assert env.c.get(f"/api/sessions/{run}/risk").status_code == 200 and env.c.get(f"/api/sessions/{run}/zones").json()["zones"][0]["zone_id"] == "Z7"
    _edit_zones(env, run, lambda d: d["zones"][0].update(digest="1" * 64))
    r2 = env.c.put(f"/api/sessions/{run}/zones", json={"expected_revision": zones_rev(env.c, f"/api/sessions/{run}/zones"), "zones": [ok]})                       # a forged-but-parsable file keeps its revision counter
    assert r2.status_code == 200 and r2.json()["revision"] == 2


def test_event_identity_matches_the_geometry_actually_used(mk):
    env = mk(scene(person=range(3, 32)))
    _, run = run_session(env, 34)
    r = env.c.get(f"/api/sessions/{run}/risk").json()
    z = r["zones"]["zones"][0]
    assert r["evaluation"]["inputs"]["zones"] == [{"zone_id": z["zone_id"], "digest": z["digest"]}] and r["evaluation"]["zone_defs"] == [z]


# ------------------------------------------------------------------ MG-TRUST-014: a mismatch seen by ANY read removes the grant for good
def test_a_passive_integrity_mismatch_removes_a_session_grant_and_restoring_the_bytes_does_not_bring_it_back(mk):
    env = mk(scene(person=range(3, 32)))
    _, run = run_session(env, 34)
    p = env.runs / run / "frames" / "000010.jpg"
    orig = p.read_bytes()
    assert env.c.get(f"/api/sessions/{run}").json()["trust"] == "session"
    p.write_bytes(orig + b"\x00")                                                            # no verification is run: the mismatch is only OBSERVED by a load
    assert env.c.get(f"/api/sessions/{run}").json()["trust"] == "unverified"
    p.write_bytes(orig)
    assert env.c.get(f"/api/sessions/{run}").json()["trust"] == "unverified"                 # not resurrected by loading restored bytes
    assert {s["session_id"]: s["trust"] for s in env.c.get("/api/sessions").json()}[run] == "unverified"
    assert env.c.get(f"/api/sessions/{run}/verify").json()["ok"] and env.c.get(f"/api/sessions/{run}").json()["trust"] == "verified"   # only a successful deep verification regrants


def test_a_verified_session_loses_its_grant_on_a_passive_mismatch_too(mk):
    env = mk(scene(person=range(3, 12)))
    _, run = run_session(env, 8)
    assert env.c.get(f"/api/sessions/{run}/verify").json()["ok"] and env.c.get(f"/api/sessions/{run}").json()["trust"] == "verified"
    p = env.runs / run / "frames" / "000003.jpg"
    orig = p.read_bytes()
    p.write_bytes(orig[:-1])
    assert env.c.get(f"/api/sessions/{run}").json()["trust"] == "unverified"
    p.write_bytes(orig)
    assert env.c.get(f"/api/sessions/{run}").json()["trust"] == "unverified"


def test_runner_refuses_to_run_against_a_port_that_something_else_already_serves(tmp_path):
    """A user's live server on the same port must never receive the test suite (it once did: sessions and zones were written into live storage)."""
    import socket
    pr, nb = sandbox(tmp_path, "exit 0\n")
    s = socket.socket()
    s.bind(("127.0.0.1", 8791))
    s.listen(1)
    try:
        rc, out = run_runner(pr, nb)
    finally:
        s.close()
    assert rc == 8 and "already in use" in out and owned_dirs(pr) == [] and not server_alive(pr)


def test_runner_uses_the_configured_port_and_passes_it_to_the_browser_script(tmp_path):
    pr, nb = sandbox(tmp_path, 'echo "ARGS:$@" > "$PWD/node_args.txt"\nexit 0\n')
    rc, out = run_runner(pr, nb)
    assert rc == 0 and (pr / "node_args.txt").read_text().split()[-1] == "http://127.0.0.1:8791"
