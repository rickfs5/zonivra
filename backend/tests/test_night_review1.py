"""Regression vectors for the night-sprint review 1 findings (MG-EVENT-015, PROV-015, TRUST-015, DELETE-015, MEDIA-015, LIST-015). Synthetic isolated storage only."""
import json
import os
import shutil
import threading
import time

from mineguard import webcam
from mineguard.app import create_app
from conftest import local_client
from test_privacy import run, start_default, tree  # noqa: F401
from test_session_deletion import OK, distinct, listing  # noqa: F401
from test_vs02_webcam_risk import mk, post, scene, wait_decided  # noqa: F401
import pytest

pytestmark = pytest.mark.usefixtures("legacy_delete_enabled")   # MG-DELETE-035: dormant removal logic, isolated test server only


def restart(env):
    return local_client(create_app(asset_dir=env.tmp / "assets", runs_dir=env.runs, detector_factory=lambda: env.det))


# ------------------------------------------------------------------ MG-EVENT-015
def test_events_and_overview_separate_unverified_claims_from_real_inference(mk):
    env = mk(scene(person=range(3, 32), vehicle=range(15, 32)))
    _, a = run(env)
    assert env.c.get("/api/overview").json()["events"]["real"]["RED"] >= 1
    fresh = restart(env)                                   # the grant belongs to the first process: the stored session is now an unverified claim
    o = fresh.get("/api/overview").json()
    assert o["events"]["real"] == {"RED": 0, "YELLOW": 0} and o["events"]["unverified"]["RED"] >= 1 and o["sources"]["unverified"] == 1 and o["sources"]["sessions"] == 0
    ev = fresh.get("/api/events").json()["events"]
    assert ev and all(e["trust"] == "unverified" and e["nature"] == "unverified_claim" for e in ev)
    p = env.runs / a / "observations.json"                 # tampering after a grant is reflected at once (not cached)
    doc = json.loads(p.read_text()); doc["meta"]["notes"] = "x"; p.write_text(json.dumps(doc, indent=1))
    assert env.c.get("/api/overview").json()["events"]["real"] == {"RED": 0, "YELLOW": 0}


# ------------------------------------------------------------------ MG-PROV-015
def test_privacy_on_coordinated_provenance_change_is_detected(mk):
    env = mk(scene(person=range(3, 32)))
    _, run_id = run(env)
    d = env.runs / run_id
    pj = json.loads((d / "provenance.json").read_text()); pj["packages"] = {**pj.get("packages", {}), "torch": "0.0.0-forged"}
    (d / "provenance.json").write_text(json.dumps(pj, indent=1))
    ex = json.loads((d / "export.json").read_text()); ex["provenance"] = pj
    (d / "export.json").write_text(json.dumps(ex, indent=1))                        # coordinated: both files agree
    rep = env.c.get(f"/api/sessions/{run_id}/verify").json()
    assert not rep["ok"]
    assert not next(c for c in rep["checks"] if c["id"] == "provenance_document_equals_the_digest_bound_into_the_session_identity")["ok"]


# ------------------------------------------------------------------ MG-TRUST-015
def test_failed_shallow_verification_revokes_and_restored_bytes_do_not_resurrect(mk):
    env = mk(scene(person=range(3, 32)))
    _, run_id = run(env, privacy="off")
    assert env.c.get(f"/api/sessions/{run_id}").json()["trust"] == "session"
    f = env.runs / run_id / "frames" / "000003.jpg"
    good = f.read_bytes()
    f.write_bytes(good + b"x")
    assert not env.c.get(f"/api/sessions/{run_id}/verify?deep=false").json()["ok"]
    f.write_bytes(good)
    assert env.c.get(f"/api/sessions/{run_id}").json()["trust"] == "unverified"


# ------------------------------------------------------------------ MG-DELETE-015
def test_a_directory_swapped_in_between_validation_and_removal_is_not_deleted(mk, monkeypatch):
    env = mk(scene(person=range(0, 40)))
    a, b = distinct(env, 2)
    keep_b = tree(env.runs / b)
    real_rename = os.rename
    def swapping(src, dst, *x, **k):
        if os.path.basename(str(src)) == a:                        # an attacker swaps the validated directory for another valid session tree just before the rename
            real_rename(src, str(src) + ".orig")
            shutil.copytree(env.runs / b, str(src))
        return real_rename(src, dst, *x, **k)
    monkeypatch.setattr("mineguard.api_vs02.os.rename", swapping)
    r = env.c.post("/api/sessions/delete", json={"ids": [a], "expected_count": 1, **OK}).json()
    assert r["deleted"] == 0 and r["results"][0]["status"] == "refused" and r["results"][0]["reason"].startswith("identity_changed")
    assert (env.runs / a).is_dir() and tree(env.runs / a) == keep_b and tree(env.runs / b) == keep_b      # the swapped-in content is back in place, untouched


