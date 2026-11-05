"""MG-VERTICAL-02 checkpoint B: the multi-feed core (shared model, one worker, bounded per-feed queues, fair scheduling, failure isolation, per-feed zones and identities).
LIGHTWEIGHT: sources and detector are test doubles (synthetic frames whose pixels encode a source code and a frame index; the detector decodes them). They exercise the plumbing,
never model quality. The genuine multi-source model acceptance is a separate, resource-guarded run (docs/MG-VERTICAL2-001)."""
import re
import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest

from conftest import local_client
from mineguard import feeds as feeds_mod
from mineguard.app import create_app
from mineguard.detector import RawDetection, load_manifest_entry

W, H = 1280, 720
ZONE = {"zone_id": "Z1", "kind": "restricted", "polygon": [[400, 200], [1200, 200], [1200, 700], [400, 700]], "name": "Zona 1"}
PERSON_IN = (600, 260, 80, 380)     # bottom centre (640, 640): inside Z1
PERSON_OUT = (50, 260, 80, 380)
VEHICLE_IN = (800, 400, 300, 250)   # bottom centre (950, 650): inside Z1


def frame(i: int, code: int) -> np.ndarray:
    i %= 256   # the index is encoded in two 4-bit blocks of the picture
    a = np.full((H, W, 3), 90, np.uint8)
    a[0:16, 0:16] = (i >> 4) * 16 + 8
    a[0:16, 16:32] = (i & 15) * 16 + 8
    a[0:16, 32:48] = code * 16 + 8
    return a


def decode(rgb):
    return int(rgb[8, 40, 0]) // 16, (int(rgb[8, 8, 0]) // 16) * 16 + int(rgb[8, 24, 0]) // 16


class Double:
    """TEST DOUBLE detector shared by all feeds: records which thread and which source each call served."""

    def __init__(self, scripts, delay=None, fail=None):
        e = load_manifest_entry()
        self.entry, self.weights_sha256, self.device, self.resolution = e, e["sha256"], "cpu", 384
        self.scripts, self.delay, self.fail = scripts, delay or {}, fail or {}
        self.order: list[int] = []
        self.threads: set[int] = set()
        self.calls = 0

    def predict(self, rgb, thr):
        code, idx = decode(rgb)
        self.threads.add(threading.get_ident())
        self.order.append(code)
        self.calls += 1
        time.sleep(self.delay.get(code, 0.0))
        if code in self.fail and idx in self.fail[code]:
            raise RuntimeError("scripted detector failure")
        return [RawDetection(cid, 0.9, (float(x), float(y), float(x + w), float(y + h))) for cid, (x, y, w, h) in self.scripts.get(code, {}).get(idx, [])]


def always(person=None, vehicle=None, n=256):
    out = {}
    for i in range(n):
        d = []
        if person:
            d.append((1, person))
        if vehicle:
            d.append((3, vehicle))
        out[i] = d
    return out


@pytest.fixture
def fm(tmp_path):
    envs = []

    def build(scripts, frames=60, delay=None, fail=None, fail_source=None, detector_factory=None, open_failures=None):
        det = Double(scripts, delay, fail)
        made = {"n": 0}

        def factory():
            made["n"] += 1
            if detector_factory:
                return detector_factory()
            return det
        app = create_app(asset_dir=tmp_path / "assets", runs_dir=tmp_path / "runs", detector_factory=factory)
        st = app.state.st
        codes = {}
        for code in sorted(scripts):
            vid = f"vid_test{code}"
            path = f"/synthetic/{code}.mp4"
            codes[path] = code
            st.videos[vid] = {"video_id": vid, "filename": f"synthetic_{code}.mp4", "path": path, "sha256": f"{code:02x}" * 32, "media": {}}

        opened = {}

        def frames_factory(path):
            code = codes[path]
            opened[code] = opened.get(code, 0) + 1
            if open_failures and opened[code] <= open_failures.get(code, 0):
                raise RuntimeError("decoder refused to start")      # fails BEFORE the first frame
            for i in range(frames):
                if fail_source and fail_source.get(code) is not None and i >= fail_source[code]:
                    raise RuntimeError("scripted source failure")
                yield SimpleNamespace(index=i, pts_ticks=i * 33, timescale=1000, rgb=frame(i, code))
        st.feeds.frames_factory = frames_factory
        env = SimpleNamespace(c=local_client(app), det=det, app=app, st=st, feeds=st.feeds, alerts=st.alerts, runs=tmp_path / "runs", tmp=tmp_path, made=made)
        envs.append(env)
        return env
    yield build
    for e in envs:
        e.feeds.stop_all()


def create(env, code, label=None, **kw):
    body = {"video_id": f"vid_test{code}", "label": label or f"Fuente {code}", "sample_fps": 5.0, "threshold": 0.3, "mode": "throttled", "speed": 1.0, "loop": False, "zones": [ZONE]} | kw
    r = env.c.post("/api/feeds", json=body)
    assert r.status_code == 201, r.text
    return r.json()["feed_id"]


