"""Reproduction + regression vectors for the five findings of review 25 (MG-PRODUCT-VERTICAL-03). Synthetic data only. Every vector asserts the CORRECT behaviour and failed on 4ad6c7a."""
import json
import time

import pytest

from mineguard import monitor
from mineguard.alerts import MAX_OCCURRENCES
from test_vertical2_alerts import engine, evt, src
from test_vertical2_dashboards import overview
from test_vertical2_feeds import always, act, create, fm, wait_state  # noqa: F401
from test_vs02_webcam_risk import mk, scene  # noqa: F401
from test_privacy import start_default

pytestmark = pytest.mark.usefixtures("legacy_delete_enabled")   # MG-DELETE-035: dormant removal logic, isolated test server only


# ---------------------------------------------------------------- MG-ALERT-024 (re-raised): attribution capacity
def _attributable(e):
    out = set()
    for a in e.alerts.values():
        out.add(a["source"]["source_id"])
        out |= {o["source_id"] for o in a["occurrences"] if o.get("source_id")}
    return out


def test_no_merged_event_is_ever_left_without_an_attributable_occurrence():
    e, c = engine()
    n = MAX_OCCURRENCES + 10
    sessions = [src("webcam:Cam", f"webcam:s{i}", kind="webcam_session") for i in range(n)]
    for i, s in enumerate(sessions):
        e.observe_source(s, [evt(f"e{i}", "RED", False, end=5)], {})
        c.advance(0.1)
    listed = {o["event_id"] for a in e.alerts.values() for o in a["occurrences"]}
    assert listed == {f"e{i}" for i in range(n)}                                  # every represented event is individually identified
    assert _attributable(e) == {s["source_id"] for s in sessions}                  # and so is every session
    assert all(a["occurrence_total"] == len(a["occurrences"]) for a in e.alerts.values())   # nothing is only counted


def test_unknown_diagnostics_beyond_the_capacity_of_the_carrier_are_not_silently_counted():
    e, c = engine()
    n = MAX_OCCURRENCES + 6
    sessions = [src("webcam:Cam", f"webcam:u{i}", kind="webcam_session") for i in range(n)]
    st = lambda t: {"state": "UNKNOWN", "pending": [], "causes": ["frame_not_analyzed"], "t_us": t}
    for s in sessions:
        e.observe_source(s, [], {"Z1": st(1)})
        e.observe_source(s, [], {"Z1": st(20_000_000)})
        c.advance(0.1)
    assert _attributable(e) >= {s["source_id"] for s in sessions}
    assert all(a["suppressed_count"] <= len(a["occurrences"]) for a in e.alerts.values())


# ---------------------------------------------------------------- MG-STATE-025: an unobserved feed has no current risk
def test_an_inactive_feed_is_reported_unknown_with_its_last_state_kept_apart_as_history(fm):
    env = fm({1: always()}, frames=400, delay={1: 0.01})        # looping and paced: the feed must still be running when it is paused (a fast fake detector ended the old 150-frame run first: a flaky precondition, not a product defect)
    a = create(env, 1, loop=True)
    act(env, a, "start")
    deadline = time.time() + 30
    while time.time() < deadline and env.feeds.feeds[a].counters["analyzed"] < 2:
        time.sleep(0.05)
    act(env, a, "pause")
    row = overview(env).json()["feeds"][0]
    assert row["state"] == "paused" and row["risk"] == "UNKNOWN" and row["last_observed_risk"] in ("GREEN", "YELLOW", "RED", "UNKNOWN")
    act(env, a, "resume")
    row = overview(env).json()["feeds"][0]
    assert row["state"] == "playing" and row["risk"] == row["last_observed_risk"]
    act(env, a, "stop")
    (v,) = wait_state(env, [a], ("stopped", "ended"))
    row = overview(env).json()["feeds"][0]
    assert row["risk"] == "UNKNOWN" and row["last_observed_risk"] is not None


