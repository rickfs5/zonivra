"""Regression vectors for the night-sprint review 3 findings (MG-WCAM-017, ZONE-017, TRUST-017, ORIGIN-017; the kernel vectors RISK-017/PENDING-017 live in test_risk_kernel.py).
Synthetic isolated storage only."""
import json
import threading

from conftest import needs_video, zones_rev
from test_night_review1 import restart
from test_privacy import run, start_default
from test_session_deletion import distinct
from test_vs02_replay_mp4 import ZONE as MP4_ZONE, post_replay, replay_obs
from test_rc1_closeout import iso  # noqa: F401
from test_vs02_webcam_risk import ZONE, jpeg, mk, post, scene, wait_decided  # noqa: F401


# ------------------------------------------------------------------ MG-WCAM-017
def test_a_declared_loss_that_cannot_be_timed_is_refused_not_dropped(mk):
    env = mk(scene(person=range(0, 40)))
    sid = start_default(env.c, privacy="off").json()["session_id"]
    assert post(env.c, sid, 0, cap_us=5_000_000).status_code == 202
    r = post(env.c, sid, 5, cap_us=5_000_001)                       # 4 frames missing but only 1 microsecond between neighbours: no strictly timed stub exists
    assert r.status_code == 409 and r.json()["detail"]["msg_id"] == "mg.refusal.session_loss_not_representable"
    assert env.c.get(f"/api/webcam/sessions/{sid}").json()["counters"]["received"] == 1          # nothing was half-recorded
    assert post(env.c, sid, 1, cap_us=5_100_000).status_code == 202                             # the session continues normally


def test_trailing_losses_declared_at_stop_become_a_final_unknown_sample(mk):
    env = mk(scene(person=range(0, 40)))
    sid = start_default(env.c, privacy="off").json()["session_id"]
    for seq in (0, 1):
        post(env.c, sid, seq); wait_decided(env.c, sid, seq + 1)
    r = env.c.post(f"/api/webcam/sessions/{sid}/stop", headers={"X-MG-Next-Seq": "4"})      # ticks 2 and 3 were issued (skipped, failed or still encoding) but never arrived
    assert r.status_code == 200
    b = env.c.get(f"/api/sessions/{r.json()['run_id']}").json()
    stub = b["source"]["samples"][-1]
    assert stub["reason"] == "client_loss" and stub["lost_frames"] == 2 and stub["status"] == "not_analyzed_dropped"
    assert stub["capture_us"] == b["source"]["samples"][-2]["capture_us"] + 100_000         # the last observation stays valid for ONE declared cadence interval (100 ms in these tests); UNKNOWN begins there
    assert b["source"]["frame_table"]["last_frame_duration_ticks"] == 200_000               # the tail covers the DECLARED extent: 2 lost ticks x 100 ms, not one invented short stub
    c = b["manifest"]["counters"]
    assert c["client_loss_events"] == 1 and c["client_lost_frames"] == 2 and c["received"] == 2
    assert env.c.get(f"/api/sessions/{r.json()['run_id']}/verify?deep=false").json()["ok"]


def test_a_frame_in_flight_after_stop_is_refused_and_never_duplicates_the_declared_loss(mk):
    env = mk(scene(person=range(0, 40)))
    sid = start_default(env.c, privacy="off").json()["session_id"]
    post(env.c, sid, 0); wait_decided(env.c, sid, 1)
    rid = env.c.post(f"/api/webcam/sessions/{sid}/stop", headers={"X-MG-Next-Seq": "3"}).json()["run_id"]       # seq 1 and 2 declared lost
    late = post(env.c, sid, 1)                                                                                    # seq 1 arrives after the stop
    assert late.status_code in (404, 409)
    b = env.c.get(f"/api/sessions/{rid}").json()
    assert b["manifest"]["counters"]["client_lost_frames"] == 2 and len(b["source"]["samples"]) == 2


def test_stop_without_a_declaration_adds_no_stub_and_headers_are_validated(mk):
    env = mk(scene(person=range(0, 40)))
    sid = start_default(env.c).json()["session_id"]
    post(env.c, sid, 0); wait_decided(env.c, sid, 1)
    for h in ({"X-MG-Next-Seq": "x"}, {"X-MG-Next-Seq": "-3"}, {"X-MG-Next-Seq": "10000001"}):
        assert env.c.post(f"/api/webcam/sessions/{sid}/stop", headers=h).status_code == 400, h
    rid = env.c.post(f"/api/webcam/sessions/{sid}/stop").json()["run_id"]
    assert all(s["reason"] != "client_loss" for s in env.c.get(f"/api/sessions/{rid}").json()["source"]["samples"])


