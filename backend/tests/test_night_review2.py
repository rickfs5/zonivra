"""Regression vectors for the night-sprint review 2 findings (MG-DELETE/MEDIA/ZONE/WCAM/EVIDENCE/PROC/TRUST/LIST-016). Synthetic isolated storage only."""
from conftest import zones_rev
import json
import os
import shutil
import subprocess
from pathlib import Path

from test_night_review1 import restart
from test_privacy import run, start_default, tree
from test_session_deletion import OK, distinct
from test_vs02_webcam_risk import ZONE, jpeg, mk, post, scene, wait_decided  # noqa: F401
import pytest

pytestmark = pytest.mark.usefixtures("legacy_delete_enabled")   # MG-DELETE-035: dormant removal logic, isolated test server only

ROOT = Path(__file__).resolve().parents[2]


# ------------------------------------------------------------------ MG-DELETE-016: substitution between pinning and validation
def test_directory_swapped_after_pin_but_before_validation_is_not_deleted(mk, monkeypatch):
    env = mk(scene(person=range(0, 40)))
    a, b = distinct(env, 2)
    keep_b = tree(env.runs / b)
    real_walk, state = os.walk, {"done": False}
    def walking(path, *x, **k):
        if not state["done"] and os.path.basename(str(path)) == a:
            state["done"] = True
            os.rename(str(path), str(path) + ".orig")
            shutil.copytree(env.runs / b, str(path))          # another structurally VALID session tree takes the name before validation reads it
        return real_walk(path, *x, **k)
    monkeypatch.setattr("mineguard.api_vs02.os.walk", walking)
    r = env.c.post("/api/sessions/delete", json={"ids": [a], "expected_count": 1, **OK}).json()
    assert r["deleted"] == 0 and r["results"][0]["reason"].startswith("identity_changed")
    assert tree(env.runs / a) == keep_b and tree(env.runs / b) == keep_b


# ------------------------------------------------------------------ MG-MEDIA-016: set or directory changed between enumeration and rename
def test_media_set_changed_before_the_rename_is_refused_and_restored(mk, monkeypatch):
    env = mk(scene(person=range(3, 32)))
    _, run_id = run(env, privacy="off")
    fd = env.runs / run_id / "frames"
    real_rename = os.rename
    def inserting(src, dst, *x, **k):
        if os.path.basename(str(src)) == "frames":
            (fd / "999999.jpg").write_bytes(b"late file")      # a file appears after the count was observed
        return real_rename(src, dst, *x, **k)
    monkeypatch.setattr("mineguard.api_vs02.os.rename", inserting)
    r = env.c.post(f"/api/sessions/{run_id}/media/delete", json={"confirm": "ELIMINAR", "expected_files": 34})
    assert r.status_code == 409 and r.json()["detail"]["reason"] == "media_set_changed_during_deletion"
    assert len(list(fd.glob("*.jpg"))) == 35 and not (env.runs / run_id / "media_deleted.json").exists() and not list((env.runs / run_id).glob("frames_deleting_*"))


def test_media_directory_substituted_before_the_rename_is_refused(mk, monkeypatch):
    env = mk(scene(person=range(3, 32)))
    _, run_id = run(env, privacy="off")
    fd = env.runs / run_id / "frames"
    real_rename = os.rename
    def swapping(src, dst, *x, **k):
        if os.path.basename(str(src)) == "frames":
            real_rename(src, str(src) + "_orig"); shutil.copytree(str(src) + "_orig", str(src))   # same names and bytes, different inode
        return real_rename(src, dst, *x, **k)
    monkeypatch.setattr("mineguard.api_vs02.os.rename", swapping)
    r = env.c.post(f"/api/sessions/{run_id}/media/delete", json={"confirm": "ELIMINAR", "expected_files": 34})
    assert r.status_code == 409 and r.json()["detail"]["reason"] == "media_set_changed_during_deletion" and len(list(fd.glob("*.jpg"))) == 34


# ------------------------------------------------------------------ MG-ZONE-016
def test_zone_write_never_follows_a_planted_temporary_symlink(mk, tmp_path):
    env = mk(scene(person=range(3, 32)))
    _, run_id = run(env, 8)
    victim = tmp_path / "victim.txt"; victim.write_text("precious")
    risk = env.runs / run_id / "risk"
    for name in ("zones.json.tmp", ".zones.json.tmp"):
        (risk / name).symlink_to(victim)
    r = env.c.put(f"/api/sessions/{run_id}/zones", json={"expected_revision": zones_rev(env.c, f"/api/sessions/{run_id}/zones"), "zones": [ZONE]})
    assert r.status_code == 200 and victim.read_text() == "precious"


# ------------------------------------------------------------------ MG-WCAM-016
def test_sequence_gaps_are_recorded_and_break_continuity(mk):
    env = mk(scene(person=range(0, 40)))
    sid = start_default(env.c, privacy="off").json()["session_id"]
    for seq in (0, 1, 3, 4, 6):                                 # seq 2 and seq 5 never arrive (failed upload, skipped capture, failed encode: all consume a sequence number)
        assert post(env.c, sid, seq).status_code == 202
        wait_decided(env.c, sid, [0, 1, 3, 4, 6].index(seq) + 1)
    run_id = env.c.post(f"/api/webcam/sessions/{sid}/stop").json()["run_id"]
    b = env.c.get(f"/api/sessions/{run_id}").json()
    c = b["manifest"]["counters"]
    assert c["client_loss_events"] == 2 and c["client_lost_frames"] == 2 and c["received"] == 5       # stubs are not received frames
    stubs = [s for s in b["source"]["samples"] if s["reason"] == "client_loss"]
    assert [s["lost_frames"] for s in stubs] == [1, 1] and all(s["status"] == "not_analyzed_dropped" and s["seq"] is None for s in stubs)
    times = [s["capture_us"] for s in b["source"]["samples"]]
    assert times == sorted(times) and len(set(times)) == len(times)
    assert env.c.get(f"/api/sessions/{run_id}/verify?deep=false").json()["ok"]


