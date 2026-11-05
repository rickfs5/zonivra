"""Regression tests for the eight findings of Codex cycle 7 (MG-PROC-001, MG-RC1-012, MG-ORIGIN-002, MG-DATA-001-005/006, MG-SEC-003, MG-RC1-013, MG-NET-001).
The process-control and gate-exit tests use harmless python3/bash processes and never touch anything by name. The run-integrity tests use the real clip and real weights."""
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from conftest import ROOT, VIDEO, fixture_obsset, local_client, needs_video
from mineguard.app import create_app
from mineguard.schema import ValidationFailure, validate_observation_set
from test_rc1_closeout import iso, forged, failed_ids, check  # noqa: F401  (the genuine isolated run fixture and helpers used by the other closeout modules)

MG_PROC = ROOT / "scripts" / "mg_proc.py"
GATE = ROOT / "scripts" / "acceptance_gate.sh"
SLEEPER = ["python3", "-c", "import time; time.sleep(120)", "mg-cycle7-test-process"]


# ================================================================== MG-PROC-001: process control by verified identity
def proc(*args, env=None):
    e = dict(os.environ, **(env or {}))
    return subprocess.run([sys.executable, str(MG_PROC), *args], capture_output=True, text=True, env=e)


def alive(pid):
    r = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    return bool(r) and not r.startswith("Z")


