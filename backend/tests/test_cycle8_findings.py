"""Regression tests for the two Codex cycle-8 findings and the controller-invocation defect:
MG-ORIGIN-003 (session vs verified presentation, backend half; the text half is in frontend/src/lib.test.ts and e2e/browser_acceptance.mjs),
MG-PROC-002 (acceptance scripts must never delete user runs; isolated, ownership-proven test storage),
controller safety (help or invalid arguments never start a review / model call). No test here launches Codex or the controller's review/checkpoint."""
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import ROOT, VIDEO, local_client, needs_video
from mineguard.analysis import DEFAULT_RUNS_DIR, configured_runs_dir
from mineguard.app import create_app

MG_RUNS = ROOT / "scripts" / "mg_runs.py"
CONTROLLER = ROOT / "scripts" / "controller.py"


# ================================================================== MG-ORIGIN-003 (backend half)
class Mutable:
    """Forwards to the real detector; once muted it returns no detections, so re-inference cannot match the stored observations."""

    def __init__(self, real):
        self._real, self.muted = real, False

    def predict(self, *a, **k):
        return [] if self.muted else self._real.predict(*a, **k)

    def __getattr__(self, name):
        return getattr(self._real, name)


@pytest.fixture(scope="module")
def sess(detector, tmp_path_factory):
    base = tmp_path_factory.mktemp("c8")
    assets, runs = base / "assets", base / "runs"
    assets.mkdir()
    shutil.copy(VIDEO, assets / VIDEO.name)
    det = Mutable(detector)
    client = local_client(create_app(asset_dir=assets, runs_dir=runs, detector_factory=lambda: det))
    vid = client.get("/api/videos").json()[0]["video_id"]
    job = client.post(f"/api/videos/{vid}/analyze", json={"stride": 30, "threshold": 0.3}).json()["job_id"]
    t = time.time()
    while time.time() - t < 300:
        j = client.get(f"/api/jobs/{job}").json()
        if j["status"] in ("done", "failed", "cancelled"):
            break
        time.sleep(0.3)
    assert j["status"] == "done", j
    return SimpleNamespace(client=client, det=det, assets=assets, runs=runs, run=j["run_id"], dir=runs / j["run_id"])


def api_trust(c, run):
    return c.get(f"/api/runs/{run}").json()["trust"], {r["run_id"]: r["trust"] for r in c.get("/api/runs").json()}[run]


@needs_video
def test_1_new_session_run_is_session_not_verified_before_verification(sess):
    detail, listed = api_trust(sess.client, sess.run)
    assert detail == listed == "session"
    assert sess.client.get(f"/api/runs/{sess.run}").json()["observation_set"]["origin"] == "model_inference"  # real-inference origin is preserved


@needs_video
def test_2_a_shallow_check_does_not_turn_session_into_verified(sess):
    rep = sess.client.get(f"/api/runs/{sess.run}/verify?deep=false").json()
    assert rep["trust_scope"]["inference_reproduced_locally"] is False
    assert api_trust(sess.client, sess.run) == ("session", "session")


@needs_video
def test_3_a_failed_deep_verification_revokes_the_session_grant(sess):
    """Re-inference that does not match must not leave the run presented as sound: session -> unverified."""
    sess.det.muted = True
    try:
        rep = sess.client.get(f"/api/runs/{sess.run}/verify").json()
    finally:
        sess.det.muted = False
    assert rep["ok"] is False and rep["trust_scope"]["inference_reproduced_locally"] is False
    assert api_trust(sess.client, sess.run) == ("unverified", "unverified")


@needs_video
def test_4_successful_deep_verification_makes_the_run_verified(sess):
    rep = sess.client.get(f"/api/runs/{sess.run}/verify").json()
    assert rep["ok"] is True and rep["trust_scope"] == {"local_integrity": True, "inference_reproduced_locally": True, "external_authenticity": False}
    assert api_trust(sess.client, sess.run) == ("verified", "verified")


