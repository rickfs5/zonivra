"""MG-PRODUCT-VERTICAL-07: recorded SOURCE LIFECYCLE, the complete incident timeline and the export that carries it.
Expectations come from the stated product rules (only what the application itself did or analysed is recorded; nothing between two samples; older / scripted / restored records say "not recorded";
UNKNOWN is never GREEN; one feed's lifecycle never leaks into another), not from output. Test doubles only (scripted frames, fake detector); no owner storage."""
import time

from mineguard import feeds as feeds_mod, incident
from test_rc1_closeout import iso  # noqa: F401
from test_vertical2_alerts import Clock, evt, src
from test_vertical2_feeds import PERSON_IN, VEHICLE_IN, always, act, create, fm, view, wait_state  # noqa: F401
from test_vertical05_provenance import wait_alert
from test_product_vertical06 import sim_incident
from mineguard.alerts import AlertEngine


def kinds(life):
    return [e["kind"] for e in life["events"]]


def life_of(env, fid):
    f = env.feeds.get(fid)
    return f.life_doc()


def inc_of(env, alert):
    return env.c.get(f"/api/incidents/{alert['alert_id']}").json()


# ------------------------------------------------------------------ the feed records what it did and saw
def test_a_run_records_start_pause_resume_stop_and_the_observation_edges_in_order(fm):
    env = fm({1: always(person=PERSON_IN, vehicle=VEHICLE_IN)}, frames=400, delay={1: 0.01})
    a = create(env, 1, loop=True)
    assert life_of(env, a)["events"] == []                                           # an idle feed has recorded nothing: no invented history
    act(env, a, "start")
    wait_alert(env, a)
    assert act(env, a, "pause").status_code == 200
    assert act(env, a, "resume").status_code == 200
    act(env, a, "stop")
    life = life_of(env, a)
    ks = kinds(life)
    assert ks[0] == "source_started" and ks[-1] == "source_stopped"
    assert ks.index("source_paused") < ks.index("source_resumed") < ks.index("source_stopped")
    assert ks.count("observation_received") == 1 and ks.count("person_detected") == 1 and ks.count("vehicle_detected") == 1   # EDGES, not one record per sample
    assert [e["seq"] for e in life["events"]] == list(range(len(life["events"])))
    obs = [e for e in life["events"] if e["group"] == "observed"]
    assert all(e["t_us"] is not None for e in obs) and [e["t_us"] for e in obs] == sorted(e["t_us"] for e in obs)
    assert any(e["kind"] == "risk_changed" and e["to"] == "RED" for e in obs)
    first_red = next(e for e in obs if e["kind"] == "risk_changed" and e["to"] == "RED")
    assert first_red["from"] in ("YELLOW", "GREEN", "UNKNOWN", None)
    assert all(e["run_uid"] == life["run_uid"] for e in life["events"]) and life["truncated"] is False


def test_the_observed_group_cap_can_never_push_the_terminal_lifecycle_record_out(fm):
    env = fm({1: always()}, frames=5)
    a = create(env, 1)
    f = env.feeds.get(a)
    with f.lock:
        for i in range(feeds_mod.LIFE_CAP_OBSERVED + 25):
            f._life("risk_changed", "observed", i, zone_id="Z1", **{"from": "GREEN", "to": "YELLOW"})
        assert f.life_truncated is True
        f._life("source_stopped", "lifecycle", None, reason="operator_stop")
    ks = kinds(f.life_doc())
    assert ks[-1] == "source_stopped" and ks.count("risk_changed") == feeds_mod.LIFE_CAP_OBSERVED


def test_a_failing_source_records_its_failure_and_another_feed_is_unaffected(fm):
    env = fm({1: always(person=PERSON_IN), 2: always()}, frames=120, fail_source={2: 8}, delay={1: 0.002})
    a, b = create(env, 1, loop=True), create(env, 2)
    act(env, a, "start")
    act(env, b, "start")
    wait_state(env, [b], {"failed"})
    t = time.time()
    while not env.feeds.get(b)._closed and time.time() - t < 20:                         # the failure is recorded when it happens; the run closes after its queued samples are settled
        time.sleep(0.05)
    lb = life_of(env, b)
    assert kinds(lb)[0] == "source_started" and kinds(lb).count("source_failed") == 1                                       # recorded once (reader failure), not again at close
    failed = next(e for e in lb["events"] if e["kind"] == "source_failed")
    assert failed["error"] == "RuntimeError" and failed["group"] == "lifecycle"
    assert all(e["seq"] <= failed["seq"] or e["group"] == "observed" for e in lb["events"])                                  # nothing but already-queued samples is recorded after the failure
    assert view(env, a)["state"] == "playing" and "source_failed" not in kinds(life_of(env, a))
    act(env, a, "stop")
    assert kinds(life_of(env, a))[-1] == "source_stopped" and "source_stopped" not in kinds(lb)       # stopping one source records on that source only