@pytest.fixture()
def spawned():
    made = []

    def spawn(cwd=ROOT, env=None):
        p = subprocess.Popen(SLEEPER, cwd=cwd, env=dict(os.environ, **(env or {})), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        made.append(p)
        time.sleep(0.4)
        return p

    yield spawn
    for p in made:  # these are this test's own child processes, stopped through their own handles
        if p.poll() is None:
            p.terminate()
        try:
            p.wait(5)
        except subprocess.TimeoutExpired:
            p.kill()


def record_of(pid, owner, **override):
    sys.path.insert(0, str(ROOT / "scripts"))
    import mg_proc

    cur = mg_proc.identity(pid)
    rec = {"pid": pid, "lstart": cur["lstart"], "command": cur["command"], "cwd": cur["cwd"], "owner": owner}
    rec.update(override)
    return rec


def test_owned_process_is_started_with_a_record_and_stopped_by_verified_identity(tmp_path):
    rec = tmp_path / "rec.json"
    r = proc("start", str(rec), "--", *SLEEPER, env={"MG_RUN_OWNER": "owner-A"})
    assert r.returncode == 0, r.stderr
    pid = int(r.stdout.strip())
    data = json.loads(rec.read_text())
    assert data["pid"] == pid and data["owner"] == "owner-A" and "mg-cycle7-test-process" in data["command"] and data["lstart"] and data["cwd"] == str(ROOT.resolve())
    assert alive(pid)
    s = proc("stop", str(rec), "--grace", "5")
    assert s.returncode == 0, s.stderr
    deadline = time.time() + 5
    while alive(pid) and time.time() < deadline:
        time.sleep(0.1)
    assert not alive(pid)


def test_start_refuses_without_an_owner_token(tmp_path):
    env = {k: v for k, v in os.environ.items() if k != "MG_RUN_OWNER"}
    r = subprocess.run([sys.executable, str(MG_PROC), "start", str(tmp_path / "r.json"), "--", *SLEEPER], capture_output=True, text=True, env=env)
    assert r.returncode == 2 and not (tmp_path / "r.json").exists()


def test_unrelated_lookalike_process_is_preserved(tmp_path, spawned):
    """Same command line, same working directory, but started elsewhere: it carries no owner token for this run."""
    lookalike = spawned()
    rec = tmp_path / "rec.json"
    rec.write_text(json.dumps(record_of(lookalike.pid, "owner-A")))
    s = proc("stop", str(rec), "--grace", "1")
    assert s.returncode == 3 and "owner token" in s.stderr
    assert lookalike.poll() is None and alive(lookalike.pid)


def test_wrong_directory_process_is_preserved(tmp_path, spawned):
    other = spawned(cwd=tmp_path, env={"MG_RUN_OWNER": "owner-A"})  # has the token, but runs in another directory
    rec = tmp_path / "rec.json"
    rec.write_text(json.dumps(record_of(other.pid, "owner-A", cwd=str(ROOT.resolve()))))
    s = proc("stop", str(rec), "--grace", "1")
    assert s.returncode == 3 and "cwd differs" in s.stderr
    assert other.poll() is None


def test_reused_or_stale_pid_is_not_trusted(tmp_path, spawned):
    p = spawned(env={"MG_RUN_OWNER": "owner-A"})
    rec = tmp_path / "rec.json"
    rec.write_text(json.dumps(record_of(p.pid, "owner-A", lstart="Mon Jan  1 00:00:00 2001")))  # the recorded process started long ago: this pid was reused
    s = proc("stop", str(rec), "--grace", "1")
    assert s.returncode == 3 and "lstart differs" in s.stderr
    assert p.poll() is None
    rec.write_text(json.dumps(record_of(p.pid, "owner-A", command="python3 something-else")))  # same pid, different command
    assert proc("stop", str(rec), "--grace", "1").returncode == 3 and p.poll() is None


def test_failed_verification_stops_safely(tmp_path, spawned):
    p = spawned(env={"MG_RUN_OWNER": "owner-A"})
    missing = proc("stop", str(tmp_path / "does-not-exist.json"))
    assert missing.returncode == 5
    bad = tmp_path / "bad.json"
    for content in ("not json", "{}", json.dumps({"pid": 1, "owner": "x", "lstart": "x", "command": "x", "cwd": "x"}),
                    json.dumps({"pid": True, "owner": "x", "lstart": "x", "command": "x", "cwd": "x"})):
        bad.write_text(content)
        assert proc("stop", str(bad)).returncode == 3
    assert p.poll() is None


def test_a_process_that_is_already_gone_is_reported_not_signalled_elsewhere(tmp_path):
    q = subprocess.Popen(["python3", "-c", "pass"])
    q.wait()
    rec = tmp_path / "rec.json"
    rec.write_text(json.dumps({"pid": q.pid, "owner": "o", "lstart": "x", "command": "x", "cwd": "x"}))
    s = proc("stop", str(rec))
    assert s.returncode == 0 and "already gone" in s.stdout


def test_no_script_terminates_processes_by_name():
    """The historical incident came from name-based termination: no checked-in script may use pkill/killall/pgrep-then-kill patterns."""
    offenders = []
    for f in sorted((ROOT / "scripts").iterdir()):
        if f.suffix not in (".sh", ".py"):
            continue
        for n, line in enumerate(f.read_text().splitlines(), 1):
            code = line.split("#", 1)[0] if f.suffix == ".sh" else line
            if f.suffix == ".sh" and re.search(r"\b(pkill|killall)\b", code):
                offenders.append(f"{f.name}:{n}")
            if f.suffix == ".py" and re.search(r"""["'](pkill|killall)["']""", code):
                offenders.append(f"{f.name}:{n}")
    assert not offenders, offenders


# ================================================================== MG-RC1-012: gate exit status
def gate_prefix():
    """The gate's own text from `STAMP=` to the INT/TERM trap: variables, helpers, finalize. Exercised as written, not re-implemented here."""
    text = GATE.read_text()
    a = text.index("STAMP=$(date")
    b = text.index("trap 'ABORT=\"interrupted by signal\"; exit 130' INT TERM") + len("trap 'ABORT=\"interrupted by signal\"; exit 130' INT TERM")
    return text[a:b]


def run_gate_scenario(tmp_path, scenario, readonly_dir=False):
    (tmp_path / "scripts").mkdir(exist_ok=True)
    link = tmp_path / "scripts" / "mg_proc.py"
    if not link.exists():
        link.symlink_to(MG_PROC)
    if not (tmp_path / "scripts" / "mg_runs.py").exists():
        shutil.copy(ROOT / "scripts" / "mg_runs.py", tmp_path / "scripts" / "mg_runs.py")  # a COPY: its ROOT (and so its allowed base) is the scenario directory, never the real project
    script = f'cd "{tmp_path}" || exit 99\n' + gate_prefix() + "\n" + ('chmod a-w "$DIR"\n' if readonly_dir else "") + scenario + "\n"
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=60)
    dirs = sorted((tmp_path / "logs" / "acceptance_runs").glob("[0-9]*"))  # gate record dirs (not the isolated runs_* dirs)
    if readonly_dir:
        for d in dirs:
            d.chmod(0o755)
    return r, dirs[-1] if dirs else None