@needs_video
def test_5_a_failure_after_verified_revokes_verified_too(sess):
    sess.det.muted = True
    try:
        assert sess.client.get(f"/api/runs/{sess.run}/verify").json()["ok"] is False
    finally:
        sess.det.muted = False
    assert api_trust(sess.client, sess.run)[0] == "unverified"
    assert sess.client.get(f"/api/runs/{sess.run}/verify").json()["ok"] is True  # and a later success restores it
    assert api_trust(sess.client, sess.run)[0] == "verified"


@needs_video
def test_6_a_run_stored_on_disk_is_unverified_for_a_new_server_process(sess):
    fresh = local_client(create_app(asset_dir=sess.assets, runs_dir=sess.runs, detector_factory=lambda: sess.det))
    assert api_trust(fresh, sess.run) == ("unverified", "unverified")


# ================================================================== MG-PROC-002: isolated acceptance storage
def run_mg_runs(*args, env=None):
    return subprocess.run([sys.executable, str(MG_RUNS), *args], capture_output=True, text=True, env=dict(os.environ, **(env or {})))


def make_owned(base: Path):
    r = run_mg_runs("create", "--base", str(base))
    assert r.returncode == 0, r.stderr
    path, token = r.stdout.split()
    return Path(path), token


def populate(d: Path, runs=("run_0123456789abcdef",)):
    for n in runs:
        (d / n / "frames").mkdir(parents=True)
        (d / n / "observations.json").write_text("{}")


def tree_digest(p: Path) -> str:
    h = hashlib.sha256()
    for q in sorted(p.rglob("*")):
        h.update(f"{q.relative_to(p)}|{'L' if q.is_symlink() else 'D' if q.is_dir() else 'F'}|{q.read_bytes().hex() if q.is_file() and not q.is_symlink() else ''}\n".encode())
    return h.hexdigest()


@pytest.fixture()
def arena(tmp_path):
    """A base directory with a test-owned runs dir and decoys: a 'user runs' dir full of run_* folders, and sibling decoys next to the owned dir."""
    base = (tmp_path / "acceptance_runs").resolve()
    base.mkdir()
    owned, token = make_owned(base)
    populate(owned)
    user = (tmp_path / "runs").resolve()
    populate(user, ("run_aaaaaaaaaaaaaaaa", "run_bbbbbbbbbbbbbbbb", "run_cccccccccccccccc"))
    sibling = base / "runs_20200101T000000Z_deadbeef"  # looks like an acceptance dir but was not created by mg_runs.py
    populate(sibling, ("run_dddddddddddddddd",))
    return SimpleNamespace(base=base, owned=owned, token=token, user=user, sibling=sibling, snap=lambda: (tree_digest(user), tree_digest(sibling)))


def test_create_makes_unique_owner_marked_dirs_and_never_adopts_an_existing_one(tmp_path):
    base = (tmp_path / "b").resolve()
    a, _ = make_owned(base)
    b, _ = make_owned(base)
    assert a != b and a.parent == base and (a / ".mg_acceptance_runs_owner").is_file()
    assert oct(a.stat().st_mode & 0o777) == "0o700"


def test_remove_deletes_only_the_owned_dir_and_leaves_every_decoy_untouched(arena):
    before = arena.snap()
    r = run_mg_runs("remove", str(arena.owned), arena.token, "--base", str(arena.base))
    assert r.returncode == 0, r.stderr
    assert not arena.owned.exists()
    assert arena.snap() == before and arena.sibling.exists() and arena.user.exists()


@pytest.mark.parametrize("why", ["wrong_token", "empty_token", "no_marker", "marker_is_symlink", "target_is_symlink", "dotdot", "relative", "not_under_base",
                                 "bad_name", "foreign_top_level_file", "foreign_top_level_dir", "symlink_inside", "nested_symlink", "base_itself",
                                 "user_runs_dir", "sibling_without_marker", "marker_of_other_project"])
