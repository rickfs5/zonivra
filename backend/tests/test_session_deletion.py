"""MVP 1 P0-B: session deletion. Synthetic fixtures only; decoys stand for MP4 runs, source video files, weights, review archives and 'personal' sessions that must survive."""
import hashlib
import os
import shutil
from pathlib import Path

import pytest

from mineguard import webcam
from test_privacy import image_like_files, run, start_default, tree  # noqa: F401
from test_vs02_webcam_risk import ZONE, jpeg, mk, post, scene, wait_decided  # noqa: F401

pytestmark = pytest.mark.usefixtures("legacy_delete_enabled")   # MG-DELETE-035: dormant removal logic, isolated test server only

OK = {"confirm": "ELIMINAR"}


def make_sessions(env, n, privacy=None):
    return [run(env, 6, privacy=privacy)[1] for _ in range(n)]


def distinct(env, n):
    """n sessions with different capture clocks (so they get different ids)."""
    out = []
    for k in range(n):
        sid = start_default(env.c, privacy="off" if k % 2 else "on").json()["session_id"]
        for i in range(6):
            post(env.c, sid, i, cap_us=1_000_000 * (k + 1) + i * 100_000)
            wait_decided(env.c, sid, i + 1)
        out.append(env.c.post(f"/api/webcam/sessions/{sid}/stop").json()["run_id"])
    assert len(set(out)) == n
    return out


def decoys(env):
    d = {}
    (env.runs / "run_0123456789abcdef" / "frames").mkdir(parents=True)
    (env.runs / "run_0123456789abcdef" / "export.json").write_text("{}")
    (env.runs / "run_0123456789abcdef" / "frames" / "000001.jpg").write_bytes(b"mp4-run-evidence-stand-in")
    (env.runs / "review").mkdir()
    (env.runs / "review" / "cycle1.txt").write_text("historical review archive")
    (env.runs / "notes.txt").write_text("unrelated file")
    (env.runs / "source.mp4").write_bytes(b"source-video-stand-in")
    (env.runs / "weights.pth").write_bytes(b"weights-stand-in")
    for name in ("run_0123456789abcdef", "review"):
        d[name] = tree(env.runs / name)
    d["files"] = {n: (env.runs / n).read_bytes() for n in ("notes.txt", "source.mp4", "weights.pth")}
    return d


def decoys_intact(env, d):
    assert tree(env.runs / "run_0123456789abcdef") == d["run_0123456789abcdef"] and tree(env.runs / "review") == d["review"]
    assert {n: (env.runs / n).read_bytes() for n in d["files"]} == d["files"]


def listing(env):
    return {s["session_id"] for s in env.c.get("/api/sessions").json()}


# ------------------------------------------------------------------ delete one / selected / all
def test_delete_one_removes_only_that_session_and_reports_truthfully(mk):
    env = mk(scene(person=range(0, 40)))
    a, b, c = distinct(env, 3)
    dec = decoys(env)
    keep = {b: tree(env.runs / b), c: tree(env.runs / c)}
    r = env.c.post("/api/sessions/delete", json={"ids": [a], "expected_count": 1, **OK})
    assert r.status_code == 200 and r.json()["deleted"] == 1 and r.json()["results"][0]["status"] == "deleted" and r.json()["results"][0]["files"] > 0
    assert not (env.runs / a).exists() and listing(env) == {b, c}
    assert tree(env.runs / b) == keep[b] and tree(env.runs / c) == keep[c]
    decoys_intact(env, dec)
    assert env.c.get(f"/api/sessions/{a}").status_code == 404 and not [p for p in env.runs.iterdir() if p.name.startswith("_tmp_")]


def test_delete_selected_and_delete_all_touch_only_webcam_sessions(mk):
    env = mk(scene(person=range(0, 40)))
    ids = distinct(env, 4)
    dec = decoys(env)
    r = env.c.post("/api/sessions/delete", json={"ids": ids[:2], "expected_count": 2, **OK})
    assert r.json()["deleted"] == 2 and listing(env) == set(ids[2:])
    r = env.c.post("/api/sessions/delete", json={"all": True, "expected_count": 2, **OK})
    assert r.status_code == 200 and r.json()["deleted"] == 2 and listing(env) == set()
    decoys_intact(env, dec)
    assert not [p for p in env.runs.iterdir() if p.name.startswith(("session_", "_tmp_"))]
    assert sorted(image_like_files(env.runs)) == ["run_0123456789abcdef/frames/000001.jpg", "source.mp4"]     # only the decoys remain: nothing session-owned does