def act(env, fid, what):
    return env.c.post(f"/api/feeds/{fid}/{what}")


def view(env, fid):
    return env.c.get(f"/api/feeds/{fid}").json()


def wait_state(env, fids, states, timeout=40):
    t = time.time()
    while time.time() - t < timeout:
        vs = [view(env, f) for f in fids]
        if all(v["state"] in states for v in vs):
            return vs
        time.sleep(0.05)
    raise AssertionError([(v["feed_id"], v["state"], v["counters"]) for v in (view(env, f) for f in fids)])


def sevs(v):
    return sorted(e["severity"] for e in v["events"])


# ------------------------------------------------------------------ three independent feeds
def test_three_feeds_have_independent_identity_state_events_and_zones(fm):
    env = fm({1: {i: [(1, PERSON_IN)] for i in range(256)}, 2: {i: [(1, PERSON_IN), (3, VEHICLE_IN)] for i in range(256)}, 3: {i: [] for i in range(256)}}, frames=150)
    a, b, c = create(env, 1, "Entrada"), create(env, 2, "Rampa"), create(env, 3, "Taller")
    assert len({a, b, c}) == 3
    for f in (a, b, c):
        assert act(env, f, "start").status_code == 200
    va, vb, vc = wait_state(env, [a, b, c], ("ended",))
    assert "YELLOW" in sevs(va) and "RED" not in sevs(va)                     # person alone in the zone: warning only
    assert "RED" in sevs(vb)                                                 # person AND vehicle in the same zone
    assert sevs(vc) == [] and vc["counters"]["detections"] == 0               # an empty scene produced nothing
    for v, f in ((va, a), (vb, b), (vc, c)):
        assert v["feed_id"] == f and v["counters"]["analyzed"] == v["counters"]["offered"] > 20 and v["counters"]["dropped_queue_overflow"] == 0
    ids = [{e["event_id"] for e in v["events"]} for v in (va, vb, vc)]
    assert not (ids[0] & ids[1]) and not (ids[1] & ids[2])                    # event identities never collide across feeds
    for v in (va, vb):                                                       # tracker ids are namespaced per feed: no track of one feed ever appears in another
        tracks = {t for e in v["events"] for t in e["tracks"]}
        assert all(t.startswith(v["feed_id"] + "/") for t in tracks)       # a RED episode is keyed by the zone, so it may name no track; any named track is this feed's
        assert v["tracks_unique"].get("person", 0) >= 1 and v["latest"]["objects"]
        assert {o["track"].split("/")[0] for o in v["latest"]["objects"] if o["track"]} <= {v["feed_id"]}
    assert {t for e in va["events"] for t in e["tracks"]}
    assert env.made["n"] == 1 and len(env.det.threads) == 1                  # ONE model instance and ONE inference worker thread for all feeds
    assert view(env, a)["source"]["provenance"] == "real_inference" and view(env, a)["source"]["physical_camera"] is False


def test_each_feed_carries_its_own_zone_set_and_risk_state(fm):
    env = fm({1: always(person=PERSON_IN), 2: always(person=PERSON_IN)}, frames=120)
    other = {"zone_id": "Z9", "kind": "restricted", "polygon": [[10, 10], [200, 10], [200, 200], [10, 200]], "name": "Otra"}
    a, b = create(env, 1), create(env, 2, zones=[other])
    for f in (a, b):
        act(env, f, "start")
    va, vb = wait_state(env, [a, b], ("ended",))
    assert set(va["risk"]) == {"Z1"} and set(vb["risk"]) == {"Z9"}
    assert "YELLOW" in sevs(va) and sevs(vb) == []                            # the same detections are outside feed b's own zone


# ------------------------------------------------------------------ independent controls
def test_pause_resume_stop_and_restart_are_independent(fm):
    env = fm({1: always(), 2: always()}, frames=240, delay={1: 0.01, 2: 0.01})
    a, b = create(env, 1, loop=True), create(env, 2, loop=True)
    act(env, a, "start"); act(env, b, "start")
    time.sleep(0.6)
    assert act(env, a, "pause").status_code == 200
    time.sleep(0.4)
    ca, cb = view(env, a)["counters"]["analyzed"], view(env, b)["counters"]["analyzed"]
    time.sleep(0.6)
    assert view(env, a)["state"] == "paused" and view(env, a)["counters"]["analyzed"] <= ca + 2          # a paused feed (in-flight frames only) stops
    assert view(env, b)["state"] == "playing" and view(env, b)["counters"]["analyzed"] > cb               # the other feed keeps going
    assert act(env, a, "resume").status_code == 200
    time.sleep(0.5)
    assert view(env, a)["counters"]["analyzed"] > ca
    assert act(env, b, "stop").status_code == 200
    vb = view(env, b)
    assert vb["state"] == "stopped" and vb["closed_reason"] == "operator_stop" and vb["result_id"]
    assert view(env, a)["state"] == "playing"                                                              # stopping b did not touch a
    old_uid = env.feeds.feeds[b].run_uid
    assert act(env, b, "start").status_code == 200                                                         # independent restart: a NEW run
    time.sleep(0.3)
    vb2 = view(env, b)
    assert vb2["state"] == "playing" and env.feeds.feeds[b].run_uid != old_uid and vb2["counters"]["analyzed"] <= vb["counters"]["analyzed"] + 5
    act(env, a, "stop"); act(env, b, "stop")


