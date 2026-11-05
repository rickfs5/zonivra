"""Regression vectors for the night-sprint review 5 findings (MG-WCAM-019/020, MG-ZONE-019/020, MG-VERSION-019). Fixes after the fifth review are UNREVIEWED by Codex."""
import threading
import time

import pathlib

from mineguard import webcam
from mineguard.risk.kernel import ENGINE_VERSION, SEMANTICS_VERSION
from mineguard.risk.tracker import TRACKER_ID, TRACKER_VERSION
from conftest import needs_video, zones_rev
from test_privacy import run, start_default
from test_rc1_closeout import iso  # noqa: F401
from test_vs02_webcam_risk import ZONE, mk, post, scene, wait_decided  # noqa: F401


# ------------------------------------------------------------------ MG-WCAM-019
def test_a_vanished_client_loss_is_recorded_from_the_heartbeat_watermark(mk, monkeypatch):
    monkeypatch.setattr(webcam, "IDLE_FINALIZE_S", 0.6)
    env = mk(scene(person=range(0, 40)))
    sid = start_default(env.c).json()["session_id"]
    post(env.c, sid, 0); wait_decided(env.c, sid, 1)
    assert env.c.post(f"/api/webcam/sessions/{sid}/watermark", headers={"X-MG-Next-Seq": "6"}).status_code == 202       # ticks 1..5 were issued but never reached the server
    assert env.c.post(f"/api/webcam/sessions/{sid}/watermark", headers={"X-MG-Next-Seq": "3"}).status_code == 202       # monotonic: a lower value changes nothing
    t = time.time()
    while time.time() - t < 8 and env.c.get(f"/api/webcam/sessions/{sid}").json()["state"] != "done":
        time.sleep(0.1)                                                                                                    # the reaper finalizes; no stop is ever sent
    v = env.c.get(f"/api/webcam/sessions/{sid}").json()
    assert v["state"] == "done" and v["closed_reason"] == "client_disconnected"
    b = env.c.get(f"/api/sessions/{v['run_id']}").json()
    stub = b["source"]["samples"][-1]
    assert stub["reason"] == "client_loss" and stub["lost_frames"] == 5 and b["manifest"]["counters"]["client_lost_frames"] == 5
    late = env.c.post(f"/api/webcam/sessions/{sid}/watermark", headers={"X-MG-Next-Seq": "9"})                           # after finalization only the BOUNDED, server-judged late declaration exists
    assert late.status_code == 202 and late.json()["late"] is True and late.json()["late_declaration"]["admitted"] in (True, False)
    assert env.c.get(f"/api/sessions/{v['run_id']}").json()["manifest"]["counters"]["client_lost_frames"] == 5          # the stored evidence is never rewritten


def test_heartbeat_headers_are_validated_and_do_not_count_as_activity(mk, monkeypatch):
    monkeypatch.setattr(webcam, "IDLE_FINALIZE_S", 0.6)
    env = mk(scene(person=range(0, 40)))
    sid = start_default(env.c).json()["session_id"]
    post(env.c, sid, 0); wait_decided(env.c, sid, 1)
    for h in ({}, {"X-MG-Next-Seq": "x"}, {"X-MG-Next-Seq": "-1"}, {"X-MG-Next-Seq": "10000001"}):
        assert env.c.post(f"/api/webcam/sessions/{sid}/watermark", headers=h).status_code == 400, h
    t = time.time()
    while time.time() - t < 8 and env.c.get(f"/api/webcam/sessions/{sid}").json()["state"] != "done":
        env.c.post(f"/api/webcam/sessions/{sid}/watermark", headers={"X-MG-Next-Seq": "1"})        # heartbeats alone must not keep a silent session alive
        time.sleep(0.1)
    assert env.c.get(f"/api/webcam/sessions/{sid}").json()["state"] == "done"