# ---------------------------------------------------------------- MG-TREND-025: genuine series only
def test_trend_severity_series_count_only_genuine_live_alerts_and_report_the_rest_apart():
    now = 1_800_000_000.0
    mk_ = lambda ago, sev, sim=False, loaded=False: {"created_epoch": now - ago, "severity": sev, "simulation": sim, "loaded_from_store": loaded}
    t = monitor.trend([mk_(30, "YELLOW"), mk_(40, "YELLOW", sim=True), mk_(50, "RED", loaded=True), mk_(60, "UNKNOWN", sim=True, loaded=True)], 60, 5, now)
    last = t["buckets"][-1]
    assert (last["RED"], last["YELLOW"], last["UNKNOWN"]) == (0, 1, 0)             # only the one genuine live alert
    assert last["simulated"] == 2 and last["restored"] == 2 and t["simulated_total"] == 2 and t["restored_total"] == 2
    assert t["has_data"] is True
    only_sim = monitor.trend([mk_(30, "RED", sim=True)], 60, 5, now)
    assert only_sim["has_data"] is False and only_sim["simulated_total"] == 1       # a chart of genuine incidents stays empty


# ---------------------------------------------------------------- MG-API-025: strict bodies, finite numbers, pre-parse size cap
def test_unknown_members_are_refused_not_silently_ignored(mk):
    env = mk(scene(person=range(0, 10)))
    pad = {"padding": "x"}
    assert env.c.post("/api/simulations/person-vehicle", json={"base_run_id": "run_" + "0" * 16, **pad}).status_code == 422
    assert env.c.put("/api/runs/run_" + "0" * 16 + "/zones", json={"zones": [], "expected_revision": 0, **pad}).status_code == 422
    assert env.c.post("/api/sessions/delete", json={"ids": [], "confirm": "ELIMINAR", "expected_count": 0, **pad}).status_code == 422
    body = {"width": 640, "height": 360, "interval_ms": 300, "threshold": 0.3, "zones": [], "capture": {"origin": "test_source", "device_label": "x", "user_agent": "y"}}
    assert env.c.post("/api/webcam/sessions", json={**body, **pad}).status_code == 422
    assert env.c.post("/api/webcam/sessions", json={**body, "capture": {**body["capture"], **pad}}).status_code == 422


def test_non_finite_and_out_of_range_numbers_are_refused_before_any_arithmetic(mk):
    env = mk(scene(person=range(0, 10)))
    base = {"width": 640, "height": 360, "interval_ms": 300, "zones": [], "capture": {"origin": "test_source", "device_label": "x", "user_agent": "y"}}
    for thr in ("NaN", "Infinity", "-Infinity"):
        r = env.c.post("/api/webcam/sessions", content='{"threshold": %s, ' % thr + json.dumps(base)[1:], headers={"Content-Type": "application/json"})
        assert r.status_code == 422, thr
    for thr in (-0.1, 1.5):
        assert env.c.post("/api/webcam/sessions", json={**base, "threshold": thr}).status_code == 422


def test_oversize_json_bodies_are_refused_before_they_are_parsed(mk):
    env = mk(scene(person=range(0, 10)))
    big = {"base_run_id": "run_" + "0" * 16, "padding": "x" * 200_000}
    r = env.c.post("/api/simulations/person-vehicle", json=big)
    assert r.status_code == 413 and "body_too_large" in r.text
    r = env.c.put("/api/runs/run_" + "0" * 16 + "/zones", content=b'{"zones":[],"expected_revision":0,"p":"' + b"x" * 200_000 + b'"}', headers={"Content-Type": "application/json"})
    assert r.status_code == 413


def test_normal_sized_requests_and_frame_uploads_are_unaffected(mk):
    env = mk(scene(person=range(0, 10)))
    r = start_default(env.c)
    assert r.status_code == 201


def test_the_observation_import_route_keeps_its_own_larger_but_finite_cap(mk):
    env = mk(scene(person=range(0, 10)))
    pad = "x" * 200_000                                                          # well above the 64 KiB default, far below the route's own cap
    r = env.c.post("/api/observations/import", content=json.dumps({"schema": "mg.obsset.vs01", "pad": pad}), headers={"Content-Type": "application/json"})
    assert r.status_code == 422 and "body_too_large" not in r.text              # judged by the observation validator, not refused for size
    from mineguard.security import BodyLimit
    assert BodyLimit(None, per_path={"/api/observations/import": 32 * 1024 * 1024}).per_path["/api/observations/import"] > 65_536