@pytest.mark.parametrize("scenario,code,final", [
    ("COMPLETE=1", 0, "PASS"),
    ("FAIL=1; COMPLETE=1", 1, "FAIL"),
    ("exit 7", 7, "INCOMPLETE"),
    ("exit 0", 3, "INCOMPLETE"),  # leaving early with status 0 must never look like success
    ("kill -TERM $$; sleep 5", 130, "INCOMPLETE"),
])
def test_gate_exit_status_matches_the_aggregate(tmp_path, scenario, code, final):
    r, d = run_gate_scenario(tmp_path, scenario)
    assert r.returncode == code, (r.returncode, r.stdout, r.stderr)
    assert (d / f"transcript.{final}.txt").is_file() and (d / f"transcript.{final}.sha256").is_file()
    assert f"AGGREGATE={final}" in (d / f"transcript.{final}.txt").read_text()
    assert [p.name for p in d.glob("transcript.*.txt")] == [f"transcript.{final}.txt"]


@pytest.mark.parametrize("scenario", ["FAIL=1; COMPLETE=1", "FAIL=1; COMPLETE=1; exit 0", "FAIL=1", "FAIL=1; exit 0"])
def test_an_internally_failed_gate_can_never_exit_zero(tmp_path, scenario):
    r, d = run_gate_scenario(tmp_path, scenario)
    assert r.returncode != 0, (scenario, r.stdout)
    assert not list(d.glob("transcript.PASS.*"))


def test_unwritable_evidence_is_an_error_not_a_pass(tmp_path):
    r, d = run_gate_scenario(tmp_path, "COMPLETE=1", readonly_dir=True)
    assert r.returncode == 4 and "evidence could not be written" in r.stderr
    assert not list(d.glob("transcript.PASS.*"))


def test_failed_server_stop_verification_fails_the_gate(tmp_path, spawned):
    """A server record that no longer matches a live process (wrong owner) must signal nothing and turn a complete run into FAIL."""
    p = spawned(env={"MG_RUN_OWNER": "someone-else"})
    recfile = tmp_path / "server.record.json"
    recfile.write_text(json.dumps(record_of(p.pid, "this-run")))
    r, d = run_gate_scenario(tmp_path, f'SERVER_REC="{recfile}"; COMPLETE=1')
    assert r.returncode == 1, (r.returncode, r.stdout, r.stderr)
    assert "server stop: rc=3" in (d / "transcript.FAIL.txt").read_text()
    assert p.poll() is None  # untouched


def test_gate_and_fresh_restart_contain_no_name_based_kill_and_the_fresh_inventory_gate():
    gate, fresh = GATE.read_text(), (ROOT / "scripts" / "fresh_restart_check.sh").read_text()
    for text in (gate, fresh):
        assert not re.search(r"^[^#\n]*\b(pkill|killall)\b", text, re.M)
    assert "FRESH_TOTAL" in gate and "FRESH_TOTAL passed=$TP collected=$TC whole_suite=$ALL" in fresh
    assert "ls test_*.py" in fresh  # the module list is derived from the directory: no module can be left out by editing a list


def test_every_test_module_is_in_the_gate_module_list():
    listed = set(re.search(r'BACKEND_MODULES="([^"]+)"', GATE.read_text()).group(1).split())
    on_disk = {p.stem for p in (ROOT / "backend" / "tests").glob("test_*.py")}
    assert listed == on_disk, (listed ^ on_disk)


# ================================================================== MG-DATA-001-006 / -005: validator
def obs_with_frames(order, frame_count=3):
    o = fixture_obsset("human_annotated")
    o["source_binding"]["frame_count"] = frame_count
    base = o["frames"][0]
    o["frames"] = [{**json.loads(json.dumps(base)), "frame_index": i} for i in order]
    rest = [i for i in range(frame_count) if i not in order]
    o["frame_status_ranges"] = [{"from_frame": i, "to_frame": i, "status": "not_analyzed_stride"} for i in rest]
    return o