def test_skipped_header_is_no_longer_part_of_the_protocol(mk):
    env = mk(scene())
    sid = start_default(env.c).json()["session_id"]
    h = {"X-MG-Seq": "0", "X-MG-Capture-Us": "5000000", "X-MG-Skipped": "7", "Content-Type": "image/jpeg"}
    assert env.c.post(f"/api/webcam/sessions/{sid}/frames", content=jpeg(0), headers=h).status_code == 202      # ignored: loss is derived from sequence gaps only
    wait_decided(env.c, sid, 1)
    assert env.c.get(f"/api/webcam/sessions/{sid}").json()["counters"].get("client_lost_frames", 0) == 0


# ------------------------------------------------------------------ MG-EVIDENCE-016
def test_media_deleted_session_has_no_live_evidence_references_and_a_validated_tombstone(mk):
    env = mk(scene(person=range(3, 32), vehicle=range(15, 32)))
    _, run_id = run(env, privacy="off")
    assert env.c.get(f"/api/sessions/{run_id}/risk").json()["evaluation"]["evidence_refs"]
    assert env.c.post(f"/api/sessions/{run_id}/media/delete", json={"confirm": "ELIMINAR", "expected_files": 34}).status_code == 200
    ev = env.c.get(f"/api/sessions/{run_id}/risk").json()["evaluation"]
    assert ev["evidence_status"] == "media_deleted" and all(v == {} or v == [] for v in ev["evidence_refs"].values())
    assert env.c.get(f"/api/sessions/{run_id}").json()["evidence_available"] is False
    assert env.c.get(f"/api/sessions/{run_id}/risk/export").json()["bundle"]["evidence_available"] is False
    assert env.c.get(f"/api/sessions/{run_id}/verify").json()["ok"]
    t = env.runs / run_id / "media_deleted.json"
    good = json.loads(t.read_text())
    for edit in ({"files": good["files"] + 1}, {"evidence_sha256": "0" * 64}, {"bytes_reported": -1}, {"extra": 1}, {"bytes": 5}):
        t.write_text(json.dumps({**good, **edit}))
        rep = env.c.get(f"/api/sessions/{run_id}/verify").json()
        assert not rep["ok"] and not next(c for c in rep["checks"] if c["id"].startswith("media_deletion_tombstone"))["ok"], edit
    t.write_text(json.dumps({**good, "bytes_reported": good["bytes_reported"] + 12345}))   # a positive-but-different byte total cannot be disproved after deletion: it is declared informational, not attested
    rep = env.c.get(f"/api/sessions/{run_id}/verify").json()
    det = next(c for c in rep["checks"] if c["id"].startswith("media_deletion_tombstone"))
    assert rep["ok"] and "NOT verifiable" in det["detail"] and "bytes_reported" in det["detail"]
    t.write_text("not json")
    assert not env.c.get(f"/api/sessions/{run_id}/verify").json()["ok"]


def test_a_tombstone_planted_in_a_privacy_on_session_is_rejected(mk):
    env = mk(scene(person=range(3, 12)))
    _, run_id = run(env, 8)
    (env.runs / run_id / "media_deleted.json").write_text(json.dumps({"deleted_utc": "x", "files": 0, "bytes_reported": 0, "evidence_sha256": "0" * 64}))
    assert not env.c.get(f"/api/sessions/{run_id}/verify").json()["ok"]


# ------------------------------------------------------------------ MG-TRUST-016, MG-LIST-016
def test_overview_does_not_count_unverified_sources_as_real(mk):
    env = mk(scene(person=range(3, 32), vehicle=range(15, 32)))
    run(env)
    assert env.c.get("/api/overview").json()["sources"]["sessions"] == 1
    o = restart(env).get("/api/overview").json()
    assert o["sources"]["sessions"] == 0 and o["sources"]["unverified"] == 1


def test_run_listing_survives_a_dangling_run_symlink(mk, tmp_path):
    env = mk(scene())
    (env.runs / "run_dddddddddddddddd").symlink_to(tmp_path / "nowhere")
    (env.runs / "run_eeeeeeeeeeeeeeee").mkdir()
    (env.runs / "run_eeeeeeeeeeeeeeee" / "manifest.json").write_text("{}")
    (env.runs / "run_eeeeeeeeeeeeeeee" / "observations.json").write_text('{"frames": []}')
    assert env.c.get("/api/runs").status_code == 200


# ------------------------------------------------------------------ MG-PROC-016
def test_destructive_browser_suites_refuse_to_run_without_the_isolated_runner():
    for script in ("e2e/mvp_acceptance.mjs", "e2e/browser_acceptance.mjs"):
        env = {k: v for k, v in os.environ.items() if k != "MG_E2E_ISOLATED"}
        p = subprocess.run(["node", str(ROOT / script), "http://127.0.0.1:1"], capture_output=True, text=True, env=env, timeout=30)
        assert p.returncode == 3 and "refusing to run" in p.stderr, script


def test_legacy_test_script_never_attaches_to_an_existing_server():
    s = (ROOT / "scripts" / "test_all.sh").read_text()
    assert "127.0.0.1:8000" not in s and "run_mvp_e2e.sh" in s and "curl" not in s
    assert "MG_E2E_ISOLATED" in (ROOT / "scripts" / "run_mvp_e2e.sh").read_text()