# ------------------------------------------------------------------ MG-WCAM-020
def test_a_frame_that_became_stale_during_inference_is_dropped_not_analyzed(mk, monkeypatch):
    monkeypatch.setattr(webcam, "MAX_AGE_US", 150_000)
    env = mk(scene(person=range(0, 40)))
    real, first = env.det.predict, {"done": False}
    def slow_once(rgb, thr):
        if not first["done"]:
            first["done"] = True
            time.sleep(0.4)                                                     # a single call slower than the freshness bound, on an EMPTY queue
        return real(rgb, thr)
    monkeypatch.setattr(env.det, "predict", slow_once)
    sid = start_default(env.c).json()["session_id"]
    post(env.c, sid, 0)
    t = time.time()
    while time.time() - t < 10 and env.c.get(f"/api/webcam/sessions/{sid}").json()["counters"]["dropped_stale"] < 1:
        time.sleep(0.05)
    v = env.c.get(f"/api/webcam/sessions/{sid}").json()
    assert v["counters"]["dropped_stale"] == 1 and v["counters"]["analyzed"] == 0 and v["latest"]["seq"] is None and v["latest"]["objects"] == []
    for i in (1, 2, 3):
        post(env.c, sid, i); wait_decided(env.c, sid, i + 1)
    rid = env.c.post(f"/api/webcam/sessions/{sid}/stop").json()["run_id"]
    first_sample = env.c.get(f"/api/sessions/{rid}").json()["source"]["samples"][0]
    assert first_sample["status"] == "not_analyzed_dropped" and first_sample["reason"] == "stale" and first_sample["jpeg_sha256"] is None
    assert env.c.get(f"/api/sessions/{rid}").json()["observation_set"]["frames"][0]["frame_index"] != 0                  # no objects of the stale result were stored


# ------------------------------------------------------------------ MG-ZONE-019
@needs_video
def test_simulation_default_zone_never_overwrites_a_concurrent_user_edit(iso, monkeypatch):
    c = iso.client
    rid = c.post("/api/simulations/person-vehicle", json={"base_run_id": iso.run}).json()["run_id"]
    zf = c.app.state.st.runs_dir / rid / "risk" / "zones.json"
    zf.unlink()                                                                  # recreate "no zone set yet" on the stored simulation
    paused, resume, armed = threading.Event(), threading.Event(), threading.Event()
    armed.set()
    real_exists = pathlib.Path.exists
    def exists(self, *a, **k):
        r = real_exists(self, *a, **k)
        if self == zf and not r and armed.is_set() and not paused.is_set():
            paused.set(); resume.wait(1.0)                                       # the simulation observed "no zones" and is about to create the default
        return r
    monkeypatch.setattr(pathlib.Path, "exists", exists)
    sim = threading.Thread(target=lambda: c.post("/api/simulations/person-vehicle", json={"base_run_id": iso.run}), name="sim")
    sim.start(); assert paused.wait(10)
    out = []
    put = threading.Thread(target=lambda: out.append(c.put(f"/api/runs/{rid}/zones", json={"expected_revision": 0, "zones": [{**{"zone_id": "Z7", "kind": "restricted", "polygon": [[200, 300], [1000, 300], [1000, 700], [200, 700]]}}]}).status_code))
    put.start(); resume.set(); sim.join(30); put.join(30)
    monkeypatch.undo()
    cur = c.get(f"/api/runs/{rid}/zones").json()
    assert out and (out[0] == 409 or [z["zone_id"] for z in cur["zones"]] == ["Z7"])      # a PUT that reported success is never silently discarded


# ------------------------------------------------------------------ MG-ZONE-020
def test_zone_names_must_be_nfc_and_a_valid_name_keeps_risk_views_working(mk):
    env = mk(scene(person=range(3, 32)))
    _, rid = run(env, 8)
    url = f"/api/sessions/{rid}/zones"
    for bad in ("Café", "bad\ud800"):
        import json as _json
        r = env.c.put(url, content=_json.dumps({"expected_revision": zones_rev(env.c, url), "zones": [{**ZONE, "name": bad}]}), headers={"Content-Type": "application/json"})
        assert r.status_code == 422, bad                                     # a lone surrogate is rejected by request validation, which must answer 422 (not crash while echoing it)
        if bad == "Cafe\u0301":
            assert r.json()["detail"]["msg_id"] == "mg.refusal.zone_name_not_nfc"
        else:
            assert "bad" not in r.text and r.json()["detail"][0]["loc"][-1] == "name"
    ok = env.c.put(url, json={"expected_revision": zones_rev(env.c, url), "zones": [{**ZONE, "name": "Café"}]})
    assert ok.status_code == 200 and env.c.get(f"/api/sessions/{rid}/risk").status_code == 200 and env.c.get(f"/api/sessions/{rid}/risk/export").status_code == 200


# ------------------------------------------------------------------ MG-VERSION-019
def test_overview_reports_the_versions_that_produce_the_provenance(mk):
    env = mk(scene())
    e = env.c.get("/api/overview").json()["engine"]
    assert e["risk_engine"] == ENGINE_VERSION == "mg-risk-kernel/2.0.3" and e["semantics_version"] == SEMANTICS_VERSION and e["tracker"] == f"{TRACKER_ID} {TRACKER_VERSION}"
    root = pathlib.Path(__file__).resolve().parents[2] / "docs"
    for f in ("README.md", "MG-PRODUCT-001-operating-guide.md"):
        assert "mg-risk-kernel/2.0.0" not in (root / f).read_text(), f