def test_remove_refuses_ambiguous_or_unsafe_targets_and_deletes_nothing(arena, tmp_path, why):
    base, owned, token, user = arena.base, arena.owned, arena.token, arena.user
    target, tok = str(owned), token
    if why == "wrong_token": tok = "0" * 32
    elif why == "empty_token": tok = ""
    elif why == "no_marker": (owned / ".mg_acceptance_runs_owner").unlink()
    elif why == "marker_is_symlink":
        real = tmp_path / "m.json"; real.write_text((owned / ".mg_acceptance_runs_owner").read_text())
        (owned / ".mg_acceptance_runs_owner").unlink(); (owned / ".mg_acceptance_runs_owner").symlink_to(real)
    elif why == "target_is_symlink":
        link = base / "runs_20260101T000000Z_abcdef01"; link.symlink_to(user, target_is_directory=True); target = str(link)
    elif why == "dotdot": target = str(base / "x" / ".." / owned.name)
    elif why == "relative": target = owned.name
    elif why == "not_under_base":
        other = (tmp_path / "other").resolve(); other.mkdir(); moved = other / owned.name; shutil.copytree(owned, moved); target = str(moved)
    elif why == "bad_name":
        renamed = base / "important_runs"; shutil.copytree(owned, renamed); target = str(renamed)
    elif why == "foreign_top_level_file": (owned / "precious.txt").write_text("user data")
    elif why == "foreign_top_level_dir": (owned / "notes").mkdir()
    elif why == "symlink_inside": (owned / "run_ffffffffffffffff").symlink_to(user, target_is_directory=True)
    elif why == "nested_symlink": (owned / "run_0123456789abcdef" / "frames" / "link").symlink_to(user, target_is_directory=True)
    elif why == "base_itself": target = str(base)
    elif why == "user_runs_dir": target = str(user)
    elif why == "sibling_without_marker": target = str(arena.sibling)
    elif why == "marker_of_other_project":
        m = owned / ".mg_acceptance_runs_owner"; d = json.loads(m.read_text()); d["project"] = "/somewhere/else"; m.write_text(json.dumps(d))
    before = arena.snap(), tree_digest(owned) if owned.exists() else None
    r = run_mg_runs("remove", target, tok, "--base", str(base))
    assert r.returncode == 3 and "REFUSED" in r.stderr, (why, r.stdout, r.stderr)
    assert (arena.snap(), tree_digest(owned) if owned.exists() else None) == before
    assert user.exists() and all((user / n).exists() for n in ("run_aaaaaaaaaaaaaaaa", "run_bbbbbbbbbbbbbbbb", "run_cccccccccccccccc"))


def test_default_base_is_inside_the_project_evidence_dir_and_never_the_user_runs_dir():
    spec = importlib.util.spec_from_file_location("mg_runs", MG_RUNS)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    assert m.DEFAULT_BASE == ROOT / "logs" / "acceptance_runs" and m.DEFAULT_BASE != ROOT / "runs"
    with pytest.raises(m.Refused):
        m.verify_owned(str(ROOT / "runs"), "x" * 32, m.DEFAULT_BASE)


def test_server_storage_follows_MG_RUNS_DIR_and_rejects_unsafe_values(tmp_path):
    ok = (tmp_path / "isolated").resolve(); ok.mkdir()
    assert configured_runs_dir({"MG_RUNS_DIR": str(ok)}) == ok
    assert configured_runs_dir({}) == DEFAULT_RUNS_DIR
    link = tmp_path / "link"; link.symlink_to(ok, target_is_directory=True)
    f = tmp_path / "file"; f.write_text("x")
    for bad in ("relative/runs", str(ok / ".." / "x"), str(link), str(f)):
        with pytest.raises(RuntimeError):
            configured_runs_dir({"MG_RUNS_DIR": bad})


def test_an_app_over_an_isolated_dir_never_lists_or_touches_other_runs(arena, tmp_path):
    before = arena.snap()
    c = local_client(create_app(asset_dir=tmp_path, runs_dir=arena.owned, detector_factory=lambda: None))
    assert [r["run_id"] for r in c.get("/api/runs").json()] == []  # decoy run in the owned dir has no valid observations and is skipped; user runs are not visible
    assert arena.snap() == before