def test_illegal_transitions_are_refused(fm):
    env = fm({1: always()}, frames=60)
    a = create(env, 1)
    assert act(env, a, "pause").status_code == 409 and act(env, a, "resume").status_code == 409
    assert env.c.get("/api/feeds/feed_deadbeef").status_code == 404 and env.c.get("/api/feeds/not-a-feed").status_code == 404
    assert env.c.delete(f"/api/feeds/{a}").status_code == 200
    assert env.c.get(f"/api/feeds/{a}").status_code == 404


# ------------------------------------------------------------------ failure isolation
def test_a_failing_source_ends_only_that_feed(fm):
    env = fm({1: always(person=PERSON_IN), 2: always(), 3: always(person=PERSON_IN)}, frames=120, fail_source={2: 8})
    fids = [create(env, c) for c in (1, 2, 3)]
    for f in fids:
        act(env, f, "start")
    va, vb, vc = wait_state(env, fids, ("ended", "failed"))
    assert vb["state"] == "failed" and vb["error"] == "RuntimeError" and vb["closed_reason"] == "source_failed"
    assert va["state"] == "ended" and vc["state"] == "ended" and va["counters"]["analyzed"] > 10 and vc["counters"]["analyzed"] > 10
    assert env.feeds.snapshot()["scheduler"]["alive"] in (True, False) and va["error"] is None and vc["error"] is None


def test_a_detector_error_on_one_frame_is_recorded_for_its_feed_only(fm):
    env = fm({1: always(), 2: always()}, frames=90, fail={2: {7, 14}})
    a, b = create(env, 1), create(env, 2)
    act(env, a, "start"); act(env, b, "start")
    va, vb = wait_state(env, [a, b], ("ended",))
    assert va["counters"]["adapter_error"] == 0 and vb["counters"]["adapter_error"] >= 1
    assert vb["counters"]["analyzed"] + vb["counters"]["adapter_error"] == vb["counters"]["offered"]       # accounted, not replaced by invented output


def test_an_unavailable_model_fails_the_feeds_cleanly_and_releases_the_lock(fm):
    def broken():
        raise OSError("weights missing")
    env = fm({1: always()}, detector_factory=broken)
    a = create(env, 1)
    act(env, a, "start")
    (v,) = wait_state(env, [a], ("failed", "stopped", "ended"))
    assert v["state"] == "failed" and v["closed_reason"] == "model_unavailable" and v["counters"]["analyzed"] == 0
    time.sleep(0.3)
    assert not env.st.infer_lock.locked()


# ------------------------------------------------------------------ bounded queues, backpressure, staleness
def test_queues_stay_bounded_and_every_offered_frame_is_accounted(fm):
    env = fm({1: always(person=PERSON_IN), 2: always(person=PERSON_IN), 3: always(person=PERSON_IN)}, frames=240, delay={1: 0.12, 2: 0.12, 3: 0.12})
    fids = [create(env, c, mode="realtime", speed=8.0, sample_fps=10.0) for c in (1, 2, 3)]
    for f in fids:
        act(env, f, "start")
    vs = wait_state(env, fids, ("ended",), timeout=90)
    for v in vs:
        c = v["counters"]
        assert v["max_queue_depth"] <= feeds_mod.FEED_QUEUE_MAX
        assert c["dropped_queue_overflow"] > 0                                                              # overload is visible, not hidden
        assert c["offered"] == c["analyzed"] + c["dropped_queue_overflow"] + c["dropped_stale"] + c["adapter_error"] + c["dropped_stopped"]
        assert c["decoded"] >= c["offered"] and c["decoded"] == c["offered"] + c["skipped_by_sampling"]
        assert v["risk"] and all(s["state"] in ("GREEN", "YELLOW", "RED", "UNKNOWN") for s in v["risk"].values())


def test_throttled_backpressure_drops_nothing(fm):
    env = fm({1: always(), 2: always()}, frames=90, delay={1: 0.03, 2: 0.03})
    a, b = create(env, 1), create(env, 2)
    act(env, a, "start"); act(env, b, "start")
    for v in wait_state(env, [a, b], ("ended",)):
        c = v["counters"]
        assert c["dropped_queue_overflow"] == 0 and c["dropped_stale"] == 0 and c["analyzed"] == c["offered"] > 10