def codes(obs):
    with pytest.raises(ValidationFailure) as e:
        validate_observation_set(obs)
    return e.value.codes


def test_ordered_frames_validate_and_unordered_or_duplicated_frames_are_rejected_not_sorted():
    validate_observation_set(obs_with_frames([0, 1, 2]))
    validate_observation_set(obs_with_frames([0, 2]))
    assert "V-26" in codes(obs_with_frames([2, 0]))  # complete coverage, wrong order: rejected, not silently sorted
    assert "V-26" in codes(obs_with_frames([0, 2, 1]))
    assert "V-26" in codes(obs_with_frames([1, 1]))
    assert {"V-26", "V-05"} <= set(codes(obs_with_frames([0, 0, 2])))


def test_model_inference_coverage_requires_stride_order(monkeypatch):
    from mineguard import schema

    o = fixture_obsset("human_annotated")
    model = schema.ObservationSet.model_validate({**o, "origin": "model_inference", "producer": {**o["producer"], "params": {"stride": 1}}})
    model.source_binding.frame_count = 2
    model.frames = [model.frames[0].model_copy(update={"frame_index": 1}), model.frames[0].model_copy(update={"frame_index": 0})]
    model.frame_status_ranges = []
    assert schema._coverage_codes(model) == ["V-24"]
    model.frames = [model.frames[1], model.frames[0]]
    assert schema._coverage_codes(model) == []


@pytest.mark.parametrize("field,value", [("notes", "café"), ("tool", "café"), ("provenance_id", "ñ"), ("created_utc", "é")])
def test_decomposed_unicode_in_meta_values_is_rejected(field, value):
    o = fixture_obsset("human_annotated")
    o["meta"][field] = value
    assert "V-17" in codes(o)
    o["meta"][field] = "café"  # the composed spelling is accepted
    validate_observation_set(o)


def test_import_rejects_decomposed_meta_and_unordered_frames():
    import tempfile
    fx = fixture_obsset

    with tempfile.TemporaryDirectory() as td:
        app = create_app(asset_dir=Path(td) / "a", runs_dir=Path(td) / "r", detector_factory=lambda: None, dist_dir=Path(td) / "none")
        c = local_client(app)
        o = fx("human_annotated")
        o["meta"]["notes"] = "café"
        r = c.post("/api/observations/import", json=o)
        assert r.status_code == 422 and "V-17" in r.json()["codes"]
        r = c.post("/api/observations/import", json=obs_with_frames([2, 0]))
        assert r.status_code == 422 and "V-26" in r.json()["codes"]


# ================================================================== run-integrity tests on a genuine run (real clip, real weights)
@needs_video
def test_coordinated_reordering_of_frames_and_evidence_fails_deep_verification(iso):
    """Observations AND manifest evidence reversed, every digest, the run id and export.json recomputed: still rejected (never sorted into shape)."""
    def reverse_obs(o):
        o["frames"] = list(reversed(o["frames"]))

    def reverse_manifest(m):
        m["evidence"] = list(reversed(m["evidence"]))

    with forged(iso, mutate_obs=reverse_obs, mutate_manifest=reverse_manifest) as rid:
        rep = iso.client.get(f"/api/runs/{rid}/verify").json()
        assert not rep["ok"]
        ids = failed_ids(rep)
        assert "frame_order_canonical" in ids and "schema_valid" in ids
        assert iso.client.get(f"/api/runs/{rid}").json()["trust"] == "unverified"


@needs_video
def test_reordering_only_the_observations_fails_too(iso):
    with forged(iso, mutate_obs=lambda o: o.__setitem__("frames", list(reversed(o["frames"])))) as rid:
        rep = iso.client.get(f"/api/runs/{rid}/verify?deep=false").json()
        assert not rep["ok"] and "frame_order_canonical" in failed_ids(rep)


@needs_video
def test_detections_reassigned_to_other_frames_fail_deep_verification(iso):
    """Pixel digest and boxes of two analyzed frames exchanged between their index slots (order kept increasing, digests recomputed)."""
    def swap(o):
        a, b = o["frames"][1], o["frames"][2]
        a["pixel_sha256"], b["pixel_sha256"] = b["pixel_sha256"], a["pixel_sha256"]
        a["objects"], b["objects"] = b["objects"], a["objects"]

    with forged(iso, mutate_obs=swap) as rid:
        rep = iso.client.get(f"/api/runs/{rid}/verify").json()
        assert not rep["ok"]
        assert {"evidence_matches_observations", "redecoded_pixels_match"} & failed_ids(rep)
        assert iso.client.get(f"/api/runs/{rid}").json()["trust"] == "unverified"