def test_deletion_waits_for_the_store_lock_held_by_an_installing_session(mk):
    env = mk(scene(person=range(0, 40)))
    (a,) = distinct(env, 1)
    done = []
    with webcam.STORE_LOCK:
        t = threading.Thread(target=lambda: done.append(env.c.post("/api/sessions/delete", json={"ids": [a], "expected_count": 1, **OK}).status_code))
        t.start(); time.sleep(0.4)
        assert not done and (env.runs / a).exists()               # serialized: nothing happens while an install holds the lock
    t.join(10)
    assert done == [200] and not (env.runs / a).exists()


# ------------------------------------------------------------------ MG-MEDIA-015
def test_media_delete_failure_leaves_the_session_exactly_as_it_was(mk, monkeypatch):
    env = mk(scene(person=range(3, 32)))
    _, run_id = run(env, privacy="off")
    before = tree(env.runs / run_id)
    def boom(*a, **k): raise OSError("disk full")
    monkeypatch.setattr("mineguard.api_vs02._atomic_write", boom)
    r = env.c.post(f"/api/sessions/{run_id}/media/delete", json={"confirm": "ELIMINAR", "expected_files": 34})
    assert r.status_code == 409 and r.json()["detail"]["reason"] == "tombstone_write_failed_nothing_removed" and tree(env.runs / run_id) == before and not (env.runs / run_id / "media_deleted.json").exists()
    assert env.c.get("/api/sessions").json()[0]["retention"] == "frames"


def test_media_delete_requires_the_observed_file_count(mk):
    env = mk(scene(person=range(3, 32)))
    _, run_id = run(env, privacy="off")
    r = env.c.post(f"/api/sessions/{run_id}/media/delete", json={"confirm": "ELIMINAR", "expected_files": 33})
    assert r.status_code == 409 and r.json()["detail"]["now"] == 34 and len(list((env.runs / run_id / "frames").glob("*.jpg"))) == 34
    assert env.c.post(f"/api/sessions/{run_id}/media/delete", json={"confirm": "ELIMINAR"}).status_code == 422


# ------------------------------------------------------------------ MG-LIST-015
def test_listing_survives_dangling_links_and_malformed_directories(mk, tmp_path):
    env = mk(scene(person=range(0, 40)))
    (a,) = distinct(env, 1)
    (env.runs / "session_dddddddddddddddd").symlink_to(tmp_path / "nowhere")
    bad = env.runs / "session_eeeeeeeeeeeeeeee"; bad.mkdir()
    (bad / "manifest.json").write_text("{}"); (bad / "observations.json").write_text('{"frames": []}')
    assert [s["session_id"] for s in env.c.get("/api/sessions").json()] == [a]
    assert env.c.get("/api/overview").status_code == 200 and env.c.get("/api/events").status_code == 200


# ------------------------------------------------------------------ MG-EVENT-030 (cycle-30 review): the events cache is keyed by EVERY file the events derive from
def test_the_events_cache_follows_observations_and_the_other_derivation_inputs(mk, monkeypatch):
    from mineguard import api_vs02
    calls = {"n": 0}
    real = api_vs02.evaluate_dir
    monkeypatch.setattr(api_vs02, "evaluate_dir", lambda *a, **k: (calls.__setitem__("n", calls["n"] + 1), real(*a, **k))[1])
    env = mk(scene(person=range(3, 32), vehicle=range(15, 32)))
    _, a = run(env)
    env.c.get("/api/events"); n1 = calls["n"]
    env.c.get("/api/events")
    assert calls["n"] == n1                                                             # unchanged inputs: served from the cache
    p = env.runs / a / "observations.json"
    st = os.stat(p)
    doc = json.loads(p.read_text()); doc["meta"]["notes"] = "changed after the cache was filled"; p.write_text(json.dumps(doc, indent=1))
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns))                                    # even with the SAME mtime (the size differs)
    env.c.get("/api/events")
    assert calls["n"] > n1                                                              # the derivation input changed: the cached rows are not reused