def test_a_result_that_is_stale_at_the_commit_is_dropped_and_never_current(fm, monkeypatch):
    monkeypatch.setattr(feeds_mod, "FEED_MAX_AGE_US", 120_000)
    env = fm({1: always(person=PERSON_IN), 2: always(person=PERSON_IN)}, frames=90, delay={1: 0.25, 2: 0.25})
    a, b = create(env, 1), create(env, 2)
    act(env, a, "start"); act(env, b, "start")
    for v in wait_state(env, [a, b], ("ended",)):
        c = v["counters"]
        assert c["dropped_stale"] > 0 and c["analyzed"] + c["dropped_stale"] == c["offered"]
        assert v["latest"]["index"] is None or v["latest"]["analyzed_at"] is not None


# ------------------------------------------------------------------ fairness
def test_the_scheduler_serves_ready_feeds_in_least_recently_served_order(fm):
    env = fm({1: always(), 2: always(), 3: always()}, frames=90, delay={1: 0.02, 2: 0.02, 3: 0.02})
    fids = [create(env, c) for c in (1, 2, 3)]
    for f in fids:
        act(env, f, "start")
    vs = wait_state(env, fids, ("ended",))
    order = env.det.order
    assert env.det.calls == sum(v["counters"]["analyzed"] for v in vs)
    window = 3
    starved = [i for i in range(5, len(order) - 8 - window) if len(set(order[i:i + window + 3])) < 3]
    assert len(starved) <= 3, (starved, order[:40])                                                         # no feed waits more than a few services while all three are ready
    shares = {c: order.count(c) for c in (1, 2, 3)}
    assert max(shares.values()) - min(shares.values()) <= 3                                                  # equal workloads get equal service
    assert vs[0]["queue_wait_ms"]["p95"] is not None


def test_a_slow_feed_does_not_starve_the_others(fm):
    env = fm({1: always(), 2: always(), 3: always()}, frames=200, delay={1: 0.2})
    fids = [create(env, c, mode="realtime", speed=2.0, sample_fps=10.0) for c in (1, 2, 3)]
    for f in fids:
        act(env, f, "start")
    vs = wait_state(env, fids, ("ended",), timeout=90)
    assert vs[1]["counters"]["analyzed"] >= 5 and vs[2]["counters"]["analyzed"] >= 5                        # the fast feeds were served throughout the slow feed's inference
    assert vs[0]["counters"]["analyzed"] >= 3


# ------------------------------------------------------------------ zones per feed
def test_zone_edits_are_per_feed_compare_and_swap_and_keep_old_events(fm):
    env = fm({1: always(person=PERSON_IN), 2: always(person=PERSON_IN)}, frames=240, delay={1: 0.01, 2: 0.01})
    a, b = create(env, 1, loop=True), create(env, 2, loop=True)
    act(env, a, "start"); act(env, b, "start")
    t = time.time()
    while time.time() - t < 30 and not any(e["severity"] == "YELLOW" for e in view(env, a)["events"]):
        time.sleep(0.1)
    va = view(env, a)
    assert va["zones_revision"] == 1 and sevs(va)
    far = {"zone_id": "Z2", "kind": "restricted", "polygon": [[10, 10], [200, 10], [200, 100], [10, 100]], "name": "Lejos"}
    assert env.c.put(f"/api/feeds/{a}/zones", json={"zones": [far], "expected_revision": 5}).status_code == 409
    r = env.c.put(f"/api/feeds/{a}/zones", json={"zones": [far], "expected_revision": 1})
    assert r.status_code == 200 and r.json()["zones_revision"] == 2 and set(r.json()["risk"]) <= {"Z2"}
    assert any(e.get("closed_by") == "zone_edit" for e in view(env, a)["events"])                            # the earlier episode is kept, right-censored
    assert view(env, b)["zones_revision"] == 1 and set(view(env, b)["zones"][0]["zone_id"] for _ in [0]) == {"Z1"}
    bad = {"zone_id": "Z3", "kind": "restricted", "polygon": [[0, 0], [100, 100], [100, 0], [0, 100]], "name": "Cruzada"}
    assert env.c.put(f"/api/feeds/{b}/zones", json={"zones": [bad], "expected_revision": 1}).status_code == 422
    assert view(env, b)["zones_revision"] == 1
    act(env, a, "stop"); act(env, b, "stop")


# ------------------------------------------------------------------ capacity and honesty about sources
def test_capacity_limits_and_unvalidated_counts_need_an_explicit_acknowledgement(fm):
    env = fm({i: always() for i in range(1, 6)}, frames=200, delay={i: 0.01 for i in range(1, 6)})
    fids = [create(env, c, loop=True) for c in range(1, 6)]
    assert env.c.post("/api/feeds", json={"video_id": "vid_test1", "zones": []}).status_code == 409           # at most MAX_FEEDS exist
    for f in fids[:3]:
        assert act(env, f, "start").status_code == 200
    assert act(env, fids[3], "start").status_code == 409                                                     # default active limit = the validated count (3)
    assert env.c.post("/api/feeds/capacity", json={"limit": 4}).status_code == 409                            # not validated: needs acknowledgement
    assert env.c.post("/api/feeds/capacity", json={"limit": 4, "acknowledge_unvalidated": True}).json() == {"active_limit": 4}
    assert act(env, fids[3], "start").status_code == 200
    assert env.c.post("/api/feeds/capacity", json={"limit": 9, "acknowledge_unvalidated": True}).status_code == 422
    assert env.c.get("/api/feeds").json()["validated_feeds"] == 3
    env.feeds.stop_all()