@needs_video
def test_the_genuine_run_has_canonical_order_and_real_directories(iso):
    rep = iso.client.get(f"/api/runs/{iso.run}/verify?deep=false").json()
    assert rep["ok"] and check(rep, "frame_order_canonical")["ok"] and check(rep, "run_and_frames_directories_are_real")["ok"]


# ---------------------------------------------------------------- MG-SEC-003
@needs_video
def test_symlinked_frames_directory_is_rejected_for_verification_and_serving(iso):
    frames, real = iso.dir / "frames", iso.dir / "frames_real"
    assert iso.client.get(f"/api/runs/{iso.run}/frames/0").status_code == 200
    frames.rename(real)
    frames.symlink_to(real, target_is_directory=True)
    try:
        rep = iso.client.get(f"/api/runs/{iso.run}/verify?deep=false").json()
        assert not rep["ok"] and not check(rep, "run_and_frames_directories_are_real")["ok"] and not check(rep, "evidence_paths_canonical")["ok"]
        assert iso.client.get(f"/api/runs/{iso.run}/frames/0").status_code == 404
        assert iso.client.get(f"/api/runs/{iso.run}").json()["trust"] == "unverified"
    finally:
        frames.unlink()
        real.rename(frames)
    assert iso.client.get(f"/api/runs/{iso.run}/verify?deep=false").json()["ok"]


@needs_video
def test_symlinked_frames_directory_pointing_outside_the_run_cannot_serve_outside_files(iso, tmp_path):
    outside = tmp_path / "outside"
    shutil.copytree(iso.dir / "frames", outside)
    frames, real = iso.dir / "frames", iso.dir / "frames_real"
    frames.rename(real)
    frames.symlink_to(outside, target_is_directory=True)
    try:
        assert iso.client.get(f"/api/runs/{iso.run}/frames/0").status_code == 404
        assert not iso.client.get(f"/api/runs/{iso.run}/verify?deep=false").json()["ok"]
    finally:
        frames.unlink()
        real.rename(frames)


@needs_video
def test_symlinked_run_directory_is_neither_listed_nor_served(iso):
    link = iso.runs / "run_0123456789abcdef"
    link.symlink_to(iso.dir, target_is_directory=True)
    try:
        assert iso.client.get("/api/runs/run_0123456789abcdef").status_code == 404
        assert iso.client.get("/api/runs/run_0123456789abcdef/frames/0").status_code == 404
        assert "run_0123456789abcdef" not in [r["run_id"] for r in iso.client.get("/api/runs").json()]
    finally:
        link.unlink()


# ---------------------------------------------------------------- MG-ORIGIN-002 (backend half)
@needs_video
def test_a_run_found_on_disk_is_unverified_until_deep_verification_passes(iso):
    """A new server process (no in-memory trust) over the same run directory: the stored model_inference claim is 'unverified' through the API
    until deep verification passes; only then does it become 'verified'."""
    fresh = local_client(create_app(asset_dir=iso.video.parent, runs_dir=iso.runs, detector_factory=lambda: iso.spy))
    runs = {r["run_id"]: r for r in fresh.get("/api/runs").json()}
    assert runs[iso.run]["trust"] == "unverified"
    bundle = fresh.get(f"/api/runs/{iso.run}").json()
    assert bundle["observation_set"]["origin"] == "model_inference" and bundle["trust"] == "unverified"
    assert fresh.get(f"/api/runs/{iso.run}/verify?deep=false").json()["trust_scope"]["inference_reproduced_locally"] is False
    assert fresh.get(f"/api/runs/{iso.run}").json()["trust"] == "unverified"  # a shallow check never grants trust
    assert fresh.get(f"/api/runs/{iso.run}/verify").json()["ok"]
    assert fresh.get(f"/api/runs/{iso.run}").json()["trust"] == "verified"