def test_a_restarted_feed_keeps_the_lifecycle_of_the_old_run_for_the_old_incident(fm):
    env = fm({1: always(person=PERSON_IN, vehicle=VEHICLE_IN)}, frames=400, delay={1: 0.01})
    a = create(env, 1, loop=True)
    act(env, a, "start")
    (old,) = wait_alert(env, a)[:1]
    act(env, a, "stop")
    run1 = old["source"]["source_id"].split(":", 1)[1]
    act(env, a, "start")                                                              # independent restart = a NEW run
    time.sleep(0.3)
    assert env.feeds.get(a).run_uid != run1
    inc = inc_of(env, old)
    assert inc["timeline"]["lifecycle"]["recorded"] is True and inc["timeline"]["lifecycle"]["run_uid"] == run1
    pk = [e["kind"] for e in inc["timeline"]["processing"]]
    assert "source_started" in pk and "source_stopped" in pk and pk.count("source_started") == 1             # the NEW run's start never lends itself to the old incident
    act(env, a, "stop")


# ------------------------------------------------------------------ the incident projects it, and says what is not recorded
def test_a_feed_incident_carries_both_tracks_with_the_recorded_lifecycle(fm):
    env = fm({1: always(person=PERSON_IN, vehicle=VEHICLE_IN)}, frames=400, delay={1: 0.01})
    a = create(env, 1, loop=True)
    act(env, a, "start")
    (al,) = wait_alert(env, a)[:1]
    act(env, a, "stop")
    inc = inc_of(env, al)
    tl = inc["timeline"]
    assert tl["unsupported_kinds"] == [] and tl["lifecycle"]["recorded"] is True and inc["completeness"]["source_lifecycle"] == "recorded" and inc["completeness"]["complete"] is True
    ok = [e["kind"] for e in tl["observation"]]
    assert {"observation_received", "person_detected", "vehicle_detected", "risk_changed", "risk_entry"} <= set(ok)
    assert all("at_us" in e for e in tl["observation"]) and all("at_utc" in e for e in tl["processing"])                    # the two time bases stay apart
    assert [e["at_us"] for e in tl["observation"]] == sorted(e["at_us"] for e in tl["observation"])
    pk = [e["kind"] for e in tl["processing"]]
    assert pk.index("source_started") < pk.index("alert_created") < pk.index("source_stopped")                             # numeric instant order across second- and millisecond-resolution stamps
    assert all(e["zone_id"] == inc["identity"]["zone_id"] for e in tl["observation"] if e["kind"] == "risk_changed")
    assert all(e["recorded"] for e in tl["observation"] + tl["processing"] if e["kind"].startswith(("source_", "person_", "vehicle_", "observation_")))


def test_a_scripted_simulation_has_no_source_lifecycle_and_says_so(iso):
    rid, aid = sim_incident(iso)
    inc = iso.client.get(f"/api/incidents/{aid}").json()
    tl = inc["timeline"]
    assert tl["lifecycle"] == {"recorded": False, "run_uid": None, "truncated": False, "reason": "mg.inc.life.not_recorded"}
    assert set(tl["unsupported_kinds"]) >= {"source_started", "source_stopped", "source_failed", "observation_received", "person_detected"}
    assert not [e for e in tl["processing"] if e["kind"].startswith("source_")]                                           # nothing fabricated from the alert timestamps
    assert inc["completeness"]["source_lifecycle"] == "not_recorded" and inc["classification"] == "synthetic"
    assert inc["completeness"]["complete"] is False                                                                      # a record without its source lifecycle is never called complete