def test_the_same_file_in_two_feeds_is_labelled_a_repeated_source_simulation(fm):
    env = fm({1: always()}, frames=40)
    a = create(env, 1)
    assert view(env, a)["source"]["repeated_source"] is False and view(env, a)["source"]["provenance"] == "real_inference"
    b = create(env, 1, label="Copia")
    for f in (a, b):                                                                    # MG-PROV-035: two IDLE feeds of one file are not concurrent monitoring
        assert view(env, f)["source"]["repeated_source"] is False and view(env, f)["source"]["provenance"] == "real_inference"
    act(env, a, "start")
    act(env, b, "start")
    time.sleep(0.3)
    for f in (a, b):                                                                    # both ACTIVE: now it is a repeated-source simulation
        s = view(env, f)["source"]
        assert s["repeated_source"] is True and s["provenance"] == "repeated_source_simulation" and s["physical_camera"] is False and "not several cameras" in s["note"]
    act(env, a, "stop")
    act(env, b, "stop")


def test_browser_camera_sources_are_an_interface_only(fm):
    env = fm({1: always()})
    r = env.c.post("/api/feeds", json={"video_id": "vid_test1", "source_kind": "browser_camera", "zones": []})
    assert r.status_code == 501 and r.json()["detail"]["msg_id"] == "mg.refusal.feed_source_not_available"
    assert env.c.post("/api/feeds", json={"video_id": "vid_test1", "source_kind": "rtsp", "zones": []}).status_code == 422
    assert env.c.post("/api/feeds", json={"video_id": "vid_unknown", "zones": []}).status_code == 404
    assert env.c.post("/api/feeds", json={"video_id": "vid_test1", "mode": "x", "zones": []}).status_code == 422


def test_source_time_is_monotonic_across_loop_passes(fm):
    env = fm({1: always(person=PERSON_IN)}, frames=60, delay={1: 0.005})
    a = create(env, 1, loop=True)
    act(env, a, "start")
    t = time.time()
    while time.time() - t < 30 and view(env, a)["passes"] < 2:
        time.sleep(0.1)
    v = view(env, a)
    assert v["passes"] >= 2 and v["source_time_us"] > 60 * 33_000                                              # source time kept growing past one pass
    act(env, a, "stop")


# ------------------------------------------------------------------ privacy and history
def test_a_feed_never_writes_a_frame_and_keeps_only_a_metadata_summary(fm):
    env = fm({1: always(person=PERSON_IN)}, frames=150, delay={1: 0.05})
    a = create(env, 1)
    act(env, a, "start")
    deadline = time.time() + 20
    while time.time() < deadline and env.c.get(f"/api/feeds/{a}/preview.jpg").status_code != 200:
        time.sleep(0.05)
    r = env.c.get(f"/api/feeds/{a}/preview.jpg")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg" and r.headers["cache-control"] == "no-store" and r.content[:3] == b"\xff\xd8\xff"
    (v,) = wait_state(env, [a], ("ended",))
    assert env.c.get(f"/api/feeds/{a}/preview.jpg").status_code == 404                                         # the in-memory frame is released with the feed
    files = [p for p in env.tmp.rglob("*") if p.is_file()]
    assert files and all(p.suffix == ".json" for p in files if "runs" in p.parts), [str(p) for p in files]
    for p in files:
        b = p.read_bytes()
        assert not b.startswith((b"\xff\xd8\xff", b"\x89PNG")) and b"/9j/" not in b
    hist = env.c.get("/api/feeds/history").json()
    assert hist["retention"] == {"mode": "metadata_only", "frames_persisted": False} and len(hist["runs"]) == 1
    run = hist["runs"][0]
    assert run["trust"] == "live_summary" and run["origin"] == "model_inference_live_feed" and run["id"] == v["result_id"] and run["retention"]["frames_persisted"] is False


def test_saved_feed_summaries_are_deleted_only_with_exact_confirmation(fm):
    env = fm({1: always(), 2: always()}, frames=40)
    for c in (1, 2):
        f = create(env, c)
        act(env, f, "start")
        wait_state(env, [f], ("ended",))
    runs = env.c.get("/api/feeds/history").json()["runs"]
    ids = [r["id"] for r in runs]
    assert len(ids) == 2
    d = lambda **k: env.c.post("/api/feeds/history/delete", json={"ids": ids[:1], "confirm": "ELIMINAR", "expected_count": 1} | k)
    st = lambda r: [(x["status"]) for x in r.json()["results"]]
    assert d(confirm="eliminar").status_code == 422 and d(expected_count=2).status_code == 409
    bad = d(ids=["../x", "feedrun_zz"], expected_count=2).json()["results"]
    assert bad == [{"index": 0, "id": None, "status": "refused_bad_id"}, {"index": 1, "id": None, "status": "refused_bad_id"}]      # invalid ids are reported by position, never echoed
    r = d()
    assert st(r) == ["deleted"] and r.json()["results"][0]["id"] == ids[0]
    left = [r["id"] for r in env.c.get("/api/feeds/history").json()["runs"]]
    assert left == ids[1:]
    assert st(d()) == ["missing"]