def script_text(name):
    return (ROOT / "scripts" / name).read_text()


@pytest.mark.parametrize("name", ["acceptance_gate.sh", "fresh_restart_check.sh"])
def test_acceptance_scripts_contain_no_broad_deletion_and_use_isolated_storage(name):
    t = "\n".join(l for l in script_text(name).splitlines() if not l.lstrip().startswith("#"))
    for banned in ("rm -rf runs", "rm -rf ./runs", "runs/run_*", "runs/_tmp_", "pkill", "killall"):
        assert banned not in t, f"{name} contains {banned!r}"
    assert "rm -rf" not in t and "rm -r " not in t
    assert "scripts/mg_runs.py create" in t and "MG_RUNS_DIR" in t


def test_the_gate_removes_only_through_the_ownership_checked_helper_and_compares_user_runs_before_and_after():
    t = script_text("acceptance_gate.sh")
    assert "scripts/mg_runs.py remove" in t and "USER_RUNS_BEFORE" in t and "user runs after" in t


# ================================================================== controller invocation safety
@pytest.fixture()
def stub_path(tmp_path):
    """A fake `codex` first on PATH that records any call. The controller must never reach it for help or invalid arguments."""
    d = tmp_path / "bin"; d.mkdir()
    marker = tmp_path / "codex_was_called"
    stub = d / "codex"
    stub.write_text(f"#!/bin/sh\necho called >> '{marker}'\nexit 99\n"); stub.chmod(0o755)
    return SimpleNamespace(env={**os.environ, "PATH": f"{d}:{os.environ['PATH']}"}, marker=marker)


def controller_files():
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT / "logs").glob("controller*") if p.is_file() and p.suffix != ".lock"}


@pytest.mark.parametrize("argv,rc", [(["--help"], 0), (["-h"], 0), (["help"], 0), (["review", "--help"], 2), (["review", "-h"], 2), (["review", "extra"], 2),
                                     (["review", "--dry-run"], 2), (["bogus"], 2), (["--version"], 2), (["checkpoint", "--help"], 2), (["checkpoint", "-m", "x"], 2),
                                     (["status", "--help"], 2), (["status", "x"], 2), (["authorize"], 2), (["authorize", "two", "ref"], 2), (["authorize", "--help"], 2)])
def test_help_and_invalid_controller_arguments_never_start_a_review_or_touch_state(stub_path, argv, rc):
    before = controller_files()
    runs_before = sorted(p.name for p in (ROOT / "runs" / "review").glob("*")) if (ROOT / "runs" / "review").exists() else []
    r = subprocess.run([sys.executable, str(CONTROLLER), *argv], capture_output=True, text=True, env=stub_path.env, timeout=60)
    assert r.returncode == rc, (argv, r.stdout[-200:], r.stderr[-300:])
    assert not stub_path.marker.exists(), "codex was invoked"
    assert controller_files() == before, "controller state or journal changed"
    assert runs_before == (sorted(p.name for p in (ROOT / "runs" / "review").glob("*")) if (ROOT / "runs" / "review").exists() else [])


def test_parse_accepts_only_exact_commands_and_never_returns_review_for_flags():
    spec = importlib.util.spec_from_file_location("controller_mod", CONTROLLER)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    assert m.parse(["review"]) == ("review", [])
    assert m.parse([]) == ("status", [])
    assert m.parse(["checkpoint", "label", "words"]) == ("checkpoint", ["label", "words"])
    assert m.parse(["authorize", "2", "ref"]) == ("authorize", ["2", "ref"])
    for argv in (["review", "--help"], ["review", "-x"], ["--review"], ["Review"], ["review", "x"], ["checkpoint", "--x"]):
        assert isinstance(m.parse(argv), int) and m.parse(argv) != ("review", []), argv


