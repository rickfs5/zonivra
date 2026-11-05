"""MG-TRACK-022 (review B): an ambiguous association ends tracks, and that discontinuity must REACH the kernel (offline, live webcam and feeds) and be counted, never fabricated as zero."""
import json
import time

from mineguard.risk import evaluation as ev
from mineguard.risk.tracker import TRACKER_VERSION, IouTracker, TrackerParams
from mineguard.risk.zone_eval import make_zone
from test_privacy import start_default
from test_vertical2_feeds import PERSON_IN as FPERSON, ZONE as FZONE, act, always, create, fm, view, wait_state  # noqa: F401
from test_vs02_webcam_risk import PERSON_IN, mk, post, wait_decided  # noqa: F401

TRUNC = {"left": False, "right": False, "top": False, "bottom": False}


def box(x, y, w, h):
    return {"x": x, "y": y, "w": w, "h": h}


def obj(x, y, w, h):
    return {"obj_key": "o", "cls": {"label": "person", "state": "known", "detector_label": "person", "detector_class_id": 1}, "bbox": box(x, y, w, h), "truncated": TRUNC,
            "confidence": {"permille": 900, "kind": "model_uncalibrated"}, "track_ref": None}


def test_the_tracker_reports_the_tracks_it_ends_by_ambiguity_for_exactly_that_update():
    tr = IouTracker(TrackerParams())
    tr.update(0, [("person", box(100, 100, 50, 100))])
    tr.update(200_000, [("person", box(100, 100, 50, 100))])
    assert tr.breaks == []
    tr.update(400_000, [("person", box(100, 100, 50, 100)), ("person", box(110, 104, 50, 100))])   # both detections overlap the one track
    assert tr.breaks == ["T1"] and tr.ended[-1]["reason"] == "ambiguous_association"
    tr.update(600_000, [("person", box(100, 100, 50, 100))])
    assert "T1" not in tr.breaks                                                                     # per update, not cumulative
    assert TRACKER_VERSION == "0.3.0"


def test_offline_evaluation_closes_the_episode_as_a_track_discontinuity_not_as_unknown_inputs():
    W, H = 1280, 720
    zone = make_zone("Z1", "restricted", [[400, 200], [1200, 200], [1200, 700], [400, 700]], W, H, "Zona")
    recs = [{"index": i, "t_us": i * 200_000, "status": "analyzed", "objects": [obj(600, 260, 80, 380)] + ([obj(612, 266, 80, 380)] if i >= 14 else [])} for i in range(24)]
    p, tp = ev.live_params(200)
    inputs = {"obsset_digest": "a" * 64, "source_digest": "b" * 64, "frame_table_digest": "c" * 64, "kind": "webcam", "frame_table_provider": {}, "run_config": "d" * 64}
    doc = ev.evaluate_records(recs, recs[-1]["t_us"] + 200_000, [zone], p, tp, inputs, "model_inference")
    (e,) = [x for x in doc["zones"]["Z1"]["events"] if x["severity"] == "YELLOW"]
    assert e["closed_reason"] == "track_discontinuity" and e["tracks"] == ["T1"]
    ids, tracks, breaks = ev.run_tracker(recs, tp)
    assert [i for i, b in enumerate(breaks) if b][0] == 14 and len(ids) == len(breaks) == 24


def ambiguous_script(n=40, start=25):
    s = {}
    for i in range(n):
        d = [(1, PERSON_IN)]
        if i >= start:
            d.append((1, (156, 62, 20, 100)))
        s[i] = d
    return s


def test_a_live_webcam_session_and_its_stored_evaluation_agree_on_the_discontinuity(mk):
    env = mk(ambiguous_script())
    sid = start_default(env.c, privacy="on").json()["session_id"]
    for i in range(34):
        post(env.c, sid, i)
        wait_decided(env.c, sid, i + 1)
    live = [e for evs in env.c.get(f"/api/webcam/sessions/{sid}").json()["events"].values() for e in evs if e["severity"] == "YELLOW"]
    run = env.c.post(f"/api/webcam/sessions/{sid}/stop", headers={"X-MG-Next-Seq": "34"}).json()["run_id"]
    stored = json.loads((env.runs / run / "risk" / "evaluation.json").read_text())
    ev_stored = [e for z in stored["zones"].values() for e in z["events"] if e["severity"] == "YELLOW"]
    assert ev_stored and all(e["closed_reason"] == "track_discontinuity" for e in ev_stored)
    assert live and all(e.get("open") is not True and e["closed_reason"] == "track_discontinuity" for e in live)             # the live engine decided the same
    v = env.c.get(f"/api/sessions/{run}/verify?deep=false").json()
    assert v["ok"] and any(c["id"] == "risk_evaluation_reproduces_from_stored_observations" and c["ok"] for c in v["checks"])


def test_feeds_count_measured_discontinuities_and_close_the_episode_for_that_reason(fm):
    amb = {i: ([(1, FPERSON)] + ([(1, (612, 266, 80, 380))] if i >= 90 else [])) for i in range(256)}
    env = fm({1: amb, 2: always(person=FPERSON)}, frames=150)
    a, b = create(env, 1), create(env, 2)
    act(env, a, "start"); act(env, b, "start")
    va, vb = wait_state(env, [a, b], ("ended",))
    assert va["discontinuities"] >= 1 and vb["discontinuities"] == 0                                              # a measured count, zero only where nothing broke
    yellow = [e for e in va["events"] if e["severity"] == "YELLOW"]
    assert yellow and yellow[0]["closed_reason"] == "track_discontinuity" and all(t.startswith(va["feed_id"] + "/") for e in yellow for t in e["tracks"])
    d = env.c.get("/api/monitor/developer").json()
    assert {f["feed_id"]: f["tracking_discontinuities"] for f in d["feeds"]} == {a: va["discontinuities"], b: 0}