# ------------------------------------------------------------------ shared model exclusivity
def test_a_running_feed_blocks_other_model_users_and_releases_them_when_it_ends(fm):
    env = fm({1: always()}, frames=400, delay={1: 0.02})
    a = create(env, 1, loop=True)
    act(env, a, "start")
    time.sleep(0.3)
    body = {"width": 320, "height": 180, "interval_ms": 100, "threshold": 0.3, "zones": [], "capture": {"origin": "test_source", "device_label": "x", "user_agent": "t"}}
    assert env.c.post("/api/webcam/sessions", json=body).status_code == 409                                    # the single shared model is busy
    act(env, a, "stop")
    t = time.time()
    while time.time() - t < 5 and env.st.infer_lock.locked():
        time.sleep(0.1)
    assert not env.st.infer_lock.locked()
    r = env.c.post("/api/webcam/sessions", json=body)
    assert r.status_code == 201
    env.c.post(f"/api/webcam/sessions/{r.json()['session_id']}/stop")


def test_a_running_webcam_session_blocks_feeds(fm):
    env = fm({1: always()}, frames=40)
    body = {"width": 320, "height": 180, "interval_ms": 100, "threshold": 0.3, "zones": [], "capture": {"origin": "test_source", "device_label": "x", "user_agent": "t"}}
    r = env.c.post("/api/webcam/sessions", json=body)
    assert r.status_code == 201
    a = create(env, 1)
    assert act(env, a, "start").status_code == 409
    env.c.post(f"/api/webcam/sessions/{r.json()['session_id']}/stop")


def test_a_source_that_fails_before_its_first_frame_is_retried_a_bounded_number_of_times(fm, monkeypatch):
    monkeypatch.setattr(feeds_mod, "SOURCE_RETRY_DELAY_S", 0.05)
    env = fm({1: always(), 2: always()}, frames=60, open_failures={1: 2, 2: 99})
    a, b = create(env, 1), create(env, 2)
    act(env, a, "start"); act(env, b, "start")
    va, vb = wait_state(env, [a, b], ("ended", "failed"))
    assert va["state"] == "ended" and va["source_retries"] == 2 and va["counters"]["analyzed"] > 5            # recovered on the third attempt
    assert vb["state"] == "failed" and vb["source_retries"] == feeds_mod.SOURCE_OPEN_RETRIES and vb["error"] == "RuntimeError" and "decoder refused" in vb["error_detail"]


def test_a_stop_is_always_reported_as_an_operator_stop_even_when_the_reader_finishes_first(fm):
    """Regression (found by the browser suite): the scheduler's reaper could close a feed whose reader had ended BECAUSE it was stopped, labelling it «end of stream»."""
    env = fm({1: always(), 2: always()}, frames=400, delay={1: 0.002, 2: 0.002})
    for _ in range(12):
        a = create(env, 1, loop=True)
        act(env, a, "start")
        time.sleep(0.15)
        v = act(env, a, "stop").json()
        assert v["state"] == "stopped" and v["closed_reason"] == "operator_stop", (v["closed_reason"], v["error"], v["error_detail"])
        env.c.delete(f"/api/feeds/{a}")


def test_the_cold_start_of_the_shared_model_never_turns_the_first_frames_stale(fm, monkeypatch):
    monkeypatch.setattr(feeds_mod, "FEED_MAX_AGE_US", 300_000)
    det = Double({1: always()})

    def slow_factory():
        time.sleep(1.0)      # a cold model load, far longer than the freshness bound
        return det
    env = fm({1: always()}, frames=60, detector_factory=slow_factory)
    a = create(env, 1)
    act(env, a, "start")
    time.sleep(0.3)
    assert view(env, a)["model_loading"] is True and view(env, a)["counters"]["offered"] == 0
    (v,) = wait_state(env, [a], ("ended",))
    assert v["counters"]["dropped_stale"] == 0 and v["counters"]["analyzed"] == v["counters"]["offered"] > 5 and v["model_loading"] is False


def test_a_finished_feed_never_shows_its_final_state_without_its_summary_identity(fm):
    """Regression (found by the full run): `ended` used to be visible a moment before `result_id` existed."""
    env = fm({1: always(person=PERSON_IN)}, frames=40)
    for _ in range(15):
        a = create(env, 1)
        act(env, a, "start")
        t = time.time()
        while time.time() - t < 20:
            v = view(env, a)
            if v["state"] in ("ended", "stopped", "failed"):
                assert v["result_id"] is not None and v["closed_reason"] is not None, v
                break
        env.c.delete(f"/api/feeds/{a}")


