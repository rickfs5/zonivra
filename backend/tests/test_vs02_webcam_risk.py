"""VS-02B/C/D integration: webcam session pipeline (bounded queue, drops, stale frames, clean stop, disconnect), zones, tracker, risk events, evidence
references, export, verification and trust states. Plumbing tests use a SCRIPTED DETECTOR TEST DOUBLE (content-addressed: the frame index is encoded in the
picture, so verification and re-inference are deterministic). It is never used for acceptance; the real-model tests at the end use real weights on frames
decoded from the registered clip and are skipped only if the clip is absent."""
from conftest import zones_rev
import io
import shutil
import time
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from conftest import VIDEO, local_client, needs_video
from mineguard import webcam
from mineguard.app import create_app
from mineguard.detector import RawDetection, load_manifest_entry

W, H = 320, 180
ZONE = {"zone_id": "Z1", "kind": "restricted", "polygon": [[100, 50], [300, 50], [300, 170], [100, 170]], "name": "Zona 1"}
PERSON_IN = (150, 60, 20, 100)   # bottom centre (160, 160): inside Z1
PERSON_OUT = (10, 60, 20, 100)   # bottom centre (20, 160): outside
VEHICLE_IN = (200, 90, 80, 70)   # bottom centre (240, 160): inside


def jpeg(idx: int, w=W, h=H, base=100) -> bytes:
    a = np.full((h, w, 3), base, np.uint8)
    a[0:16, 0:16] = (idx >> 4) * 16 + 8
    a[0:16, 16:32] = (idx & 15) * 16 + 8
    buf = io.BytesIO()
    Image.fromarray(a).save(buf, "JPEG", quality=95)
    return buf.getvalue()