# ------------------------------------------------------------------ MG-ZONE-017
def test_zone_put_requires_the_expected_revision_and_refuses_stale_writes(mk):
    env = mk(scene(person=range(3, 32)))
    _, rid = run(env, 8)
    url = f"/api/sessions/{rid}/zones"
    rev = env.c.get(url).json()["revision"]
    assert env.c.put(url, json={"zones": [ZONE]}).status_code == 422                                 # required
    assert env.c.put(url, json={"expected_revision": rev, "zones": [{**ZONE, "zone_id": "Z9"}]}).status_code == 200
    r = env.c.put(url, json={"expected_revision": rev, "zones": [{**ZONE, "zone_id": "Z2"}]})      # a second editor still at the old revision
    assert r.status_code == 409 and r.json()["detail"]["msg_id"] == "mg.refusal.zones_revision_conflict" and r.json()["detail"]["current"]["revision"] == rev + 1
    assert [z["zone_id"] for z in env.c.get(url).json()["zones"]] == ["Z9"]                        # the first edit was not lost


def test_concurrent_zone_puts_from_one_revision_exactly_one_wins(mk):
    env = mk(scene(person=range(3, 32)))
    _, rid = run(env, 8)
    url = f"/api/sessions/{rid}/zones"
    rev = env.c.get(url).json()["revision"]
    out, gate = [], threading.Barrier(6)
    def go(i):
        gate.wait()
        out.append(env.c.put(url, json={"expected_revision": rev, "zones": [{**ZONE, "zone_id": f"Z{i}"}]}).status_code)
    ts = [threading.Thread(target=go, args=(i,)) for i in range(6)]
    [t.start() for t in ts]; [t.join(20) for t in ts]
    assert sorted(out) == [200, 409, 409, 409, 409, 409] and env.c.get(url).json()["revision"] == rev + 1


# ------------------------------------------------------------------ MG-TRUST-017
def test_an_evidence_mismatch_seen_by_the_frame_endpoint_revokes_at_once(mk):
    env = mk(scene(person=range(3, 32)))
    _, rid = run(env, privacy="off")
    f = env.runs / rid / "frames" / "000003.jpg"
    good = f.read_bytes()
    f.write_bytes(good + b"x")
    assert env.c.get(f"/api/sessions/{rid}/frames/3").status_code == 409
    f.write_bytes(good)                                                                          # restored before any other trust-bearing read
    assert env.c.get(f"/api/sessions/{rid}").json()["trust"] == "unverified"


def test_a_repeated_stop_never_regrants_trust(mk):
    env = mk(scene(person=range(3, 32)))
    sid = start_default(env.c, privacy="off").json()["session_id"]
    for i in range(6):
        post(env.c, sid, i); wait_decided(env.c, sid, i + 1)
    rid = env.c.post(f"/api/webcam/sessions/{sid}/stop").json()["run_id"]
    f = env.runs / rid / "frames" / "000002.jpg"
    good = f.read_bytes()
    f.write_bytes(good + b"x")
    assert not env.c.get(f"/api/sessions/{rid}/verify?deep=false").json()["ok"]
    f.write_bytes(good)
    again = env.c.post(f"/api/webcam/sessions/{sid}/stop")                                       # idempotent repeat on the retained tombstone
    assert again.status_code == 200 and again.json()["run_id"] == rid and "fresh" not in again.json()
    assert env.c.get(f"/api/sessions/{rid}").json()["trust"] == "unverified"


# ------------------------------------------------------------------ MG-ORIGIN-017
@needs_video
def test_human_annotations_are_not_counted_or_labelled_as_simulations(iso):
    base = iso.client.get(f"/api/runs/{iso.run}").json()
    r = post_replay(iso, replay_obs(base, origin="human_annotated"))
    assert r.status_code == 201 and r.json()["origin"] == "human_annotated"
    rid = r.json()["run_id"]
    assert iso.client.put(f"/api/runs/{rid}/zones", json={"expected_revision": 0, "zones": [MP4_ZONE]}).status_code == 200
    s = post_replay(iso, replay_obs(base, origin="synthetic_scripted", person=range(90, 450)))
    sid = s.json()["run_id"]
    assert iso.client.put(f"/api/runs/{sid}/zones", json={"expected_revision": 0, "zones": [MP4_ZONE]}).status_code == 200
    ev = iso.client.get("/api/events").json()["events"]
    assert {e["nature"] for e in ev if e["source_id"] == rid} == {"human_annotated"} and {e["nature"] for e in ev if e["source_id"] == sid} == {"synthetic"}
    o = iso.client.get("/api/overview").json()
    assert o["sources"]["annotated"] == 1 and o["sources"]["simulations"] == 1
    assert o["events"]["human_annotated"]["RED"] >= 1 and o["events"]["synthetic"]["RED"] >= 1
    no_ann = iso.client.get("/api/events?include_annotated=false").json()["events"]
    assert all(e["nature"] != "human_annotated" for e in no_ann) and any(e["nature"] == "synthetic" for e in no_ann)