def test_ordering_is_by_instant_and_unknown_is_never_green():
    assert incident._ukey("2026-10-29T10:00:00Z") < incident._ukey("2026-10-29T10:00:00.100Z") < incident._ukey("2026-10-29T10:00:01Z")
    ev = lambda k, u, seq=None: {"kind": k, "at_utc": u, **({"seq": seq} if seq is not None else {})}
    mixed = [ev("source_stopped", "2026-10-29T10:00:00.900Z", 3), ev("alert_created", "2026-10-29T10:00:00Z"), ev("source_started", "2026-10-29T10:00:00.400Z", 0), ev("alert_resolved", "2026-10-29T10:00:02Z")]
    assert [e["kind"] for e in sorted(mixed, key=incident._pkey)] == ["source_started", "alert_created", "source_stopped", "alert_resolved"]   # inside one second: causal order; across seconds: exact
    same = [ev("alert_resolved", "2026-10-29T10:00:00Z"), ev("alert_acknowledged", "2026-10-29T10:00:00Z"), ev("delivery_displayed", "2026-10-29T10:00:00Z"), ev("alert_created", "2026-10-29T10:00:00Z")]
    assert [e["kind"] for e in sorted(same, key=incident._pkey)] == ["alert_created", "delivery_displayed", "alert_acknowledged", "alert_resolved"]    # one stamp, one causal chain (never alphabetical)
    life = {"run_uid": "r", "truncated": False, "events": [
        {"seq": 0, "kind": "risk_changed", "group": "observed", "at_utc": "2026-10-29T10:00:00.100Z", "t_us": 0, "zone_id": "Z1", "from": None, "to": "UNKNOWN"},
        {"seq": 1, "kind": "risk_changed", "group": "observed", "at_utc": "2026-10-29T10:00:00.200Z", "t_us": 200000, "zone_id": "OTHER", "from": "GREEN", "to": "RED"}]}
    obs, _ = incident._life_tracks(life, "Z1", {})
    assert [(e["from"], e["to"]) for e in obs] == [(None, "UNKNOWN")]                                                      # another zone's change is not this incident's; UNKNOWN stays UNKNOWN


def test_a_restored_or_lifecycle_less_record_reports_not_recorded(tmp_path):
    e = AlertEngine(tmp_path, wall=Clock(), mono=Clock())
    e.observe_source(src(), [evt("evt_1", "RED")], {})
    (pub,) = e.alerts.values()
    inc = incident.build(e.public(pub), feed=None, run=None, provider={"kind": "model_inference", "model_id": None}, versions={"semantics_version": "v", "engine_version": "e"}, generated_utc="2026-10-29T00:00:00Z")
    assert inc["timeline"]["lifecycle"]["recorded"] is False and inc["timeline"]["unsupported_kinds"] and inc["completeness"]["complete"] is False
    empty = incident.build(e.public(pub), feed=None, run=None, provider={"kind": "model_inference", "model_id": None}, versions={"semantics_version": "v", "engine_version": "e"},
                           generated_utc="2026-10-29T00:00:00Z", lifecycle={"run_uid": "r", "truncated": False, "events": []})
    assert empty["timeline"]["lifecycle"]["recorded"] is False and empty["timeline"]["lifecycle"]["run_uid"] is None            # an empty record is not a recorded lifecycle
    trunc = incident.build(e.public(pub), feed=None, run=None, provider={"kind": "model_inference", "model_id": None}, versions={"semantics_version": "v", "engine_version": "e"},
                           generated_utc="2026-10-29T00:00:00Z", lifecycle={"run_uid": "r", "truncated": True, "events": [{"seq": 0, "kind": "source_started", "group": "lifecycle", "at_utc": "2026-10-29T09:59:59.000Z", "t_us": None}]})
    assert trunc["timeline"]["lifecycle"]["recorded"] is True and trunc["timeline"]["lifecycle"]["truncated"] is True and trunc["timeline"]["unsupported_kinds"] == [] and trunc["completeness"]["complete"] is False


# ------------------------------------------------------------------ export correspondence
def test_the_export_is_the_same_projection_with_an_honest_envelope(fm):
    env = fm({1: always(person=PERSON_IN, vehicle=VEHICLE_IN)}, frames=400, delay={1: 0.01})
    a = create(env, 1, loop=True)
    act(env, a, "start")
    (al,) = wait_alert(env, a)[:1]
    act(env, a, "stop")
    time.sleep(0.3)
    shown = inc_of(env, al)
    r = env.c.get(f"/api/incidents/{al['alert_id']}/export")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    exp = r.json()
    assert exp["schema"] == "mg.incident.export/1" and exp["projection"]["is_original_record"] is False and exp["projection"]["authenticity"] == "not_independently_verified"
    assert set(exp["projection"]["known_open_findings"]) == {"MG-RISK-036", "MG-WCAM-035"}
    got = exp["incident"]
    for k in ("incident_id", "identity", "classification", "risk", "observed", "alert", "delivery", "evidence", "timeline", "completeness", "limitations", "unknown_intervals"):
        assert got[k] == shown[k], k                                                                                         # same facts, same order; only generated_utc may differ
    assert got["evidence"]["images_included"] is False and got["completeness"]["images"] == "excluded_by_design"
    def keys(o):
        return {k for k, v in o.items()} | {kk for v in o.values() for kk in (keys(v) if isinstance(v, dict) else set().union(*[keys(i) for i in v if isinstance(i, dict)]) if isinstance(v, list) and v else set())}
    assert not [k for k in keys(exp) if any(w in k.lower() for w in ("jpeg", "jpg", "png", "base64", "frame_data", "thumbnail", "video"))]       # no pixel-bearing field anywhere in the tree
    assert exp["incident"]["timeline"]["lifecycle"]["recorded"] is True
