"""MG-PRODUCT-VERTICAL-05: review-26 MG-PROV-026 (repeated-source concurrency labels BOTH feeds, launch provenance stays immutable) and MG-TRUST-026 (restored alerts never read as live on any surface).
Synthetic data only (test double detector, scripted frames)."""
import time

import pytest

from mineguard.alerts import AlertEngine
from test_vertical2_alerts import Clock, evt, src
from test_vertical2_feeds import PERSON_IN, VEHICLE_IN, always, act, create, fm, view  # noqa: F401


def prov(env, fid):
    return view(env, fid)["source"]["provenance"]


def wait_alert(env, fid, timeout=30):
    t = time.time()
    while time.time() - t < timeout:
        mine = [a for a in env.alerts.alerts.values() if a["feed_id"] == fid]
        if mine:
            return mine
        time.sleep(0.05)
    raise AssertionError("no alert for " + fid)


# ---------------------------------------------------------------- MG-PROV-026
def test_the_second_feed_of_a_file_relabels_the_first_feed_and_everything_it_already_produced(fm):
    env = fm({1: always(person=PERSON_IN, vehicle=VEHICLE_IN), 2: always(person=PERSON_IN, vehicle=VEHICLE_IN)}, frames=400, delay={1: 0.01, 2: 0.01})
    a = create(env, 1, loop=True)
    act(env, a, "start")
    (alert_a,) = wait_alert(env, a)[:1]
    assert alert_a["simulation"] is False and prov(env, a) == "real_inference" and view(env, a)["shared_now"] is False         # A alone is genuine
    b = create(env, 1, label="Fuente 2", loop=True)
    assert prov(env, a) == "real_inference"                                                                                      # B exists but is not running
    act(env, b, "start")
    time.sleep(0.5)
    va, vb = view(env, a), view(env, b)
    assert va["source"]["provenance"] == vb["source"]["provenance"] == "repeated_source_simulation"                              # BOTH feeds
    assert va["source"]["repeated_source"] is True and va["source"]["physical_camera"] is False and vb["source"]["physical_camera"] is False
    assert va["launch_repeated"] is False and vb["launch_repeated"] is True and va["shared_now"] is True and vb["shared_now"] is True   # launch provenance apart from the present relationship
    assert "not several cameras" in va["source"]["note"] and "cameras exist" not in va["source"]["note"]
    assert alert_a["simulation"] is True and alert_a["source"]["provenance"] == "repeated_source_simulation"                     # the alert A made while alone is re-labelled (monotonic)
    assert all(x["simulation"] for x in env.alerts.alerts.values() if x["feed_id"] in (a, b))
    assert va["counters"]["analyzed"] > 0 and va["state"] == "playing" and vb["state"] == "playing"                              # nothing was reset or stopped
    act(env, a, "stop"); act(env, b, "stop")


def test_a_third_feed_of_another_file_is_independent_of_the_repeated_pair(fm):
    env = fm({1: always(person=PERSON_IN, vehicle=VEHICLE_IN), 2: always(person=PERSON_IN, vehicle=VEHICLE_IN)}, frames=400, delay={1: 0.01, 2: 0.01})
    a, b, c = create(env, 1, loop=True), create(env, 1, label="B", loop=True), create(env, 2, label="C", loop=True)
    for f in (a, b, c):
        act(env, f, "start")
    time.sleep(0.6)
    assert prov(env, a) == prov(env, b) == "repeated_source_simulation" and prov(env, c) == "real_inference"
    assert view(env, c)["shared_now"] is False and view(env, c)["launch_repeated"] is False
    for x in wait_alert(env, c):
        assert x["simulation"] is False and x["source"]["provenance"] == "real_inference"                                       # C's alerts stay genuine
    for f in (a, b, c):
        act(env, f, "stop")


def test_the_present_relationship_goes_back_but_the_evidentiary_label_history_and_alerts_are_never_upgraded(fm):
    env = fm({1: always(person=PERSON_IN, vehicle=VEHICLE_IN)}, frames=400, delay={1: 0.01})
    a = create(env, 1, loop=True)
    act(env, a, "start")
    wait_alert(env, a)
    b = create(env, 1, label="B", loop=True)
    act(env, b, "start"); time.sleep(0.4)
    assert prov(env, a) == "repeated_source_simulation"
    act(env, b, "stop")
    assert view(env, a)["shared_now"] is False and view(env, a)["shared_during_run"] is True and prov(env, a) == "repeated_source_simulation"      # the PRESENT relationship ended; the run's evidence stays a simulation
    first = [x for x in env.alerts.alerts.values() if x["feed_id"] == a][0]
    assert first["simulation"] is True and first["source"]["provenance"] == "repeated_source_simulation"                           # never upgraded back to genuine
    # B restarts: it is again a repeat, and A is labelled again
    act(env, b, "start"); time.sleep(0.4)
    assert view(env, b)["launch_repeated"] is True and prov(env, b) == prov(env, a) == "repeated_source_simulation"
    act(env, a, "stop")
    hist = [r for r in env.c.get("/api/feeds/history").json()["runs"] if r["feed_id"] == a]
    assert hist and hist[0]["source"]["provenance"] == "repeated_source_simulation" and hist[0]["source"]["physical_camera"] is False   # the saved history of A's run says it overlapped
    assert prov(env, b) == "repeated_source_simulation"                                                                             # B's launch provenance is immutable for its run
    act(env, b, "stop")