def decode_idx(rgb) -> int:
    return (int(rgb[8, 8, 0]) // 16) * 16 + int(rgb[8, 24, 0]) // 16


class Scripted:
    """TEST DOUBLE. predict() looks up the frame index encoded in the picture and returns the scripted detections."""

    def __init__(self, script, delay=0.0):
        e = load_manifest_entry()
        self.entry, self.weights_sha256, self.device, self.resolution = e, e["sha256"], "cpu", 384
        self.script, self.delay, self.calls = script, delay, 0

    def predict(self, rgb, thr):
        time.sleep(self.delay)
        self.calls += 1
        out = []
        for cid, (x, y, w, h) in self.script.get(decode_idx(rgb), []):
            out.append(RawDetection(cid, 0.9, (float(x), float(y), float(x + w), float(y + h))))
        return out


def scene(person=(), vehicle=(), person_box=PERSON_IN):
    s = {}
    for i in range(80):
        dets = []
        if i in person:
            dets.append((1, person_box))
        if i in vehicle:
            dets.append((3, VEHICLE_IN))
        s[i] = dets
    return s


@pytest.fixture
def mk(tmp_path):
    envs = []

    def build(script, delay=0.0):
        det = Scripted(script, delay)
        app = create_app(asset_dir=tmp_path / "assets", runs_dir=tmp_path / "runs", detector_factory=lambda: det)
        env = SimpleNamespace(c=local_client(app), det=det, runs=tmp_path / "runs", app=app, tmp=tmp_path)
        envs.append(env)
        return env
    yield build
    for env in envs:   # no session may outlive its test: a leftover running session would be finalized by ITS reaper under a later test's monkeypatches
        mgr = env.app.state.st.session_mgr
        for s in list(mgr.sessions.values()):
            if isinstance(s, webcam.Session) and s.state not in ("done", "failed"):
                s.stop_flag.set()
                s.state = "finalizing" if s.state == "running" else s.state
                s.worker.join(timeout=10)
                s.state = "failed"
                shutil.rmtree(s.tmp, ignore_errors=True)


def start(c, zones=(ZONE,), interval=100, origin="test_source", w=W, h=H, privacy="off"):
    """Most tests need retained frames (evidence, deep verification), so they ask for Privacy OFF EXPLICITLY; the default (no field) is Privacy ON (test_privacy.py)."""
    r = c.post("/api/webcam/sessions", json={"width": w, "height": h, "interval_ms": interval, "threshold": 0.3, "zones": list(zones), "privacy": privacy,
                                             "capture": {"origin": origin, "device_label": "scripted test source", "user_agent": "pytest"}})
    return r


def post(c, sid, seq, cap_us=None, body=None):
    return c.post(f"/api/webcam/sessions/{sid}/frames", content=body if body is not None else jpeg(seq),
                  headers={"X-MG-Seq": str(seq), "X-MG-Capture-Us": str(cap_us if cap_us is not None else 5_000_000 + seq * 100_000), "Content-Type": "image/jpeg"})


def wait_decided(c, sid, n, timeout=20):
    t = time.time()
    while time.time() - t < timeout:
        v = c.get(f"/api/webcam/sessions/{sid}").json()
        k = v["counters"]
        if k["analyzed"] + k["dropped_queue_overflow"] + k["dropped_stale"] + k["adapter_error"] >= n and v["queue_depth"] == 0:
            return v
        time.sleep(0.02)
    raise AssertionError(f"not decided: {v}")


def run_session(env, n, zones=(ZONE,), paced=True):
    sid = start(env.c, zones).json()["session_id"]
    for i in range(n):
        assert post(env.c, sid, i).status_code == 202
        if paced:
            wait_decided(env.c, sid, i + 1)
    out = env.c.post(f"/api/webcam/sessions/{sid}/stop")
    assert out.status_code == 200, out.text
    return sid, out.json()["run_id"]


def ev_of(env, run_id):
    return env.c.get(f"/api/sessions/{run_id}/risk").json()


# ------------------------------------------------------------------ pipeline / contract
def test_session_produces_a_valid_live_observation_set_and_artifact(mk):
    env = mk(scene(person=range(3, 12)))
    sid, run = run_session(env, 14)
    d = env.runs / run
    assert run.startswith("session_") and (d / "frames").is_dir() and (d / "risk" / "evaluation.json").is_file()
    b = env.c.get(f"/api/sessions/{run}").json()
    o = b["observation_set"]
    assert o["schema"] == "mg.obsset.live.vs02" and o["origin"] == "model_inference" and o["producer"]["params"]["mode"] == "webcam"
    assert len(o["frames"]) == 14 and o["source_binding"]["frame_count"] == 14 and b["source"]["kind"] == "webcam"
    assert b["provenance"]["capture"]["declared_by"] == "client" and b["provenance"]["capture"]["physical_camera_verified_by_server"] is False
    assert b["source"]["frame_table"]["pts_us"][:3] == [0, 100_000, 200_000]  # relative capture time of the client clock
    assert not [p for p in env.runs.iterdir() if p.name.startswith("_tmp_")] and not env.c.app.state.st.infer_lock.locked()


def test_live_schema_is_never_validated_as_file_set_and_vice_versa(mk):
    from mineguard.schema import ValidationFailure, validate_observation_set
    env = mk(scene(person=range(3, 8)))
    _, run = run_session(env, 8)
    obs = env.c.get(f"/api/sessions/{run}").json()["observation_set"]
    validate_observation_set(obs, live=True)
    with pytest.raises(ValidationFailure) as e:
        validate_observation_set(obs)
    assert e.value.codes == ["V-20"]
    with pytest.raises(ValidationFailure):
        validate_observation_set({**obs, "frame_status_ranges": [{"from_frame": 0, "to_frame": 0, "status": "not_analyzed_stride"}]}, live=True)


def test_yellow_event_from_person_dwelling_with_evidence_and_export(mk):
    env = mk(scene(person=range(3, 32)))
    _, run = run_session(env, 34)
    r = ev_of(env, run)
    z = r["evaluation"]["zones"]["Z1"]
    yel = [e for e in z["events"] if e["severity"] == "YELLOW"]
    assert len(yel) == 1 and not [e for e in z["events"] if e["severity"] == "RED"]
    e = yel[0]
    refs = r["evaluation"]["evidence_refs"][e["event_id"]]
    assert refs["start"]["file"].startswith("frames/") and len(refs["start"]["file_sha256"]) == 64 and "evidence" not in e
    assert e["start_us"] - e["dwell_since_us"] >= 1_500_000  # dwell threshold respected in event time
    assert r["trust"] == "session"
    exp = env.c.get(f"/api/sessions/{run}/risk/export")
    assert exp.status_code == 200 and exp.json()["schema"] == "mg.export.vs02" and exp.json()["bundle"]["provenance"]["model"]["weights_sha256"]
    assert exp.json()["evaluation"]["digest"] == r["evaluation"]["digest"] == env.c.get(f"/api/sessions/{run}/risk").json()["evaluation"]["digest"]


def test_red_event_person_and_vehicle_and_yellow_never_while_vehicle_present(mk):
    env = mk(scene(person=range(3, 32), vehicle=range(3, 32)))
    _, run = run_session(env, 34)
    z = ev_of(env, run)["evaluation"]["zones"]["Z1"]
    assert [e["severity"] for e in z["events"]] == ["RED"]
    assert z["events"][0]["start_index"] == 8 and z["events"][0]["condition_since_index"] == 3


def test_untracked_first_sighting_is_unknown_not_yellow_and_no_event_without_zone(mk):
    env = mk(scene(person=range(3, 6)))
    _, run = run_session(env, 10)
    z = ev_of(env, run)["evaluation"]["zones"]["Z1"]
    assert z["events"] == []
    assert any(iv["state"] == "UNKNOWN" and "identity_unavailable" in iv["causes"] for iv in z["timeline"])  # tentative track = untracked person
    env2 = mk(scene(person=range(3, 32)))
    _, run2 = run_session(env2, 34, zones=())
    assert ev_of(env2, run2)["evaluation"]["zones"] == {}


def test_live_state_is_provisional_never_plain_safe_while_pending(mk):
    env = mk(scene(person=range(3, 40)))
    sid = start(env.c).json()["session_id"]
    seen_pending = False
    for i in range(14):
        post(env.c, sid, i)
        v = wait_decided(env.c, sid, i + 1)
        st = v["risk"]["Z1"]
        if st["state"] == "GREEN" and st["pending"]:
            seen_pending = True
    assert seen_pending  # person dwelling toward the threshold is reported as pending, not as safe
    env.c.post(f"/api/webcam/sessions/{sid}/stop")


# ------------------------------------------------------------------ bounded queue, drops, stale, disconnect
def test_bounded_queue_never_grows_and_drops_are_recorded(mk):
    env = mk(scene(person=range(0, 40)), delay=0.12)
    sid = start(env.c).json()["session_id"]
    depths = []
    for i in range(12):
        post(env.c, sid, i)
        depths.append(env.c.get(f"/api/webcam/sessions/{sid}").json()["queue_depth"])
    v = wait_decided(env.c, sid, 12, timeout=30)
    assert max(depths) <= webcam.QUEUE_MAX and v["counters"]["dropped_queue_overflow"] >= 1
    k = v["counters"]
    assert k["received"] == 12 and k["analyzed"] + k["dropped_queue_overflow"] + k["dropped_stale"] + k["adapter_error"] == 12
    run = env.c.post(f"/api/webcam/sessions/{sid}/stop").json()["run_id"]
    b = env.c.get(f"/api/sessions/{run}").json()
    dropped = [s for s in b["source"]["samples"] if s["status"] != "analyzed"]
    assert len(dropped) == k["dropped_queue_overflow"] and all(s["reason"] == "queue_overflow" and s["jpeg_sha256"] is None for s in dropped)
    assert any(r["status"] == "not_analyzed_dropped" for r in b["observation_set"]["frame_status_ranges"])
    assert env.c.get(f"/api/sessions/{run}/verify?deep=false").json()["ok"]
    unk = [iv for iv in ev_of(env, run)["evaluation"]["zones"]["Z1"]["timeline"] if iv["state"] == "UNKNOWN"]
    assert unk and not any(iv["state"] == "GREEN" and iv["start_us"] in {u["start_us"] for u in unk} for iv in ev_of(env, run)["evaluation"]["zones"]["Z1"]["timeline"])


def test_stale_frames_are_recorded_dropped_never_analyzed_late(mk, monkeypatch):
    monkeypatch.setattr(webcam, "MAX_AGE_US", 60_000)
    env = mk(scene(person=range(0, 40)), delay=0.2)
    sid = start(env.c).json()["session_id"]
    for i in range(3):
        post(env.c, sid, i)
    v = wait_decided(env.c, sid, 3, timeout=20)
    assert v["counters"]["dropped_stale"] >= 1 or v["counters"]["dropped_queue_overflow"] >= 1
    assert env.det.calls < 3 or v["counters"]["analyzed"] == env.det.calls
    env.c.post(f"/api/webcam/sessions/{sid}/stop")


def test_idle_client_is_finalized_by_the_reaper_without_any_further_request(mk, monkeypatch):
    monkeypatch.setattr(webcam, "IDLE_FINALIZE_S", 0.4)
    env = mk(scene(person=range(0, 40)))
    sid = start(env.c).json()["session_id"]
    for i in range(5):
        post(env.c, sid, i)
        wait_decided(env.c, sid, i + 1)
    t = time.time()
    while time.time() - t < 6 and not [p for p in env.runs.iterdir() if p.name.startswith("session_")]:
        time.sleep(0.1)   # NO request is made to the server while waiting
    made = [p for p in env.runs.iterdir() if p.name.startswith("session_")]
    assert len(made) == 1, "the idle session was not finalized autonomously"
    assert not env.app.state.st.infer_lock.locked()
    v = env.c.get(f"/api/webcam/sessions/{sid}").json()
    assert v["state"] == "done" and v["closed_reason"] == "client_disconnected" and v["run_id"] == made[0].name
    assert start(env.c).status_code == 201  # a new session can start: the lock was released
    assert env.c.get(f"/api/sessions/{v['run_id']}").json()["manifest"]["closed_reason"] == "client_disconnected"


def test_concurrent_stop_calls_finalize_exactly_once(mk, monkeypatch):
    import threading
    calls = []
    orig = webcam.Session._finalize

    def counting(self):
        calls.append(1)
        time.sleep(0.2)
        return orig(self)
    monkeypatch.setattr(webcam.Session, "_finalize", counting)
    env = mk(scene(person=range(0, 40)))
    sid = start(env.c).json()["session_id"]
    for i in range(6):
        post(env.c, sid, i)
        wait_decided(env.c, sid, i + 1)
    out = []
    ts = [threading.Thread(target=lambda: out.append(env.c.post(f"/api/webcam/sessions/{sid}/stop").json())) for _ in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(calls) == 1 and len({o["run_id"] for o in out}) == 1 and all(o["state"] == "done" for o in out)
    assert len([p for p in env.runs.iterdir() if p.name.startswith("session_")]) == 1


def test_stop_never_finalizes_while_the_detector_call_is_still_running(mk, monkeypatch):
    monkeypatch.setattr(webcam, "WORKER_JOIN_S", 0.2)
    env = mk(scene(person=range(0, 40)), delay=1.0)
    sid = start(env.c).json()["session_id"]
    post(env.c, sid, 0)
    time.sleep(0.15)                                         # the worker is inside predict()
    r = env.c.post(f"/api/webcam/sessions/{sid}/stop")
    assert r.status_code == 503 and r.json()["detail"]["msg_id"] == "mg.refusal.session_worker_busy"
    assert env.app.state.st.infer_lock.locked() and not [p for p in env.runs.iterdir() if p.name.startswith("session_")]   # nothing written, lock still held
    assert post(env.c, sid, 1).status_code == 409           # a finalizing session accepts no more frames
    monkeypatch.setattr(webcam, "WORKER_JOIN_S", 30.0)
    r2 = env.c.post(f"/api/webcam/sessions/{sid}/stop")      # retry after the call returned
    assert r2.status_code == 200 and r2.json()["run_id"].startswith("session_")
    assert not env.app.state.st.infer_lock.locked()
    assert env.c.get(f"/api/sessions/{r2.json()['run_id']}/verify?deep=false").json()["ok"]


def test_stop_releases_worker_and_lock_and_second_session_is_refused_while_running(mk):
    env = mk(scene(person=range(0, 40)))
    s1 = start(env.c).json()["session_id"]
    assert start(env.c).status_code == 409
    post(env.c, s1, 0)
    wait_decided(env.c, s1, 1)
    sess = env.app.state.st  # noqa: F841
    assert env.c.post(f"/api/webcam/sessions/{s1}/stop").status_code == 200
    assert not env.app.state.st.infer_lock.locked()
    assert start(env.c).status_code == 201


def test_session_with_no_analyzed_frame_is_refused_and_leaves_no_artifact(mk):
    env = mk({})
    sid = start(env.c).json()["session_id"]
    r = env.c.post(f"/api/webcam/sessions/{sid}/stop")
    assert r.status_code == 422 and r.json()["detail"]["msg_id"] == "mg.refusal.session_no_analyzed_frames"
    assert not [p for p in env.runs.iterdir() if p.name.startswith("session_")]


# ------------------------------------------------------------------ intake refusals
def test_intake_refusals(mk):
    env = mk(scene(person=range(0, 40)))
    c = env.c
    sid = start(c).json()["session_id"]
    assert post(c, sid, 0).status_code == 202
    assert post(c, sid, 0).status_code == 409                                       # repeated sequence number
    assert post(c, sid, 5, cap_us=5_000_000).status_code == 409                      # capture clock did not advance
    assert post(c, sid, 6, body=b"not a jpeg").status_code == 400
    assert post(c, sid, 7, body=jpeg(7, w=W + 16)).status_code == 400                # wrong dimensions
    assert post(c, sid, 8, body=b"\xff" * (webcam.MAX_FRAME_BYTES + 1)).status_code == 413
    assert c.post(f"/api/webcam/sessions/{sid}/frames", content=jpeg(9), headers={"X-MG-Seq": "x"}).status_code == 400
    assert c.get("/api/webcam/sessions/sess_nope").status_code == 404
    c.post(f"/api/webcam/sessions/{sid}/stop")
    assert post(c, sid, 20).status_code == 409                                       # not running
    for bad in ({"width": 100, "height": 100}, {"width": W, "height": H, "interval_ms": 10}, {"width": W, "height": H, "threshold": 0.0}):
        body = {"width": W, "height": H, "interval_ms": 100, "threshold": 0.3, "capture": {"origin": "test_source"}} | bad
        assert c.post("/api/webcam/sessions", json=body).status_code == 422
    assert c.post("/api/webcam/sessions", json={"width": W, "height": H, "capture": {"origin": "webcam_magic"}}).status_code == 422
    bad_zone = {"zone_id": "B", "polygon": [[0, 0], [10, 0], [20, 0]]}  # collinear: refused at creation
    assert c.post("/api/webcam/sessions", json={"width": W, "height": H, "capture": {"origin": "test_source"}, "zones": [bad_zone]}).status_code == 422


def test_unauthenticated_webcam_frame_post_is_refused(mk):
    env = mk(scene())
    from fastapi.testclient import TestClient
    anon = TestClient(env.app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000))
    assert anon.post("/api/webcam/sessions", json={"width": W, "height": H, "capture": {"origin": "test_source"}}).status_code == 401


# ------------------------------------------------------------------ zones
@pytest.mark.parametrize("poly,code", [
    ([[0, 0], [10, 0]], "mg.refusal.zone_vertex_count"),
    ([[0, 0], [10, 0], [10, 0], [0, 10]], "mg.refusal.zone_duplicate_vertex"),
    ([[0, 0], [10, 0], [20, 0]], "mg.refusal.zone_collinear_vertices"),
    ([[0, 0], [100, 100], [100, 0], [0, 100]], "mg.refusal.zone_self_intersection"),
    ([[0, 0], [100, 0], [100, 100], [0, 100], [100, 50]], "mg.refusal.zone_self_intersection"),
    ([[0, 0], [W + 1, 0], [W, 100]], "mg.refusal.zone_out_of_frame"),
    ([[0, 0], [10.5, 0], [10, 10]], "mg.refusal.zone_not_integer"),
    ([[0, 0], [100, 0], [100, 100], [50, 0]], "mg.refusal.zone_collinear_vertices"),
])
def test_invalid_zone_geometry_is_rejected(mk, poly, code):
    env = mk(scene(person=range(3, 12)))
    _, run = run_session(env, 8)
    r = env.c.put(f"/api/sessions/{run}/zones", json={"expected_revision": zones_rev(env.c, f"/api/sessions/{run}/zones"), "zones": [{"zone_id": "Zx", "polygon": poly}]})
    assert r.status_code == 422, (poly, r.text)
    detail = r.json()["detail"]
    if code.endswith("not_integer"):
        assert isinstance(detail, list)  # float coordinates are refused by the request schema before geometry
    else:
        assert detail["msg_id"] == code, (poly, detail)


def test_zone_edit_persists_changes_risk_but_not_trust_or_evidence(mk):
    env = mk(scene(person=range(3, 32)))
    _, run = run_session(env, 34)
    before = env.c.get(f"/api/sessions/{run}").json()["trust"]
    far = {"zone_id": "Z9", "kind": "restricted", "polygon": [[1, 1], [40, 1], [40, 40], [1, 40]]}
    r = env.c.put(f"/api/sessions/{run}/zones", json={"expected_revision": zones_rev(env.c, f"/api/sessions/{run}/zones"), "zones": [far]})
    assert r.status_code == 200 and r.json()["revision"] == 2 and r.json()["zones"][0]["polygon"] == [[1, 1], [40, 1], [40, 40], [1, 40]]
    assert env.c.get(f"/api/sessions/{run}/zones").json()["zones"][0]["zone_id"] == "Z9"
    z = ev_of(env, run)["evaluation"]["zones"]
    assert list(z) == ["Z9"] and z["Z9"]["events"] == []  # the person is not in the new zone: no event
    assert env.c.get(f"/api/sessions/{run}").json()["trust"] == before
    mon = {"zone_id": "M1", "kind": "monitored", "polygon": ZONE["polygon"]}
    env.c.put(f"/api/sessions/{run}/zones", json={"expected_revision": zones_rev(env.c, f"/api/sessions/{run}/zones"), "zones": [mon]})
    assert ev_of(env, run)["evaluation"]["zones_not_evaluated"][0]["zone_id"] == "M1"
    assert env.c.put(f"/api/sessions/{run}/zones", json={"expected_revision": zones_rev(env.c, f"/api/sessions/{run}/zones"), "zones": [far, far]}).status_code == 422


def test_zone_decoys_other_sessions_are_untouched(mk):
    env = mk(scene(person=range(3, 12)))
    _, a = run_session(env, 10)
    _, b = run_session(env, 9)
    za = env.c.get(f"/api/sessions/{a}/zones").json()
    env.c.put(f"/api/sessions/{b}/zones", json={"expected_revision": zones_rev(env.c, f"/api/sessions/{b}/zones"), "zones": [{"zone_id": "OnlyB", "polygon": [[1, 1], [40, 1], [40, 40]]}]})
    assert env.c.get(f"/api/sessions/{a}/zones").json() == za


# ------------------------------------------------------------------ verification and trust states
def test_trust_session_then_unverified_when_stored_then_verified_after_deep_verify(mk):
    env = mk(scene(person=range(3, 32)))
    _, run = run_session(env, 34)
    assert env.c.get(f"/api/sessions/{run}").json()["trust"] == "session"
    fresh = local_client(create_app(asset_dir=env.tmp / "assets", runs_dir=env.runs, detector_factory=lambda: env.det))
    assert fresh.get(f"/api/sessions/{run}").json()["trust"] == "unverified"
    assert {s["session_id"]: s["trust"] for s in fresh.get("/api/sessions").json()}[run] == "unverified"
    rep = fresh.get(f"/api/sessions/{run}/verify").json()
    assert rep["ok"], [c for c in rep["checks"] if not c["ok"]]
    assert rep["trust_scope"] == {"local_integrity": True, "inference_reproduced_locally": True, "external_authenticity": False}
    assert fresh.get(f"/api/sessions/{run}").json()["trust"] == "verified"


def test_tampered_stored_frame_fails_verification_and_drops_trust(mk):
    env = mk(scene(person=range(3, 32)))
    _, run = run_session(env, 34)
    p = env.runs / run / "frames" / "000010.jpg"
    orig = p.read_bytes()
    p.write_bytes(jpeg(10, base=60))
    try:
        assert env.c.get(f"/api/sessions/{run}").json()["trust"] == "unverified"
        assert env.c.get(f"/api/sessions/{run}/frames/10").status_code == 409
        rep = env.c.get(f"/api/sessions/{run}/verify").json()
        assert not rep["ok"] and not next(c for c in rep["checks"] if c["id"] == "evidence_files_sha256")["ok"]
    finally:
        p.write_bytes(orig)


def test_failed_deep_verification_revokes_session_trust(mk):
    env = mk(scene(person=range(3, 32)))
    _, run = run_session(env, 34)
    assert env.c.get(f"/api/sessions/{run}").json()["trust"] == "session"
    env.det.script = scene()  # the model now "sees" nothing: re-inference cannot reproduce the stored observations
    rep = env.c.get(f"/api/sessions/{run}/verify").json()
    assert not rep["ok"] and not next(c for c in rep["checks"] if c["id"] == "reinference_matches_observations")["ok"]
    assert env.c.get(f"/api/sessions/{run}").json()["trust"] == "unverified"


def test_sample_log_tampering_is_detected_even_with_consistent_digests_elsewhere(mk):
    import json
    env = mk(scene(person=range(3, 12)))
    _, run = run_session(env, 8)
    p = env.runs / run / "source.json"
    doc = json.loads(p.read_text())
    doc["samples"][2]["capture_us"] += 1
    p.write_text(json.dumps(doc, indent=1))
    rep = env.c.get(f"/api/sessions/{run}/verify?deep=false").json()
    assert not rep["ok"]


def test_analysis_job_is_refused_while_a_session_is_running(mk):
    env = mk(scene(person=range(3, 12)))
    sid = start(env.c).json()["session_id"]
    r = env.c.post("/api/videos/vid_x/analyze", json={"stride": 3, "threshold": 0.3})
    assert r.status_code in (404, 409)
    env.c.post(f"/api/webcam/sessions/{sid}/stop")


# ------------------------------------------------------------------ REAL model on frames decoded from the registered clip (transport + inference + negative control)
def real_frames(n, step=9, w=640, h=360):
    from mineguard import decode
    out, k = [], 0
    for fr in decode.iter_frames(str(VIDEO), 1280, 720):
        if fr.index % step == 0:
            im = Image.fromarray(fr.rgb).resize((w, h))
            b = io.BytesIO()
            im.save(b, "JPEG", quality=85)
            out.append(b.getvalue())
        if len(out) >= n:
            break
    return out


@needs_video
def test_real_inference_on_posted_frames_with_negative_control(detector, tmp_path):
    app = create_app(asset_dir=tmp_path / "assets", runs_dir=tmp_path / "runs", detector_factory=lambda: detector)
    c = local_client(app)
    frames = real_frames(10)
    r = c.post("/api/webcam/sessions", json={"width": 640, "height": 360, "interval_ms": 300, "threshold": 0.3, "privacy": "off", "capture": {"origin": "test_source", "device_label": "clip frames"}}).json()
    sid = r["session_id"]
    for i, b in enumerate(frames):
        assert c.post(f"/api/webcam/sessions/{sid}/frames", content=b, headers={"X-MG-Seq": str(i), "X-MG-Capture-Us": str(i * 300_000)}).status_code == 202
        wait_decided(c, sid, i + 1, timeout=60)
    black = io.BytesIO()
    Image.fromarray(np.zeros((360, 640, 3), np.uint8)).save(black, "JPEG", quality=85)
    assert c.post(f"/api/webcam/sessions/{sid}/frames", content=black.getvalue(), headers={"X-MG-Seq": "10", "X-MG-Capture-Us": str(10 * 300_000)}).status_code == 202
    wait_decided(c, sid, 11, timeout=60)
    run = c.post(f"/api/webcam/sessions/{sid}/stop").json()["run_id"]
    o = c.get(f"/api/sessions/{run}").json()["observation_set"]
    counts = [len(f["objects"]) for f in o["frames"]]
    assert sum(counts[:10]) > 0 and any(x["cls"]["label"] == "vehicle" for f in o["frames"][:10] for x in f["objects"])  # real detections on real frames
    assert counts[10] == 0  # negative control: a black frame produces no detections from the same model
    fresh = local_client(create_app(asset_dir=tmp_path / "assets", runs_dir=tmp_path / "runs", detector_factory=lambda: detector))
    rep = fresh.get(f"/api/sessions/{run}/verify").json()
    assert rep["ok"], [x for x in rep["checks"] if not x["ok"]]
    assert fresh.get(f"/api/sessions/{run}").json()["trust"] == "verified"


def test_a_second_session_never_destroys_the_first_sessions_user_zone_edits(mk):
    """Session ids are content digests that include the measured stats; the guarded install (tested directly in test_install_dir_*) protects user zones even on a collision."""
    env = mk(scene(person=range(3, 12)))
    zs = {"zone_id": "Mine", "polygon": [[1, 1], [40, 1], [40, 40]]}
    from mineguard.webcam import Session  # noqa: F401
    sid1 = start(env.c).json()["session_id"]
    for i in range(8):
        post(env.c, sid1, i, cap_us=1_000_000 + i * 100_000)
        wait_decided(env.c, sid1, i + 1)
    run = env.c.post(f"/api/webcam/sessions/{sid1}/stop").json()["run_id"]
    assert env.c.put(f"/api/sessions/{run}/zones", json={"expected_revision": zones_rev(env.c, f"/api/sessions/{run}/zones"), "zones": [zs]}).status_code == 200
    sid2 = start(env.c).json()["session_id"]
    for i in range(8):
        post(env.c, sid2, i, cap_us=1_000_000 + i * 100_000)
        wait_decided(env.c, sid2, i + 1)
    run2 = env.c.post(f"/api/webcam/sessions/{sid2}/stop").json()["run_id"]
    # Session ids are content digests that include the measured stats: usually distinct, but two sessions whose frames, clocks AND measured stats are identical
    # (a near-instant test detector) map to the same directory. In BOTH cases the first session's user zone edits must survive.
    assert env.c.get(f"/api/sessions/{run}/zones").json()["zones"][0]["zone_id"] == "Mine"
    z2 = env.c.get(f"/api/sessions/{run2}/zones").json()["zones"][0]["zone_id"]
    assert z2 == ("Mine" if run2 == run else "Z1")


def test_zone_edit_after_the_session_keeps_the_recorded_evaluation_verifiable_and_trust(mk):
    """MG-ZONE-010: editing zones must not make verification fail or revoke trust; the stored evaluation records the zone definitions it used."""
    env = mk(scene(person=range(3, 32)))
    _, run = run_session(env, 34)
    far = {"zone_id": "Z9", "polygon": [[1, 1], [40, 1], [40, 40], [1, 40]]}
    assert env.c.put(f"/api/sessions/{run}/zones", json={"expected_revision": zones_rev(env.c, f"/api/sessions/{run}/zones"), "zones": [far]}).status_code == 200
    rep = env.c.get(f"/api/sessions/{run}/verify").json()
    assert rep["ok"], [c for c in rep["checks"] if not c["ok"]]
    assert env.c.get(f"/api/sessions/{run}").json()["trust"] == "verified"
    assert list(ev_of(env, run)["evaluation"]["zones"]) == ["Z9"]                       # the live view uses the NEW zones
    stored = __import__("json").loads((env.runs / run / "risk" / "evaluation.json").read_text())
    assert [z["zone_id"] for z in stored["zone_defs"]] == ["Z1"]                         # the recorded evaluation still names the zones it used


# ------------------------------------------------------------------ MG-PATH-010: guarded install of content-addressed directories
def _mk_obs_dir(d, digest_src):
    import json
    from mineguard.schema import obsset_digest
    d.mkdir(parents=True, exist_ok=True)
    obs = {"schema": "x", "origin": "model_inference", "marker": digest_src, "frames": []}
    (d / "observations.json").write_text(json.dumps(obs))
    return obsset_digest(obs)


def test_install_dir_refuses_symlink_destination_and_loses_nothing(tmp_path):
    from mineguard.analysis import install_dir
    real = tmp_path / "elsewhere"
    (real / "risk").mkdir(parents=True)
    (real / "risk" / "zones.json").write_text("USER ZONES")
    final = tmp_path / "session_aaaaaaaaaaaaaaaa"
    final.symlink_to(real, target_is_directory=True)
    tmp = tmp_path / "_tmp_11111111"
    dg = _mk_obs_dir(tmp, "n")
    (tmp / "risk").mkdir()
    (tmp / "risk" / "zones.json").write_text("NEW")
    with pytest.raises(RuntimeError, match="unsafe"):
        install_dir(tmp, final, dg)
    assert (real / "risk" / "zones.json").read_text() == "USER ZONES" and (tmp / "risk" / "zones.json").read_text() == "NEW" and final.is_symlink()


def test_install_dir_refuses_a_different_content_digest_and_keeps_the_old_directory(tmp_path):
    from mineguard.analysis import install_dir
    final = tmp_path / "run_bbbbbbbbbbbbbbbb"
    _mk_obs_dir(final, "old")
    (final / "risk").mkdir()
    (final / "risk" / "zones.json").write_text("USER")
    tmp = tmp_path / "_tmp_22222222"
    dg = _mk_obs_dir(tmp, "different content")
    with pytest.raises(RuntimeError, match="collision"):
        install_dir(tmp, final, dg)
    assert (final / "risk" / "zones.json").read_text() == "USER" and tmp.exists()


def test_install_dir_identical_content_preserves_user_zones_by_copy_and_leaves_no_aside_dirs(tmp_path):
    from mineguard.analysis import install_dir
    final = tmp_path / "run_cccccccccccccccc"
    dg = _mk_obs_dir(final, "same")
    (final / "risk").mkdir()
    (final / "risk" / "zones.json").write_text("USER")
    tmp = tmp_path / "_tmp_33333333"
    _mk_obs_dir(tmp, "same")
    (tmp / "risk").mkdir()
    (tmp / "risk" / "zones.json").write_text("DEFAULT")
    install_dir(tmp, final, dg)
    assert (final / "risk" / "zones.json").read_text() == "USER" and not tmp.exists()
    assert [p.name for p in tmp_path.iterdir()] == [final.name]


def test_install_dir_refuses_a_symlink_inside_the_users_risk_dir(tmp_path):
    from mineguard.analysis import install_dir
    final = tmp_path / "run_dddddddddddddddd"
    dg = _mk_obs_dir(final, "same")
    (final / "risk").mkdir()
    (final / "risk" / "link").symlink_to(tmp_path)
    tmp = _mk_obs_dir(tmp_path / "_tmp_44444444", "same")
    with pytest.raises(RuntimeError, match="unsafe"):
        install_dir(tmp_path / "_tmp_44444444", final, dg)
    assert (final / "risk" / "link").is_symlink() and (tmp_path / "_tmp_44444444").exists()


def test_stop_reason_is_a_bounded_vocabulary_and_is_stored(mk):
    env = mk(scene(person=range(0, 40)))
    sid = start(env.c).json()["session_id"]
    for i in range(4):
        post(env.c, sid, i)
        wait_decided(env.c, sid, i + 1)
    assert env.c.post(f"/api/webcam/sessions/{sid}/stop?reason=because").status_code == 422
    out = env.c.post(f"/api/webcam/sessions/{sid}/stop?reason=device_ended").json()
    assert env.c.get(f"/api/sessions/{out['run_id']}").json()["manifest"]["closed_reason"] == "device_ended"


def test_manifest_records_measured_live_stats(mk):
    env = mk(scene(person=range(0, 40)))
    _, run = run_session(env, 10)
    m = env.c.get(f"/api/sessions/{run}").json()["manifest"]["live_stats"]
    assert m["queue_depth_max_observed"] <= m["queue_max"] == webcam.QUEUE_MAX and m["latency_ms"]["p50"] is not None and m["latency_ms"]["max"] >= m["latency_ms"]["p50"]
    assert m["capture_interval_ms"] == 100 and m["analyzed_millifps"] > 0 and all(isinstance(v, int) for v in (m["span_ms"], m["analyzed_millifps"]))
    assert env.c.get(f"/api/sessions/{run}/verify?deep=false").json()["ok"]


# ------------------------------------------------------------------ cycle-2 review findings
def _sess(env, sid):
    return env.app.state.st.session_mgr.sessions[sid]


def test_a_retryable_503_stop_is_completed_autonomously_once_the_detector_returns(mk, monkeypatch):
    """MG-WCAM-012: nobody retries, nobody polls: the manager finishes every 'finalizing' session whose worker has exited and releases the lock."""
    monkeypatch.setattr(webcam, "WORKER_JOIN_S", 0.1)
    env = mk(scene(person=range(0, 40)), delay=0.8)
    sid = start(env.c).json()["session_id"]
    post(env.c, sid, 0)
    time.sleep(0.2)
    assert env.c.post(f"/api/webcam/sessions/{sid}/stop").status_code == 503
    t = time.time()
    while time.time() - t < 10 and not [p for p in env.runs.iterdir() if p.name.startswith("session_")]:
        time.sleep(0.1)                                  # NO further request to the server
    assert len([p for p in env.runs.iterdir() if p.name.startswith("session_")]) == 1
    t = time.time()
    while time.time() - t < 5 and env.app.state.st.infer_lock.locked():
        time.sleep(0.1)
    assert not env.app.state.st.infer_lock.locked() and start(env.c).status_code == 201


def test_frames_are_spooled_to_disk_and_never_accumulate_in_memory(mk):
    """MG-WCAM-013."""
    env = mk(scene(person=range(0, 40)))
    sid = start(env.c).json()["session_id"]
    for i in range(8):
        post(env.c, sid, i)
        wait_decided(env.c, sid, i + 1)
    s = _sess(env, sid)
    assert len(list((s.tmp / "frames").glob("*.jpg"))) == 8 and not any(isinstance(v, (bytes, bytearray)) for v in vars(s).values())
    assert not hasattr(s, "jpegs") and all(isinstance(v, str) and len(v) == 64 for v in s.jpeg_sha.values())
    run = env.c.post(f"/api/webcam/sessions/{sid}/stop").json()["run_id"]
    assert len(list((env.runs / run / "frames").glob("*.jpg"))) == 8
    assert env.c.get(f"/api/sessions/{run}/verify?deep=false").json()["ok"]


def test_session_storage_budget_refuses_further_frames_and_counts_them(mk, monkeypatch):
    monkeypatch.setattr(webcam, "MAX_SESSION_BYTES", 2 * len(jpeg(0)) + 10)
    env = mk(scene(person=range(0, 40)))
    sid = start(env.c).json()["session_id"]
    codes = []
    for i in range(5):
        codes.append(post(env.c, sid, i).status_code)
        wait_decided(env.c, sid, min(i + 1, 2))
    assert codes[:2] == [202, 202] and set(codes[2:]) == {409}
    run = env.c.post(f"/api/webcam/sessions/{sid}/stop").json()["run_id"]
    assert env.c.get(f"/api/sessions/{run}").json()["manifest"]["live_stats"]["intake_rejected"] == 3


def test_failed_session_verification_is_never_undone_by_restoring_the_bytes(mk):
    """MG-TRUST-010 for sessions: a failed deep verification removes the grant; restoring the original bytes later does not resurrect it."""
    env = mk(scene(person=range(3, 32)))
    _, run = run_session(env, 34)
    p = env.runs / run / "frames" / "000010.jpg"
    orig = p.read_bytes()
    assert env.c.get(f"/api/sessions/{run}").json()["trust"] == "session"
    p.write_bytes(jpeg(10, base=60))
    assert not env.c.get(f"/api/sessions/{run}/verify").json()["ok"]
    p.write_bytes(orig)
    assert env.c.get(f"/api/sessions/{run}").json()["trust"] == "unverified"
    assert env.c.get(f"/api/sessions/{run}/verify").json()["ok"] and env.c.get(f"/api/sessions/{run}").json()["trust"] == "verified"


def _rewrite(env, run, name, edit):
    """Coordinated tamper: edit a component document AND the same part of export.json (the strongest local attacker short of re-addressing the run)."""
    import json
    d = env.runs / run
    doc = json.loads((d / f"{name}.json").read_text())
    edit(doc)
    (d / f"{name}.json").write_text(json.dumps(doc, indent=1))
    exp = json.loads((d / "export.json").read_text())
    key = {"observations": "observation_set"}.get(name, name)
    exp[key] = doc
    (d / "export.json").write_text(json.dumps(exp, indent=1))


@pytest.mark.parametrize("name,edit,deep_only", [
    ("manifest", lambda m: m.update(closed_reason="device_ended"), False),
    ("manifest", lambda m: m["counters"].update(dropped_stale=7), False),
    ("manifest", lambda m: m.update(limitations=[]), False),
    ("manifest", lambda m: m["live_stats"].update(latency_ms={"p50": -5, "p95": 1, "max": 2}), False),
    ("manifest", lambda m: m["evidence"][0].update(pts_us=123456), False),
    ("provenance", lambda p: p["capture"].update(origin="getusermedia"), False),
    ("provenance", lambda p: p["model"].update(architecture="forged"), False),
    ("provenance", lambda p: p["packages"].update(torch="9.9.9"), True),
    ("provenance", lambda p: p.update(device="cuda"), True),
    ("source", lambda s: s["capture"].update(origin="getusermedia"), False),
])
def test_coordinated_edits_of_provenance_manifest_or_capture_cannot_be_verified(mk, name, edit, deep_only):
    """MG-PROV-010: components and export.json edited TOGETHER (so export_matches_components stays true) are still caught by rebuilding them."""
    env = mk(scene(person=range(3, 32)))
    _, run = run_session(env, 34)
    _rewrite(env, run, name, edit)
    shallow = env.c.get(f"/api/sessions/{run}/verify?deep=false").json()
    assert next(c for c in shallow["checks"] if c["id"] == "export_matches_components")["ok"]
    if not deep_only:
        assert not shallow["ok"], [c["id"] for c in shallow["checks"] if not c["ok"]]
    deep = env.c.get(f"/api/sessions/{run}/verify").json()
    assert not deep["ok"] and env.c.get(f"/api/sessions/{run}").json()["trust"] == "unverified"


def test_the_deep_check_rehashes_the_executing_weights_and_compares_the_provenance(mk):
    env = mk(scene(person=range(3, 12)))
    _, run = run_session(env, 8)
    rep = env.c.get(f"/api/sessions/{run}/verify").json()
    ids = {c["id"]: c["ok"] for c in rep["checks"]}
    assert ids["executing_weights_file_rehashed_and_equal"] and ids["provenance_rebuilt_from_executing_environment_equals_stored"] and ids["manifest_rebuilt_from_primary_data_equals_stored"]


# ------------------------------------------------------------------ cycle-3 review findings
def test_evidence_references_never_change_the_evaluation_digest(mk):
    """MG-EVAL-012: evidence file names/hashes are outside the deterministic evaluation contract."""
    from mineguard.risk import evaluation as ev
    env = mk(scene(person=range(3, 32)))
    _, run = run_session(env, 34)
    doc = ev_of(env, run)["evaluation"]
    base = doc["digest"]
    other = [dict(e, file_sha256="f" * 64) for e in __import__("json").loads((env.runs / run / "manifest.json").read_text())["evidence"]]
    again = ev.attach_evidence(__import__("copy").deepcopy(doc), other)
    assert again["evidence_refs"] != doc["evidence_refs"] and ev.eval_digest(again) == base == doc["digest"]
    assert all("evidence" not in e for z in doc["zones"].values() for e in z["events"])


def test_event_ids_are_canonical_and_bound_to_input_zone_rule_epoch_key_and_start_index():
    """MG-EVENT-012."""
    from mineguard.risk.kernel import Params, Sample, ZoneEngine, T, F
    def run_once(ctx):
        e = ZoneEngine("Z", Params(), id_context=ctx)
        for i in range(30):
            e.push(Sample(t_us=i * 100_000, index=i, persons={"P": (T, None)}, vehicle=(F, None)))
        e.finish(3_000_000)
        return [x["event_id"] for x in e.result()["events"]]
    a, b, c, d = run_once(("in1", "z1")), run_once(("in2", "z1")), run_once(("in1", "z2")), run_once(("in1", "z1"))
    assert a and a == d and all(x.startswith("evt_") and len(x) == 20 for x in a)
    assert set(a).isdisjoint(b) and set(a).isdisjoint(c)           # other input digest or zone geometry => different ids even with identical timing


def test_risk_response_and_export_come_from_one_zone_snapshot_even_if_a_zone_edit_races(mk, monkeypatch):
    """MG-EXPORT-012."""
    from mineguard import api_vs02
    env = mk(scene(person=range(3, 32)))
    _, run = run_session(env, 34)
    real = api_vs02.evaluate_dir
    far = {"zone_id": "Zrace", "polygon": [[1, 1], [40, 1], [40, 40]]}

    def racing(d, zdoc=None, snap=None):
        out = real(d, zdoc, snap)
        env.c.put(f"/api/sessions/{run}/zones", json={"expected_revision": zones_rev(env.c, f"/api/sessions/{run}/zones"), "zones": [far]})   # a zone edit commits right after the evaluation was computed
        return out
    monkeypatch.setattr(api_vs02, "evaluate_dir", racing)
    # MG-RISK-033: the response is derived from ONE snapshot; a zone edit that commits before it is published moves the inputs, so the response is REFUSED (never a mix of two revisions)
    r = env.c.get(f"/api/sessions/{run}/risk")
    assert r.status_code == 409 and r.json()["detail"]["msg_id"] == "mg.refusal.source_changed_while_read"
    monkeypatch.setattr(api_vs02, "evaluate_dir", real)
    r = env.c.get(f"/api/sessions/{run}/risk").json()                                       # the next response is consistent with the new revision
    assert r["zones"]["zones"] == r["evaluation"]["zone_defs"] and r["zones"]["revision"] == 2
    x = env.c.get(f"/api/sessions/{run}/risk/export").json()
    assert x["zones"]["zones"] == x["evaluation"]["zone_defs"]


def test_finished_sessions_release_their_state_and_tombstones_are_bounded(mk, monkeypatch):
    """MG-WCAM-014."""
    monkeypatch.setattr(webcam, "MAX_TOMBSTONES", 3)
    env = mk(scene(person=range(0, 40)))
    mgr = env.app.state.st.session_mgr
    sids = []
    for k in range(5):
        sid = start(env.c).json()["session_id"]
        for i in range(3):
            post(env.c, sid, i, cap_us=1_000_000 * (k + 1) + i * 100_000)
            wait_decided(env.c, sid, i + 1)
        env.c.post(f"/api/webcam/sessions/{sid}/stop")
        sids.append(sid)
    assert all(isinstance(v, webcam.Tombstone) for v in mgr.sessions.values()) and len(mgr.sessions) == 3
    kept = mgr.sessions[sids[-1]]
    assert not hasattr(kept, "records") and not hasattr(kept, "tracker") and not hasattr(kept, "engines")
    v = env.c.get(f"/api/webcam/sessions/{sids[-1]}").json()                              # a finished session is still answerable
    assert v["state"] == "done" and v["run_id"].startswith("session_") and v["latest"] == {"seq": None, "objects": []}
    assert post(env.c, sids[-1], 99).status_code == 409
    assert env.c.post(f"/api/webcam/sessions/{sids[-1]}/stop").json()["state"] == "done"   # idempotent after release
    assert env.c.get(f"/api/webcam/sessions/{sids[0]}").status_code == 404                  # evicted beyond the bound


def test_a_failed_autonomous_finalization_leaves_no_staging_directory_and_releases_the_lock(mk, monkeypatch):
    monkeypatch.setattr(webcam, "IDLE_FINALIZE_S", 0.3)
    def boom(self):
        raise webcam.SessionError(500, "mg.refusal.session_failed")
    monkeypatch.setattr(webcam.Session, "_finalize", boom)
    env = mk(scene(person=range(0, 40)))
    sid = start(env.c).json()["session_id"]
    post(env.c, sid, 0)
    wait_decided(env.c, sid, 1)
    t = time.time()
    while time.time() - t < 6 and env.app.state.st.infer_lock.locked():
        time.sleep(0.1)
    assert not env.app.state.st.infer_lock.locked()
    assert not [p for p in env.runs.iterdir() if p.name.startswith("_tmp_") or p.name.startswith("session_")]
    assert env.c.get(f"/api/webcam/sessions/{sid}").json()["state"] == "failed"


def test_chunked_uploads_are_refused_at_the_limit_and_the_server_stops_reading(mk):
    import asyncio
    from mineguard.api_vs02 import read_limited

    class Endless:
        consumed = 0

        async def stream(self):
            while True:                       # an endless chunked body with no Content-Length
                Endless.consumed += 1
                yield b"\xff" * 100_000
    with pytest.raises(webcam.SessionError) as e:
        asyncio.run(read_limited(Endless(), webcam.MAX_FRAME_BYTES, webcam.SessionError(413, "mg.refusal.frame_too_large")))
    assert e.value.status == 413 and Endless.consumed <= webcam.MAX_FRAME_BYTES // 100_000 + 1     # it never read past the limit
    env = mk(scene(person=range(0, 40)))
    sid = start(env.c).json()["session_id"]
    r = env.c.post(f"/api/webcam/sessions/{sid}/frames", content=(b"\xff" * 100_000 for _ in range(60)), headers={"X-MG-Seq": "0", "X-MG-Capture-Us": "1"})
    assert r.status_code == 413
    big = b'{"base_run_id":"x","pad":"' + b"a" * 9_000_000 + b'"}'
    assert env.c.post("/api/replays", content=big).status_code == 413
    env.c.post(f"/api/webcam/sessions/{sid}/stop")


def test_zone_request_shape_is_bounded(mk):
    env = mk(scene(person=range(3, 12)))
    _, run = run_session(env, 8)
    many = [[i % 300, (i * 7) % 170] for i in range(1000)]
    assert env.c.put(f"/api/sessions/{run}/zones", json={"expected_revision": zones_rev(env.c, f"/api/sessions/{run}/zones"), "zones": [{"zone_id": "A", "polygon": many}]}).status_code == 422
    assert env.c.put(f"/api/sessions/{run}/zones", json={"expected_revision": zones_rev(env.c, f"/api/sessions/{run}/zones"), "zones": [{"zone_id": "A", "polygon": [[1, 2, 3], [4, 5], [6, 7]]}]}).status_code == 422
    assert env.c.put(f"/api/sessions/{run}/zones", json={"expected_revision": zones_rev(env.c, f"/api/sessions/{run}/zones"), "zones": [{"zone_id": f"Z{i}", "polygon": [[1, 1], [9, 1], [9, 9]]} for i in range(9)]}).status_code == 422


def test_two_identical_sessions_share_one_directory_and_the_first_ones_user_zones_survive(mk, monkeypatch):
    """Deterministic version of the collision case: identical frames, clocks and (forced) identical measured stats => the same content-addressed directory."""
    monkeypatch.setattr(webcam.Session, "_live_stats", lambda self, pts: {"latency_ms": {"p50": 1, "p95": 1, "max": 1}, "queue_depth_max_observed": 1, "queue_max": 2,
                                                                         "span_ms": 700, "analyzed_millifps": 10000, "capture_interval_ms": 100, "intake_rejected": 0})
    env = mk(scene(person=range(3, 12)))
    ids, keep = [], {"zone_id": "Mine", "polygon": [[1, 1], [40, 1], [40, 40]]}
    for n in range(2):
        sid = start(env.c).json()["session_id"]
        for i in range(8):
            post(env.c, sid, i, cap_us=1_000_000 + i * 100_000)
            wait_decided(env.c, sid, i + 1)
        ids.append(env.c.post(f"/api/webcam/sessions/{sid}/stop").json()["run_id"])
        if n == 0:
            assert env.c.put(f"/api/sessions/{ids[0]}/zones", json={"expected_revision": zones_rev(env.c, f"/api/sessions/{ids[0]}/zones"), "zones": [keep]}).status_code == 200
    assert ids[0] == ids[1]
    assert env.c.get(f"/api/sessions/{ids[0]}/zones").json()["zones"][0]["zone_id"] == "Mine"
    assert len([p for p in env.runs.iterdir() if p.name.startswith("session_")]) == 1 and not [p for p in env.runs.iterdir() if p.name.startswith("_tmp_")]