# ------------------------------------------------------------------ cycle-22 review B: ALERT-025 / PROV-022 / STORE-022 (feed side)
def test_a_zone_edit_keeps_the_alert_source_so_the_alert_closes_and_no_twin_appears(fm):
    """MG-ALERT-025: rotating the engine identity must not rotate the alert source."""
    env = fm({1: always(person=PERSON_IN, vehicle=VEHICLE_IN)}, frames=400, delay={1: 0.01})
    a = create(env, 1, loop=True)
    act(env, a, "start")
    t = time.time()
    while time.time() - t < 30 and not any(x["severity"] == "RED" for x in env.alerts.alerts.values()):
        time.sleep(0.05)
    before = [x for x in env.alerts.alerts.values() if x["feed_id"] == a]
    assert before and all(x["status"] != "resolved" for x in before)
    uid = env.feeds.feeds[a].run_uid
    far = {"zone_id": "Z2", "kind": "restricted", "polygon": [[10, 10], [200, 10], [200, 100], [10, 100]], "name": "Lejos"}
    assert env.c.put(f"/api/feeds/{a}/zones", json={"zones": [far], "expected_revision": 1}).status_code == 200
    assert env.feeds.feeds[a].run_uid == uid                                                   # the RUN identity did not change
    t = time.time()
    while time.time() - t < 10 and any(x["status"] != "resolved" for x in env.alerts.alerts.values() if x["event_id"] in {e["event_id"] for e in before}):
        time.sleep(0.05)
    assert all(x["status"] == "resolved" for x in before)                                      # the old episodes were closed by the edit and their alerts followed
    act(env, a, "stop")
    mine = [x for x in env.alerts.alerts.values() if x["feed_id"] == a]
    assert len({x["source"]["source_id"] for x in mine}) == 1 and all(x["status"] == "resolved" for x in mine)


def test_the_provenance_of_a_run_is_frozen_when_it_starts(fm):
    """MG-PROV-022: creating or removing another feed of the same file must not relabel a run that is already going."""
    env = fm({1: always(person=PERSON_IN, vehicle=VEHICLE_IN)}, frames=400, delay={1: 0.01})
    a = create(env, 1, loop=True)
    assert view(env, a)["source"]["provenance"] == "real_inference"
    act(env, a, "start")
    t = time.time()
    while time.time() - t < 30 and not any(x["feed_id"] == a for x in env.alerts.alerts.values()):
        time.sleep(0.05)
    alert = next(x for x in env.alerts.alerts.values() if x["feed_id"] == a)
    assert alert["simulation"] is False and alert["source"]["provenance"] == "real_inference"
    b = create(env, 1, label="Copia", loop=True)
    assert view(env, a)["source"]["repeated_source"] is False and view(env, a)["source"]["provenance"] == "real_inference"      # B exists but is not running: A is still alone
    assert view(env, b)["source"]["repeated_source"] is True                                    # B (not started) follows the feeds that exist
    act(env, b, "start")
    time.sleep(0.4)
    # cycle-26 MG-PROV-026 (supersedes the cycle-22 expectation that A stays «real»): while B plays the same file BOTH feeds are a repeated-source simulation
    assert view(env, b)["source"]["provenance"] == "repeated_source_simulation" and view(env, a)["source"]["provenance"] == "repeated_source_simulation"
    assert view(env, a)["launch_repeated"] is False and view(env, a)["shared_now"] is True and view(env, b)["launch_repeated"] is True      # immutable launch provenance is kept apart from the present relationship
    assert alert["simulation"] is True and alert["source"]["provenance"] == "repeated_source_simulation" and all(x["simulation"] for x in env.alerts.alerts.values() if x["feed_id"] == b)
    act(env, b, "stop")
    env.c.delete(f"/api/feeds/{b}")
    assert view(env, a)["source"]["provenance"] == "repeated_source_simulation" and view(env, a)["shared_now"] is False and view(env, a)["shared_during_run"] is True      # cycle-27 MG-PROV-027: the PRESENT relationship ended, the run's evidence stays a simulation
    assert alert["simulation"] is True                                                           # an alert is never upgraded back to genuine
    act(env, a, "stop")
    own = [r for r in env.c.get("/api/feeds/history").json()["runs"] if r["feed_id"] == a]
    assert own and own[0]["source"]["provenance"] == "repeated_source_simulation" and own[0]["trust"] == "live_summary"     # the saved history of the run says it overlapped


def test_a_restart_gives_the_feed_a_new_run_identity_and_a_new_provenance_decision(fm):
    env = fm({1: always()}, frames=40)
    a = create(env, 1)
    act(env, a, "start")
    wait_state(env, [a], ("ended",))
    f = env.feeds.feeds[a]
    uid = f.run_uid
    b = create(env, 1, label="Otra")
    act(env, a, "start")
    time.sleep(0.2)
    assert f.run_uid != uid and view(env, a)["source"]["repeated_source"] is False      # MG-PROV-035: the other feed exists but is idle
    act(env, a, "stop")