def test_restarting_a_feed_decides_its_provenance_again_and_does_not_inherit_the_old_overlap(fm):
    env = fm({1: always()}, frames=60)
    a, b = create(env, 1), create(env, 1, label="B")
    act(env, a, "start"); act(env, b, "start")
    from test_vertical2_feeds import wait_state
    wait_state(env, [a, b], ("ended",))
    env.c.delete(f"/api/feeds/{b}")
    act(env, a, "start")                                   # a new run, alone
    time.sleep(0.2)
    v = view(env, a)
    assert v["shared_during_run"] is False and v["launch_repeated"] is False and v["source"]["provenance"] == "real_inference"
    act(env, a, "stop")


def test_provenance_labels_survive_the_dashboard_surface_too(fm):
    env = fm({1: always(person=PERSON_IN, vehicle=VEHICLE_IN)}, frames=400, delay={1: 0.01})
    a = create(env, 1, loop=True); b = create(env, 1, label="B", loop=True)
    act(env, a, "start"); act(env, b, "start"); time.sleep(0.6)
    rows = env.c.get("/api/monitor/overview").json()["feeds"]
    assert {r["provenance"] for r in rows} == {"repeated_source_simulation"} and all(r["repeated_source"] for r in rows)
    tl = env.c.get("/api/monitor/overview").json()
    assert tl["counts_kept_apart"]["risk_events_live"] == {"RED": 0, "YELLOW": 0}                 # nothing of the pair is counted as genuine live
    act(env, a, "stop"); act(env, b, "stop")


# ---------------------------------------------------------------- MG-TRUST-026
def restored_engine(tmp_path, simulated=False):
    e, c = AlertEngine(tmp_path, wall=Clock(), mono=Clock()), None
    if simulated:
        e.ingest_simulation("run_dddddddddddddddd", [evt("s1", "RED", False, end=9)])
    else:
        e.observe_source(src("feed_a", "feed_a:r1"), [evt("e1", "RED", False, end=5)], {})
    e.flush()
    return AlertEngine(tmp_path, wall=Clock(), mono=Clock())


def test_a_restored_genuine_looking_alert_is_never_a_live_alert_on_any_data_surface(fm, tmp_path):
    env = fm({1: always()})
    e2 = restored_engine(tmp_path / "rest")
    (rec,) = e2.alerts.values()
    assert rec["loaded_from_store"] is True
    rec["created_epoch"] = time.time() - 120                                                    # inside the trend window
    env.alerts.alerts.update(e2.alerts); env.alerts.rev = max(env.alerts.rev, e2.rev)       # the routes use the app's engine object: give it the records a restart loaded
    s = env.c.get("/api/alerts/summary").json()
    assert s["loaded_from_disk_unverified"] == 1 and s["by_provenance"] == {} and s["open"] == 0
    ov = env.c.get("/api/monitor/overview").json()
    (row,) = ov["recent_timeline"]
    assert row["loaded_from_store"] is True and row["provenance"] in ("real_inference", "repeated_source_simulation", "synthetic_scripted")     # the flag travels with the row
    tr = env.c.get("/api/monitor/overview", params={"minutes": 1440, "bucket_min": 60}).json()["trend"]
    assert tr["has_data"] is False and sum(b["RED"] + b["YELLOW"] + b["UNKNOWN"] for b in tr["buckets"]) == 0 and tr["restored_total"] == 1      # not a genuine-live trend population
    lst = env.c.get("/api/alerts").json()["alerts"]
    assert all(x["loaded_from_store"] is True for x in lst)


def test_acknowledge_and_resolve_never_upgrade_the_trust_of_a_restored_alert(fm, tmp_path):
    env = fm({1: always()})
    e2 = restored_engine(tmp_path / "rest")
    env.alerts.alerts.update(e2.alerts); env.alerts.rev = max(env.alerts.rev, e2.rev)
    (aid,) = list(e2.alerts)
    before = env.c.get("/api/alerts").json()["alerts"][0]
    for action in ("ack", "resolve"):
        r = env.c.post(f"/api/alerts/{aid}/{action}", json={"expected_rev": env.c.get("/api/alerts").json()["alerts"][0]["rev"]}) if False else env.c.post(f"/api/alerts/{aid}/{action}")
        assert r.status_code in (200, 409), r.text
        cur = env.c.get("/api/alerts").json()["alerts"][0]
        assert cur["loaded_from_store"] is True and cur["source"]["provenance"] == before["source"]["provenance"] and cur["simulation"] == before["simulation"]
    assert env.c.get("/api/alerts/summary").json()["by_provenance"] == {}