def test_confirmation_count_and_shape_are_mandatory(mk):
    env = mk(scene(person=range(0, 40)))
    ids = distinct(env, 2)
    before = {i: tree(env.runs / i) for i in ids}
    for body in ({"ids": ids, "expected_count": 2}, {"ids": ids, "expected_count": 2, "confirm": "eliminar"}, {"ids": ids, "expected_count": 2, "confirm": "yes"},
                 {"ids": ids, "all": True, "expected_count": 2, **OK}, {"expected_count": 2, **OK}, {"ids": ids, "expected_count": -1, **OK}):
        assert env.c.post("/api/sessions/delete", json=body).status_code == 422, body
    r = env.c.post("/api/sessions/delete", json={"ids": ids, "expected_count": 3, **OK})                 # the user saw 3, the request names 2: refused
    assert r.status_code == 409 and r.json()["detail"]["msg_id"] == "mg.refusal.delete_count_changed"
    r = env.c.post("/api/sessions/delete", json={"all": True, "expected_count": 1, **OK})                # a new session appeared / disappeared since the dialog
    assert r.status_code == 409 and r.json()["detail"]["now"] == 2
    assert {i: tree(env.runs / i) for i in ids} == before


# ------------------------------------------------------------------ safety: traversal, symlinks, ambiguity
@pytest.mark.parametrize("bad", ["../x", "session_0123456789abcdef/..", "/etc/passwd", "session_ZZZZZZZZZZZZZZZZ", "run_0123456789abcdef", "review", "session_*", "", "..", "session_0123456789abcdef/frames",
                                 "session_0123456789abcdef\x00", "session_0123456789abcdef\n"])
def test_traversal_and_wrong_ids_are_refused_without_touching_anything(mk, bad):
    env = mk(scene(person=range(0, 40)))
    ids = distinct(env, 1)
    dec = decoys(env)
    before = tree(env.runs / ids[0])
    r = env.c.post("/api/sessions/delete", json={"ids": [bad], "expected_count": 1, **OK})
    assert r.status_code == 200 and r.json()["deleted"] == 0 and r.json()["results"][0]["status"] in ("refused", "missing")
    assert tree(env.runs / ids[0]) == before
    decoys_intact(env, dec)


def test_a_symlinked_session_directory_is_refused_and_its_target_survives(mk, tmp_path):
    env = mk(scene(person=range(0, 40)))
    target = tmp_path / "elsewhere"
    (target / "frames").mkdir(parents=True)
    (target / "frames" / "keep.jpg").write_bytes(b"precious")
    link = env.runs / "session_aaaaaaaaaaaaaaaa"
    link.symlink_to(target, target_is_directory=True)
    r = env.c.post("/api/sessions/delete", json={"ids": ["session_aaaaaaaaaaaaaaaa"], "expected_count": 1, **OK})
    assert r.json()["results"][0]["status"] == "refused" and r.json()["results"][0]["reason"] == "symlink_or_not_a_directory"
    assert (target / "frames" / "keep.jpg").read_bytes() == b"precious" and link.is_symlink()
    r = env.c.post("/api/sessions/delete", json={"all": True, "expected_count": 1, **OK})                      # "all" does not follow it either
    assert r.json()["deleted"] == 0 and (target / "frames" / "keep.jpg").exists()


def test_a_symlink_inside_a_session_makes_it_ambiguous_and_nothing_is_deleted(mk, tmp_path):
    env = mk(scene(person=range(0, 40)))
    (sid,) = distinct(env, 1)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "important.txt").write_text("keep")
    (env.runs / sid / "risk" / "link").symlink_to(outside, target_is_directory=True)
    before = {p: (env.runs / sid / p).stat().st_size for p in ("observations.json", "export.json")}
    r = env.c.post("/api/sessions/delete", json={"ids": [sid], "expected_count": 1, **OK})
    assert r.json()["results"][0]["status"] == "refused" and r.json()["results"][0]["reason"] == "contains_a_symlink"
    assert (outside / "important.txt").read_text() == "keep" and before == {p: (env.runs / sid / p).stat().st_size for p in before}


