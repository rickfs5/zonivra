"""MVP 1 privacy and deletion (P0-A/B/C). Everything runs against isolated synthetic storage: no personal image is read, copied or deleted, and no real server is contacted."""
import hashlib
import json
import os
import shutil
import time
from pathlib import Path

import pytest

from conftest import VIDEO, needs_video
from mineguard import webcam
from test_vs02_webcam_risk import ZONE, W, H, jpeg, mk, post, real_frames, scene, wait_decided  # noqa: F401  (fixtures and helpers of the webcam suite)

pytestmark = pytest.mark.usefixtures("legacy_delete_enabled")   # MG-DELETE-035: dormant removal logic, isolated test server only

IMAGE_MAGIC = (b"\xff\xd8\xff", b"\x89PNG", b"GIF8", b"RIFF", b"BM")
B64_JPEG = (b"/9j/", b"iVBOR")


def start_default(c, **extra):
    """Starts a session WITHOUT any privacy field: the default must be Privacy ON."""
    body = {"width": W, "height": H, "interval_ms": 100, "threshold": 0.3, "zones": [ZONE], "capture": {"origin": "test_source", "device_label": "synthetic", "user_agent": "pytest"}} | extra
    return c.post("/api/webcam/sessions", json=body)


def run(env, n=34, privacy=None):
    r = start_default(env.c, **({} if privacy is None else {"privacy": privacy}))
    assert r.status_code == 201, r.text
    sid = r.json()["session_id"]
    for i in range(n):
        assert post(env.c, sid, i).status_code == 202
        wait_decided(env.c, sid, i + 1)
    out = env.c.post(f"/api/webcam/sessions/{sid}/stop")
    assert out.status_code == 200, out.text
    return sid, out.json()["run_id"]


def image_like_files(root: Path):
    hits = []
    for p in root.rglob("*"):
        if p.is_file():
            b = p.read_bytes()
            if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".mp4", ".mov", ".y4m") or b.startswith(IMAGE_MAGIC) or any(m in b for m in B64_JPEG):
                hits.append(str(p.relative_to(root)))
    return hits