# ---- the gate's own prefix (variables, helpers, finalize) exercised as written against decoy user runs
from test_cycle7_findings import run_gate_scenario  # noqa: E402


def decoy_user_runs(tmp_path):
    runs = tmp_path / "runs"
    populate(runs, ("run_aaaaaaaaaaaaaaaa", "run_bbbbbbbbbbbbbbbb"))
    return runs


def isolated_dirs(tmp_path):
    return sorted((tmp_path / "logs" / "acceptance_runs").glob("runs_*"))


def test_gate_pass_removes_only_its_isolated_dir_and_leaves_user_runs_byte_identical(tmp_path):
    runs = decoy_user_runs(tmp_path)
    before = tree_digest(runs)
    r, d = run_gate_scenario(tmp_path, 'mkdir "$MG_RUNS_DIR/run_1111111111111111"; COMPLETE=1')
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert isolated_dirs(tmp_path) == [] and tree_digest(runs) == before
    assert "isolated runs dir cleanup: rc=0" in (d / "transcript.PASS.txt").read_text()


def test_gate_fails_when_user_runs_change_during_the_run(tmp_path):
    decoy_user_runs(tmp_path)
    r, d = run_gate_scenario(tmp_path, "rm -r runs/run_aaaaaaaaaaaaaaaa; COMPLETE=1")
    assert r.returncode == 1 and (d / "transcript.FAIL.txt").is_file()
    assert "user runs after" in (d / "transcript.FAIL.txt").read_text()


def test_gate_refuses_to_remove_an_isolated_dir_that_holds_foreign_data_and_fails(tmp_path):
    runs = decoy_user_runs(tmp_path)
    before = tree_digest(runs)
    r, d = run_gate_scenario(tmp_path, 'echo precious > "$MG_RUNS_DIR/notes.txt"; COMPLETE=1')
    assert r.returncode == 1 and "REFUSED" in (d / "transcript.FAIL.txt").read_text()
    kept = isolated_dirs(tmp_path)
    assert len(kept) == 1 and (kept[0] / "notes.txt").read_text() == "precious\n" and tree_digest(runs) == before


def test_gate_aborted_early_still_removes_nothing_but_its_own_dir(tmp_path):
    runs = decoy_user_runs(tmp_path)
    before = tree_digest(runs)
    r, d = run_gate_scenario(tmp_path, "exit 7")
    assert r.returncode == 7 and isolated_dirs(tmp_path) == [] and tree_digest(runs) == before


# ---- MG-MVP-VERTICAL-01: the review can only run through an explicit, executable, non-raw wrapper path
def test_wrapper_resolution_is_explicit_and_never_falls_back_to_path(tmp_path):
    spec = importlib.util.spec_from_file_location("controller_mod2", CONTROLLER)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    w = tmp_path / "codex"
    w.write_text("#!/bin/sh\nexit 0\n")
    on = {"MG_REQUIRE_WRAPPER": "1"}
    assert m.resolve_codex_exe(on)[0] is None                                  # required but not given: refused, no PATH lookup
    assert m.resolve_codex_exe({**on, "MG_CODEX_EXE": "codex"})[0] is None     # relative name: refused
    assert m.resolve_codex_exe({**on, "MG_CODEX_EXE": str(w)})[0] is None      # not executable: refused
    w.chmod(0o755)
    assert m.resolve_codex_exe({**on, "MG_CODEX_EXE": str(w)}) == (str(w), None)
    link = tmp_path / "link"; link.symlink_to(w)
    assert m.resolve_codex_exe({**on, "MG_CODEX_EXE": str(link)})[0] is None   # symlink: refused
    assert m.resolve_codex_exe({**on, "MG_CODEX_EXE": str(w), "MG_RAW_CODEX": str(w)})[0] is None  # equals the raw binary: refused
    assert m.resolve_codex_exe({"MG_CODEX_EXE": str(tmp_path / "missing")})[0] is None  # an explicit path alone also must be valid