def test_a_directory_that_merely_looks_like_a_session_is_refused(mk):
    env = mk(scene(person=range(0, 40)))
    fake = env.runs / "session_bbbbbbbbbbbbbbbb"
    fake.mkdir()
    (fake / "user_notes.txt").write_text("not ours")
    (env.runs / "session_cccccccccccccccc").mkdir()
    (env.runs / "session_cccccccccccccccc" / "source.json").write_text('{"kind": "file"}')
    (env.runs / "session_cccccccccccccccc" / "export.json").write_text("{}")
    (env.runs / "session_cccccccccccccccc" / "observations.json").write_text("{}")
    r = env.c.post("/api/sessions/delete", json={"ids": ["session_bbbbbbbbbbbbbbbb", "session_cccccccccccccccc"], "expected_count": 2, **OK})
    assert [x["reason"] for x in r.json()["results"]] == ["unreadable_session_record", "not_a_webcam_session"] and r.json()["deleted"] == 0
    assert (fake / "user_notes.txt").exists()


# ------------------------------------------------------------------ active sessions, honest reporting, bookkeeping
def test_a_running_session_is_untouched_and_a_finalizing_one_blocks_deletion(mk):
    env = mk(scene(person=range(0, 40)))
    (old,) = distinct(env, 1)
    sid = start_default(env.c, privacy="off").json()["session_id"]                                    # running now
    post(env.c, sid, 0)
    wait_decided(env.c, sid, 1)
    r = env.c.post("/api/sessions/delete", json={"all": True, "expected_count": 1, **OK})
    assert r.json()["deleted"] == 1 and not (env.runs / old).exists()
    assert env.c.get(f"/api/webcam/sessions/{sid}").json()["state"] == "running" and len([p for p in image_like_files(env.runs) if p.startswith("_tmp_")]) == 1   # the live (Privacy OFF) session's staging frame is untouched
    sess = env.app.state.st.session_mgr.sessions[sid]
    sess.state = "finalizing"
    try:
        r2 = env.c.post("/api/sessions/delete", json={"all": True, "expected_count": 0, **OK})
        assert r2.status_code == 409 and r2.json()["detail"]["msg_id"] == "mg.refusal.session_in_progress"
    finally:
        sess.state = "running"
    assert env.c.post(f"/api/webcam/sessions/{sid}/stop").status_code == 200


def test_a_failed_removal_is_reported_as_failed_not_as_deleted(mk, monkeypatch):
    env = mk(scene(person=range(0, 40)))
    (sid,) = distinct(env, 1)
    monkeypatch.setattr(shutil, "rmtree", lambda *a, **k: None)      # the removal silently does nothing
    r = env.c.post("/api/sessions/delete", json={"ids": [sid], "expected_count": 1, **OK}).json()
    assert r["deleted"] == 0 and r["results"][0]["status"] == "failed" and r["results"][0]["reason"].startswith("residue_remains_in__tmp_")


def test_deleting_a_session_forgets_its_trust_and_leaves_no_reference(mk):
    env = mk(scene(person=range(3, 32)))
    ids = distinct(env, 2)
    st = env.app.state.st
    assert env.c.get(f"/api/sessions/{ids[0]}").json()["trust"] == "session" and ids[0] in st.trusted
    env.c.post("/api/sessions/delete", json={"ids": [ids[0]], "expected_count": 1, **OK})
    assert ids[0] not in st.trusted and env.c.get(f"/api/sessions/{ids[0]}/risk").status_code == 404
    assert all(ids[0] not in p.read_text(errors="ignore") for p in env.runs.rglob("*.json"))


def test_listing_reports_retention_size_and_source_without_reading_images(mk):
    env = mk(scene(person=range(0, 40)))
    on, off = distinct(env, 2)
    rows = {s["session_id"]: s for s in env.c.get("/api/sessions").json()}
    assert {rows[on]["retention"], rows[off]["retention"]} == {"metadata_only", "frames"}
    assert all(r["source_origin"] == "test_source" and isinstance(r["bytes"], int) and r["bytes"] > 0 for r in rows.values())
    assert next(r for r in rows.values() if r["retention"] == "frames")["bytes"] > next(r for r in rows.values() if r["retention"] == "metadata_only")["bytes"]


def test_unauthenticated_deletion_is_refused(mk):
    from fastapi.testclient import TestClient
    env = mk(scene())
    anon = TestClient(env.app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000))
    assert anon.post("/api/sessions/delete", json={"all": True, "expected_count": 0, **OK}).status_code == 401