def test_a_restored_simulation_stays_a_simulation_and_restored(tmp_path):
    e2 = restored_engine(tmp_path / "rest", simulated=True)
    (a,) = e2.alerts.values()
    assert a["loaded_from_store"] is True and a["simulation"] is True and a["source"]["provenance"] == "synthetic_scripted"
    e3 = AlertEngine(tmp_path / "rest", wall=Clock(), mono=Clock())                       # a second restart does not change anything
    (b,) = e3.alerts.values()
    assert b["loaded_from_store"] is True and b["simulation"] is True


def test_a_live_alert_after_a_restart_is_a_new_record_and_the_restored_one_stays_restored(fm, tmp_path):
    env = fm({1: always()})
    e2 = restored_engine(tmp_path / "rest")
    env.alerts.alerts.update(e2.alerts); env.alerts.rev = max(env.alerts.rev, e2.rev)
    env.alerts.observe_source(src("feed_b", "feed_b:r9"), [evt("e9", "RED", False, end=5)], {})
    flags = sorted((a["source"]["source_id"], bool(a.get("loaded_from_store"))) for a in env.alerts.alerts.values())
    assert flags == [("feed_a:r1", True), ("feed_b:r9", False)]
    s = env.alerts.summary()
    assert s["by_provenance"] == {"real_inference": 1} and s["loaded_from_disk_unverified"] == 1


def test_the_saved_history_of_a_run_that_overlapped_says_so_even_when_it_ends_alone(fm):
    """The overlap ended BEFORE the run closed: only the sticky per-run memory can make the saved summary say it was a repeated-file run."""
    env = fm({1: always(person=PERSON_IN, vehicle=VEHICLE_IN)}, frames=400, delay={1: 0.01})
    a = create(env, 1, loop=True)
    act(env, a, "start")                                                            # launched ALONE
    b = create(env, 1, label="B", loop=True)
    act(env, b, "start"); time.sleep(0.4)
    act(env, b, "stop")
    assert prov(env, a) == "repeated_source_simulation" and view(env, a)["shared_now"] is False         # alone again, but the run already overlapped: its evidence stays a simulation
    act(env, a, "stop")
    hist = [r for r in env.c.get("/api/feeds/history").json()["runs"] if r["feed_id"] == a]
    assert hist and hist[0]["source"]["provenance"] == "repeated_source_simulation" and hist[0]["source"]["repeated_source"] is True


# ---------------------------------------------------------------- MG-PROV-027: evidentiary provenance is sticky for the run; only the PRESENT RELATIONSHIP goes back
def test_after_the_duplicate_stops_the_surviving_run_stays_a_repeated_source_for_every_evidentiary_consumer(fm):
    env = fm({1: always(person=PERSON_IN, vehicle=VEHICLE_IN)}, frames=400, delay={1: 0.01})
    a = create(env, 1, loop=True)
    act(env, a, "start")
    wait_alert(env, a)
    b = create(env, 1, label="B", loop=True)
    act(env, b, "start"); time.sleep(0.5)
    act(env, b, "stop")
    va = view(env, a)
    assert va["shared_now"] is False and va["shared_during_run"] is True                                         # the PRESENT relationship has ended ...
    assert va["source"]["provenance"] == "repeated_source_simulation" and va["source"]["repeated_source"] is True    # ... the evidence of this run is still a repeated-file simulation
    # 1) new alerts built from the surviving feed
    seen = []
    real_observe = env.alerts.observe_source
    env.alerts.observe_source = lambda src, *x, **k: (seen.append(dict(src)), real_observe(src, *x, **k))[1]
    time.sleep(0.4)
    assert seen and all(s["provenance"] == "repeated_source_simulation" and s["repeated_source"] is True for s in seen if s.get("feed_id") == a)
    # 2) the operator aggregates never count the surviving run's retained events as genuine
    ov = env.c.get("/api/monitor/overview").json()
    row = next(r for r in ov["feeds"] if r["feed_id"] == a)
    assert row["provenance"] == "repeated_source_simulation" and row["repeated_source"] is True
    assert ov["counts_kept_apart"]["risk_events_live"] == {"RED": 0, "YELLOW": 0}
    assert ov["counts_kept_apart"]["risk_events_from_simulated_sources"]["RED"] >= 1
    # 3) the saved summary agrees
    act(env, a, "stop")
    hist = [r for r in env.c.get("/api/feeds/history").json()["runs"] if r["feed_id"] == a]
    assert hist and hist[0]["source"]["provenance"] == "repeated_source_simulation"
    # 4) every alert of the run is a simulation, none genuine
    assert all(x["simulation"] is True for x in env.alerts.alerts.values() if x["feed_id"] == a)
    # a RESTART is a new run and decides again; with the other feed removed it is alone
    env.c.delete(f"/api/feeds/{b}")
    act(env, a, "start"); time.sleep(0.3)
    assert view(env, a)["source"]["provenance"] == "real_inference" and view(env, a)["shared_during_run"] is False
    act(env, a, "stop")