def test_resuming_after_a_long_pause_does_not_replay_the_lost_time_in_a_burst(fm):
    """Found by the author's own audit: the pacing clock must restart when a paused feed resumes."""
    env = fm({1: always()}, frames=900, delay={1: 0.002})
    a = create(env, 1, mode="realtime", sample_fps=5.0, loop=True)
    act(env, a, "start")
    time.sleep(1.0)
    act(env, a, "pause")
    time.sleep(0.3)
    before = view(env, a)["counters"]
    time.sleep(2.0)                                                                           # two seconds of source time pass while paused
    act(env, a, "resume")
    time.sleep(0.8)
    after = view(env, a)["counters"]
    assert after["offered"] - before["offered"] <= 8, (before, after)                         # ~5 per second x 0.8 s, never the ~10 frames a catch-up would offer
    assert after["dropped_queue_overflow"] - before["dropped_queue_overflow"] <= 1 and after["dropped_stale"] == before["dropped_stale"]
    act(env, a, "stop")


# ------------------------------------------------------------------ cycle-23 review C: MG-FEED-023 (containment of unexpected internal errors)
def _wait_lock_free(env, secs=8):
    t = time.time()
    while time.time() - t < secs and env.st.infer_lock.locked():
        time.sleep(0.05)
    return not env.st.infer_lock.locked()


def test_an_unexpected_error_before_settlement_ends_that_feed_explicitly_and_frees_the_model(fm, monkeypatch):
    env = fm({1: always(person=PERSON_IN), 2: always(person=PERSON_IN)}, frames=150)
    a, b = create(env, 1), create(env, 2)
    real = feeds_mod.Feed.process

    def boom(self, item, det):
        if self.fid == a and self.counters["analyzed"] >= 3:
            raise RuntimeError("injected before settlement")
        return real(self, item, det)
    monkeypatch.setattr(feeds_mod.Feed, "process", boom)
    act(env, a, "start"); act(env, b, "start")
    va, vb = wait_state(env, [a, b], ("ended", "failed"))
    assert va["state"] == "failed" and va["closed_reason"] == "internal_error" and va["error"] == "RuntimeError" and "injected" in va["error_detail"] and va["result_id"]
    assert vb["state"] == "ended" and vb["error"] is None and vb["counters"]["analyzed"] == vb["counters"]["offered"] > 10                  # the other feed was not affected
    assert va["queue_depth"] == 0 and _wait_lock_free(env)                                      # nothing stranded, the shared lock is released


def test_an_error_while_a_multi_zone_sample_is_consumed_fails_the_feed_instead_of_leaving_zones_divergent(fm, monkeypatch):
    z2 = {"zone_id": "Z2", "kind": "restricted", "polygon": [[10, 10], [300, 10], [300, 300], [10, 300]], "name": "Dos"}
    env = fm({1: always(person=PERSON_IN), 2: always(person=PERSON_IN)}, frames=150)
    a, b = create(env, 1, zones=[ZONE, z2]), create(env, 2)
    real = feeds_mod.Feed._consume
    n = {"c": 0}

    def flaky(self, rec):
        if self.fid == a:
            n["c"] += 1
            if n["c"] == 4:
                raise ValueError("injected while consuming")
        return real(self, rec)
    monkeypatch.setattr(feeds_mod.Feed, "_consume", flaky)
    act(env, a, "start"); act(env, b, "start")
    va, vb = wait_state(env, [a, b], ("ended", "failed"))
    assert va["state"] == "failed" and va["closed_reason"] == "internal_error" and va["error"] == "ValueError" and va["result_id"]
    assert vb["state"] == "ended" and vb["counters"]["analyzed"] == vb["counters"]["offered"]
    assert _wait_lock_free(env)
    assert all(env.feeds.feeds[a].engines[z]._finished for z in env.feeds.feeds[a].engines)    # every zone engine of the failed feed was closed together


def test_a_failure_in_the_scheduler_loop_itself_does_not_kill_the_only_inference_thread(fm, monkeypatch):
    env = fm({1: always(), 2: always()}, frames=90)
    real = feeds_mod.FeedManager._pick
    n = {"c": 0}

    def flaky(self):
        n["c"] += 1
        if n["c"] in (3, 7):
            raise RuntimeError("injected in the loop")
        return real(self)
    monkeypatch.setattr(feeds_mod.FeedManager, "_pick", flaky)
    a, b = create(env, 1), create(env, 2)
    act(env, a, "start"); act(env, b, "start")
    va, vb = wait_state(env, [a, b], ("ended",))
    assert va["state"] == vb["state"] == "ended" and env.feeds.sched_stats.get("loop_errors", 0) >= 2 and _wait_lock_free(env)