def tree(d: Path):
    return {str(p.relative_to(d)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(d.rglob("*")) if p.is_file()}


# ------------------------------------------------------------------ P0-A: Privacy ON is the default and persists no image
def test_default_is_privacy_on_and_no_image_is_ever_written(mk):
    env = mk(scene(person=range(3, 32)))
    r = start_default(env.c)
    sid = r.json()["session_id"]
    assert env.c.get(f"/api/webcam/sessions/{sid}").json()["retention"] == "metadata_only"
    for i in range(12):
        post(env.c, sid, i)
        wait_decided(env.c, sid, i + 1)
    assert image_like_files(env.tmp) == []                                    # not while the session runs (staging dir, queue spill, anything under the test storage)
    run_id = env.c.post(f"/api/webcam/sessions/{sid}/stop").json()["run_id"]
    d = env.runs / run_id
    assert image_like_files(env.tmp) == [] and not (d / "frames").exists()    # nor after it is stored
    b = env.c.get(f"/api/sessions/{run_id}").json()
    assert b["retention"] == "metadata_only" and b["manifest"]["evidence"] == [] and b["manifest"]["retention"] == {"mode": "metadata_only", "frames_persisted": False}
    assert b["source"]["retention"]["mode"] == "metadata_only" and all(s["jpeg_sha256"] is None for s in b["source"]["samples"])
    listing = {s["session_id"]: s for s in env.c.get("/api/sessions").json()}[run_id]
    assert listing["retention"] == "metadata_only"


def test_privacy_value_is_strict_and_off_must_be_asked_explicitly(mk):
    env = mk(scene())
    for bad in ("OFF", "maybe", "", None, False, 0, "false"):
        assert start_default(env.c, privacy=bad).status_code == 422, bad
    sid = start_default(env.c, privacy="off").json()["session_id"]
    assert env.c.get(f"/api/webcam/sessions/{sid}").json()["retention"] == "frames"


def test_detection_zones_and_risk_still_work_without_images(mk):
    env = mk(scene(person=range(3, 32)))
    _, run_id = run(env)
    o = env.c.get(f"/api/sessions/{run_id}").json()["observation_set"]
    assert len(o["frames"]) == 34 and any(f["objects"] for f in o["frames"]) and o["origin"] == "model_inference"
    z = env.c.get(f"/api/sessions/{run_id}/risk").json()["evaluation"]["zones"]["Z1"]
    assert [e["severity"] for e in z["events"]] == ["YELLOW"] and env.c.get(f"/api/sessions/{run_id}/risk").json()["evaluation"]["evidence_refs"][z["events"][0]["event_id"]] == {}
    assert env.c.get(f"/api/sessions/{run_id}/frames/10").status_code == 410     # explained, not a silent 404
    x = env.c.get(f"/api/sessions/{run_id}/risk/export").json()
    assert x["bundle"]["manifest"]["evidence"] == [] and x["bundle"]["provenance"]["model"]["weights_sha256"]


def test_retention_is_fixed_per_session_and_the_next_session_has_its_own(mk):
    env = mk(scene(person=range(3, 12)))
    _, a = run(env, 8)                       # default: ON
    _, b = run(env, 8, privacy="off")        # explicit OFF
    _, c = run(env, 8)                       # back to ON: nothing sticky
    ret = {s["session_id"]: s["retention"] for s in env.c.get("/api/sessions").json()}
    assert ret[a] == "metadata_only" and ret[b] == "frames" and ret[c] == "metadata_only"
    assert not (env.runs / a / "frames").exists() and len(list((env.runs / b / "frames").glob("*.jpg"))) == 8 and not (env.runs / c / "frames").exists()


def test_privacy_off_keeps_frames_evidence_and_deep_verification(mk):
    env = mk(scene(person=range(3, 32)))
    _, run_id = run(env, privacy="off")
    assert len(list((env.runs / run_id / "frames").glob("*.jpg"))) == 34
    rep = env.c.get(f"/api/sessions/{run_id}/verify").json()
    assert rep["ok"] and rep["deep"] and rep["visual_verification"] == "performed" and env.c.get(f"/api/sessions/{run_id}").json()["trust"] == "verified"
    assert env.c.get(f"/api/sessions/{run_id}/frames/10").status_code == 200


# ------------------------------------------------------------------ P0-C: verification never claims what was discarded
def test_privacy_on_verification_is_metadata_integrity_only_and_never_verified(mk):
    env = mk(scene(person=range(3, 32)))
    _, run_id = run(env)
    assert env.c.get(f"/api/sessions/{run_id}").json()["trust"] == "session"
    rep = env.c.get(f"/api/sessions/{run_id}/verify").json()               # a DEEP request
    ids = {c["id"]: c["ok"] for c in rep["checks"]}
    assert rep["ok"] and rep["deep"] is False and rep["visual_verification"] == "unavailable_no_frames" and rep["retention"] == "metadata_only"
    assert rep["trust_scope"] == {"local_integrity": True, "inference_reproduced_locally": False, "external_authenticity": False}
    assert ids["no_frames_persisted_on_disk"] and ids["deep_visual_verification_unavailable"] and "executing_weights_file_rehashed_and_equal" not in ids
    assert env.c.get(f"/api/sessions/{run_id}").json()["trust"] == "session"      # never promoted to "verified"
    fresh_runs = {s["session_id"]: s["trust"] for s in env.c.get("/api/sessions").json()}
    assert fresh_runs[run_id] == "session"


def test_privacy_on_stored_session_after_restart_is_unverified_and_stays_so(mk):
    from mineguard.app import create_app
    from conftest import local_client
    env = mk(scene(person=range(3, 12)))
    _, run_id = run(env, 8)
    fresh = local_client(create_app(asset_dir=env.tmp / "assets", runs_dir=env.runs, detector_factory=lambda: env.det))
    assert fresh.get(f"/api/sessions/{run_id}").json()["trust"] == "unverified"
    rep = fresh.get(f"/api/sessions/{run_id}/verify").json()
    assert rep["ok"] and rep["visual_verification"] == "unavailable_no_frames" and fresh.get(f"/api/sessions/{run_id}").json()["trust"] == "unverified"


@pytest.mark.parametrize("name,edit", [
    ("observations", lambda d: d["frames"][0]["objects"].append(d["frames"][0]["objects"][0]) if d["frames"][0]["objects"] else d["frames"].pop()),
])
def test_privacy_on_metadata_tampering_is_detected_and_revokes(mk, name, edit):
    env = mk(scene(person=range(0, 40)))
    _, run_id = run(env, 8)
    p = env.runs / run_id / "observations.json"
    doc = json.loads(p.read_text())
    edit(doc)
    p.write_text(json.dumps(doc, indent=1))
    assert env.c.get(f"/api/sessions/{run_id}").json()["trust"] == "unverified"
    assert not env.c.get(f"/api/sessions/{run_id}/verify").json()["ok"]


def test_an_image_planted_in_a_privacy_on_session_is_flagged(mk):
    env = mk(scene(person=range(3, 12)))
    _, run_id = run(env, 8)
    (env.runs / run_id / "frames").mkdir()
    (env.runs / run_id / "frames" / "000001.jpg").write_bytes(jpeg(1))
    rep = env.c.get(f"/api/sessions/{run_id}/verify?deep=false").json()
    assert not rep["ok"] and not next(c for c in rep["checks"] if c["id"] == "no_frames_persisted_on_disk")["ok"]


@needs_video
def test_real_model_privacy_on_detects_without_persisting_any_pixel(detector, tmp_path):
    import io
    from mineguard.app import create_app
    from conftest import local_client
    app = create_app(asset_dir=tmp_path / "assets", runs_dir=tmp_path / "runs", detector_factory=lambda: detector)
    c = local_client(app)
    sid = c.post("/api/webcam/sessions", json={"width": 640, "height": 360, "interval_ms": 300, "threshold": 0.3, "capture": {"origin": "test_source", "device_label": "clip frames"}}).json()["session_id"]
    for i, b in enumerate(real_frames(8)):
        assert c.post(f"/api/webcam/sessions/{sid}/frames", content=b, headers={"X-MG-Seq": str(i), "X-MG-Capture-Us": str(i * 300_000)}).status_code == 202
        wait_decided(c, sid, i + 1, timeout=60)
    run_id = c.post(f"/api/webcam/sessions/{sid}/stop").json()["run_id"]
    o = c.get(f"/api/sessions/{run_id}").json()["observation_set"]
    assert any(x["cls"]["label"] == "vehicle" for f in o["frames"] for x in f["objects"])           # genuine inference on the real clip frames
    assert image_like_files(tmp_path) == []                                                       # and nothing image-like anywhere under the test storage


# ------------------------------------------------------------------ delete retained media (Privacy OFF)
def test_delete_media_removes_images_keeps_metadata_and_is_reported_honestly(mk):
    env = mk(scene(person=range(3, 32)))
    _, run_id = run(env, privacy="off")
    assert env.c.post(f"/api/sessions/{run_id}/media/delete", json={"confirm": "no", "expected_files": 34}).status_code == 422
    assert len(list((env.runs / run_id / "frames").glob("*.jpg"))) == 34
    r = env.c.post(f"/api/sessions/{run_id}/media/delete", json={"confirm": "ELIMINAR", "expected_files": 34})
    assert r.status_code == 200 and r.json()["files_deleted"] == 34 and r.json()["retention"] == "media_deleted"
    d = env.runs / run_id
    assert not (d / "frames").exists() and image_like_files(d) == [] and (d / "media_deleted.json").is_file() and (d / "observations.json").is_file()
    b = env.c.get(f"/api/sessions/{run_id}").json()
    assert b["retention"] == "media_deleted" and b["trust"] == "unverified" and env.c.get(f"/api/sessions/{run_id}/frames/10").status_code == 410
    rep = env.c.get(f"/api/sessions/{run_id}/verify").json()
    assert rep["ok"] and rep["visual_verification"] == "unavailable_no_frames" and rep["retention"] == "media_deleted"
    assert env.c.post(f"/api/sessions/{run_id}/media/delete", json={"confirm": "ELIMINAR", "expected_files": 34}).status_code == 409           # nothing left to delete


def test_delete_media_refuses_privacy_on_sessions(mk):
    env = mk(scene(person=range(3, 12)))
    _, run_id = run(env, 8)
    assert env.c.post(f"/api/sessions/{run_id}/media/delete", json={"confirm": "ELIMINAR", "expected_files": 34}).status_code == 409


# ------------------------------------------------------------------ read-only aggregation views
def test_events_and_overview_label_real_and_synthetic_and_never_mix_counts(mk):
    env = mk(scene(person=range(3, 32), vehicle=range(15, 32)))
    _, a = run(env)
    ev = env.c.get("/api/events").json()
    assert ev["total"] >= 1 and all(e["source_id"] == a and e["synthetic"] is False and e["origin"] == "model_inference" for e in ev["events"])
    assert env.c.get("/api/events?severity=RED").json()["events"] and all(e["severity"] == "RED" for e in env.c.get("/api/events?severity=RED").json()["events"])
    assert env.c.get("/api/events?source=session_ffffffffffffffff").json()["events"] == []
    assert env.c.get("/api/events?severity=BLUE").status_code == 422 and env.c.get("/api/events?limit=0").status_code == 422
    o = env.c.get("/api/overview").json()
    assert o["privacy_default"] == "on" and o["sources"]["sessions"] == 1 and o["events"]["synthetic"] == {"RED": 0, "YELLOW": 0} and o["events"]["real"]["RED"] >= 1
    assert o["active_session"] is None


def test_overview_shows_the_active_session_retention(mk):
    env = mk(scene(person=range(0, 40)))
    sid = start_default(env.c).json()["session_id"]
    o = env.c.get("/api/overview").json()
    assert o["active_session"]["session_id"] == sid and o["active_session"]["retention"] == "metadata_only"
    env.c.post(f"/api/webcam/sessions/{sid}/stop")
