"""MG-PRODUCT-VERTICAL-06: incident investigation record, export, source tile facts, and the bounded containment of MG-DELETE-035 / MG-RISK-035 / MG-PROV-035.
Expectations are authored from the stated product rules (a stored fact is shown as stored; an unobserved interval is UNKNOWN; acknowledge/resolve are not delivery; no image in the export), not from output.
Synthetic, test-owned storage only; no owner session is read or written."""
import contextlib
import json
import time

import pytest

from mineguard import incident, overlap
from mineguard.alerts import AlertEngine
from test_rc1_closeout import iso  # noqa: F401
from test_vertical2_alerts import Clock, evt, src
from test_vertical2_feeds import PERSON_IN, VEHICLE_IN, always, act, create, fm, view  # noqa: F401
from test_vertical05_provenance import wait_alert

RULES = {"R_PERSON_AND_VEHICLE_IN_ZONE", "R_PERSON_DWELL_NO_VEHICLE"}


def sim_incident(iso):
    c = iso.client
    rid = c.post("/api/simulations/person-vehicle", json={"base_run_id": iso.run}).json()["run_id"]
    mine = [a for a in c.get("/api/alerts?simulated=true&limit=200").json()["alerts"] if a["source"]["source_id"].startswith(f"sim:{rid}:")]
    assert mine, "the scripted drill must have produced alerts"
    red = [a for a in mine if a["severity"] == "RED"]
    return rid, (red or mine)[0]["alert_id"]


# ------------------------------------------------------------------ MG-DELETE-035 containment
def test_destructive_session_routes_are_disabled_and_remove_nothing(iso):
    c, runs = iso.client, iso.runs
    decoy = runs / "session_aaaaaaaaaaaaaaaa"                                        # a test-owned directory that looks like a session
    decoy.mkdir()
    (decoy / "keep.txt").write_text("unrelated")
    before = sorted(p.name for p in runs.iterdir())
    for body in ({"all": True, "expected_count": 1, "confirm": "ELIMINAR"}, {"ids": ["session_aaaaaaaaaaaaaaaa"], "expected_count": 1, "confirm": "ELIMINAR"}):
        r = c.post("/api/sessions/delete", json=body)
        assert r.status_code == 503 and r.json()["detail"]["msg_id"] == "mg.refusal.delete_temporarily_unavailable"
    r = c.post("/api/sessions/session_aaaaaaaaaaaaaaaa/media/delete", json={"confirm": "ELIMINAR", "expected_files": 0})
    assert r.status_code == 503 and r.json()["detail"]["msg_id"] == "mg.refusal.delete_temporarily_unavailable"
    r = c.post("/api/sessions/delete", content=b"{not json", headers={"Content-Type": "application/json"})    # MG-DELETE-036: answered before the body is parsed
    assert r.status_code == 503 and r.json()["detail"]["msg_id"] == "mg.refusal.delete_temporarily_unavailable"
    r = c.post("/api/sessions/delete", content=b"x" * 5_000_000, headers={"Content-Type": "application/json"})
    assert r.status_code in (413, 503)                                                                       # bounded: the application's global size limit or the 503, never a parsed/buffered body
    assert sorted(p.name for p in runs.iterdir()) == before and (decoy / "keep.txt").read_text() == "unrelated" and (runs / iso.run / "export.json").is_file()
    assert (iso.client.get("/api/runs").status_code == 200)                           # the rest of the application is unaffected


# ------------------------------------------------------------------ incident record from a scripted simulation (stored run behind it)
def test_simulation_incident_is_grounded_labelled_synthetic_and_consistent(iso):
    c = iso.client
    rid, aid = sim_incident(iso)
    inc = c.get(f"/api/incidents/{aid}").json()
    assert inc["schema"] == "mg.incident/1" and inc["incident_id"] == aid and inc["classification"] == "synthetic"
    assert inc["identity"]["run_id"] == rid and inc["identity"]["physical_camera"] is False and inc["provenance"]["simulation"] is True
    assert inc["provenance"]["provider"] == {"kind": "scripted_simulation", "model_id": None}                           # no model produced it: never presented as inference
    assert inc["risk"]["rule_id"] in RULES and inc["risk"]["ai_observations_decide_severity"] is False and inc["risk"]["decided_by"] == "deterministic_policy"
    obs = {e["kind"]: e for e in inc["timeline"]["observation"]}
    assert obs["risk_entry"]["at_us"] == inc["observed"]["start_us"] and obs["risk_entry"]["synthetic"] is True
    assert inc["evidence"]["local_consistency"] == "pass" and inc["evidence"]["stored_run"] == rid and inc["evidence"]["images_included"] is False
    assert inc["evidence"]["authenticity"] == "not_independently_verified" and inc["evidence"]["digest_sha256"]
    assert inc["unknown_intervals"]["available"] is True
    kinds = [e["kind"] for e in inc["timeline"]["processing"]]
    assert kinds[0] == "alert_created" and "source_started" in inc["timeline"]["unsupported_kinds"]                      # not recorded -> declared unsupported, not invented
    # the risk the incident reports is the one the stored run's own evaluation reports
    zdoc = c.get(f"/api/runs/{rid}/risk").json()["evaluation"]["zones"][inc["identity"]["zone_id"]]
    assert any(e["event_id"] == inc["identity"]["event_id"] and e["start_us"] == inc["observed"]["start_us"] for e in zdoc["events"])


def test_unknown_intervals_are_the_evaluation_unknowns_never_green(iso):
    rid, aid = sim_incident(iso)
    inc = iso.client.get(f"/api/incidents/{aid}").json()
    zdoc = iso.client.get(f"/api/runs/{rid}/risk").json()["evaluation"]["zones"][inc["identity"]["zone_id"]]
    expect = [(i["start_us"], i["end_us"]) for i in zdoc["timeline"] if i["state"] == "UNKNOWN"]
    assert [(u["start_us"], u["end_us"]) for u in inc["unknown_intervals"]["intervals"]] == expect
    assert {e["kind"] for e in inc["timeline"]["observation"]} <= {"risk_entry", "unknown_interval", "episode_closed"}


def test_ack_and_resolve_change_the_lifecycle_but_never_the_delivery(iso):
    c = iso.client
    _, aid = sim_incident(iso)
    before = c.get(f"/api/incidents/{aid}").json()
    assert before["alert"]["status"] in ("new", "active")
    assert c.post(f"/api/alerts/{aid}/ack").status_code == 200
    mid = c.get(f"/api/incidents/{aid}").json()
    assert mid["alert"]["status"] == "acknowledged" and mid["alert"]["acknowledged_utc"]
    assert c.post(f"/api/alerts/{aid}/resolve").status_code == 200
    after = c.get(f"/api/incidents/{aid}").json()
    assert after["alert"]["status"] == "resolved" and after["alert"]["resolved_by"] == "operator"
    kinds = [e["kind"] for e in after["timeline"]["processing"]]
    assert "alert_acknowledged" in kinds and "alert_resolved" in kinds
    assert after["delivery"]["state"] != "delivered" and after["delivery"]["channels"]["browser"]["status"] == "pending"      # nothing was reported by a browser: delivery is not assumed
    assert "not delivery" in after["delivery"]["note"]


def test_json_export_is_the_same_incident_and_holds_no_image(iso):
    c = iso.client
    _, aid = sim_incident(iso)
    r = c.get(f"/api/incidents/{aid}/export")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"] and aid in r.headers["content-disposition"]
    body = json.loads(r.text)
    shown = c.get(f"/api/incidents/{aid}").json()
    got, want = dict(body["incident"]), dict(shown)
    got.pop("generated_utc"), want.pop("generated_utc")
    assert body["schema"] == "mg.incident.export/1" and got == want                                               # the export IS the incident the screen shows
    low = r.text.lower()
    assert "jpeg" not in low and "frames/" not in low and "base64" not in low and body["incident"]["evidence"]["images_included"] is False


def test_unknown_alert_is_404(iso):
    assert iso.client.get("/api/incidents/alt_0000000000000000").status_code == 404
    assert iso.client.get("/api/incidents/not-an-alert").status_code == 404


# ------------------------------------------------------------------ MG-RISK-035 containment
@contextlib.contextmanager
def restoring(*paths):
    saved = {p: p.read_bytes() for p in paths}
    try:
        yield
    finally:
        for p, b in saved.items():
            p.write_bytes(b)


def test_a_run_whose_components_disagree_is_not_presented_as_one_coherent_result(iso):
    c = iso.client
    rid, aid = sim_incident(iso)
    d = iso.runs / rid
    with restoring(d / "observations.json"):
        raw = (d / "observations.json").read_bytes()
        assert b'"person"' in raw
        (d / "observations.json").write_bytes(raw.replace(b'"person"', b'"xerson"', 1))                           # observations alone are altered; export.json still holds the old ones
        assert c.get(f"/api/runs/{rid}/risk").status_code == 409
        assert c.get(f"/api/runs/{rid}/risk/export").status_code == 409
        inc = c.get(f"/api/incidents/{aid}").json()
        assert inc["evidence"]["local_consistency"] == "fail" and inc["evidence"]["conflicts"] == ["mg.inc.conflict.run_unavailable_or_inconsistent"]
        assert inc["unknown_intervals"]["available"] is False and inc["evidence"]["digest_sha256"] is None
        assert all(e["source_id"] != rid for e in c.get("/api/events").json()["events"])                          # nor does the main events list promote it
    assert c.get(f"/api/runs/{rid}/risk").status_code == 200                                                      # restored bytes: coherent again


def test_a_missing_run_is_reported_as_a_conflict_not_invented(iso):
    c = iso.client
    rid, aid = sim_incident(iso)
    d = iso.runs / rid
    with restoring(d / "export.json"):
        (d / "export.json").unlink()
        inc = c.get(f"/api/incidents/{aid}").json()
        assert inc["evidence"]["local_consistency"] == "fail" and inc["evidence"]["conflicts"]
    assert c.get(f"/api/incidents/{aid}").json()["evidence"]["local_consistency"] == "pass"


# ------------------------------------------------------------------ restored / unit-level classification
def test_a_restored_alert_is_history_not_a_live_incident(tmp_path):
    e = AlertEngine(tmp_path, wall=Clock(), mono=Clock())
    e.observe_source(src(), [evt("evt_1", "RED")], {})
    e.flush()
    r = AlertEngine(tmp_path, wall=Clock(), mono=Clock())
    (a,) = r.alerts.values()
    pub = r.public(a)
    inc = incident.build(pub, feed=None, run=None, provider={"kind": "model_inference", "model_id": None}, versions={"semantics_version": "v", "engine_version": "e"}, generated_utc="2026-10-28T00:00:00Z")
    assert pub["loaded_from_store"] and inc["classification"] == "restored" and inc["evidence"]["restored"] is True and inc["evidence"]["local_consistency"] == "not_checked"
    assert inc["delivery"]["channels"]["browser"]["status"] != "delivered"
    with pytest.raises(ValueError):
        r.report_delivery(a["alert_id"], "browser", "delivered")                                                  # a restored record accepts no delivery report


# ------------------------------------------------------------------ MG-PROV-035 and the operator tile facts
def test_duplicate_status_keeps_four_facts_apart():
    ds = overlap.duplicate_status
    assert ds("playing", True, False, True, False) == "active_overlap"
    assert ds("playing", False, False, True, False) == "historical_repeated" and ds("playing", False, True, False, False) == "historical_repeated"
    assert ds("stopped", True, True, True, False) == "inactive" and ds("idle", False, None, False, False) == "inactive"
    assert ds("playing", False, False, False, True) == "unknown" and ds("playing", False, None, False, False) == "unknown"
    assert ds("playing", False, False, False, False) == "none"


def test_an_idle_duplicate_does_not_make_a_single_active_playback_look_like_concurrent_monitoring(fm):
    env = fm({1: always(person=PERSON_IN, vehicle=VEHICLE_IN)}, frames=400, delay={1: 0.01})
    a = create(env, 1, loop=True)
    b = create(env, 1, label="Fuente 2", loop=True)                                                               # exists, never started
    act(env, a, "start")
    wait_alert(env, a)
    va = view(env, a)
    assert va["source"]["provenance"] == "real_inference" and va["launch_repeated"] is False and va["shared_now"] is False   # MG-PROV-035
    rows = {r["feed_id"]: r for r in env.c.get("/api/monitor/overview").json()["feeds"]}
    assert rows[a]["duplicate"] == "none" and rows[b]["duplicate"] == "inactive" and rows[a]["provenance"] == "real_inference"
    act(env, b, "start")
    time.sleep(0.5)
    rows = {r["feed_id"]: r for r in env.c.get("/api/monitor/overview").json()["feeds"]}
    assert rows[a]["duplicate"] == rows[b]["duplicate"] == "active_overlap" and rows[a]["provenance"] == "repeated_source_simulation"
    act(env, b, "stop")
    time.sleep(0.5)
    rows = {r["feed_id"]: r for r in env.c.get("/api/monitor/overview").json()["feeds"]}
    assert rows[a]["duplicate"] == "historical_repeated" and rows[b]["duplicate"] == "inactive"                    # overlapped once, not now; the stopped one is inactive
    act(env, a, "stop")


def test_tile_facts_come_from_the_feed_and_never_invent_counts(fm):
    env = fm({1: always(person=PERSON_IN, vehicle=VEHICLE_IN)}, frames=400, delay={1: 0.01})
    a = create(env, 1, loop=True)
    idle = {r["feed_id"]: r for r in env.c.get("/api/monitor/overview").json()["feeds"]}[a]
    assert idle["counts_latest"] is None and idle["observation"]["fresh"] is False and idle["last_observation_source_us"] is None and idle["incidents_open"] == 0
    assert idle["source_id"] == view(env, a)["source"]["source_id"] and idle["source_kind"] == "mp4_file" and idle["zone_ids"]
    act(env, a, "start")
    (al,) = wait_alert(env, a)[:1]
    row = {r["feed_id"]: r for r in env.c.get("/api/monitor/overview").json()["feeds"]}[a]
    assert row["observation"]["fresh"] is True and row["counts_latest"] == {"person": 1, "vehicle": 1} and row["incidents_open"] >= 1
    assert env.c.get("/api/monitor/overview").json()["detector"].keys() == {"model_id", "architecture"}
    inc = env.c.get(f"/api/incidents/{al['alert_id']}").json()
    assert inc["classification"] == "live_feed_metadata" and inc["identity"]["feed_id"] == a and inc["provenance"]["feed"]["state"] == "playing"
    assert inc["provenance"]["provider"]["kind"] == "model_inference" and inc["evidence"]["stored_run"] is None and inc["unknown_intervals"]["available"] is False
    act(env, a, "stop")
    time.sleep(0.5)
    stopped = {r["feed_id"]: r for r in env.c.get("/api/monitor/overview").json()["feeds"]}[a]
    assert stopped["counts_latest"] is None and stopped["duplicate"] == "inactive"                                   # a stopped feed shows no carried-over counts


# ------------------------------------------------------------------ review-35 corrections (UNREVIEWED): MG-INCIDENT-036 / MG-INCIDENT-037 / MG-EXPORT-036
def _pub(tmp_path, **over):
    e = AlertEngine(tmp_path, wall=Clock(), mono=Clock())
    e.observe_source(src(**over), [evt("evt_1", "RED")], {})
    (a,) = e.alerts.values()
    return e, a


def _build(pub, run=None, provider=None):
    return incident.build(pub, feed=None, run=run, provider=provider or {"kind": "model_inference", "model_id": None}, versions={"semantics_version": "v", "engine_version": "e"}, generated_utc="2026-10-28T00:00:00Z")


def test_provider_and_classification_follow_the_source_kind(fm):
    env = fm({1: always(person=PERSON_IN, vehicle=VEHICLE_IN)}, frames=400, delay={1: 0.01})
    a, b = create(env, 1, loop=True), create(env, 1, label="Fuente 2", loop=True)
    act(env, a, "start")
    (alert,) = wait_alert(env, a)[:1]
    one = env.c.get(f"/api/incidents/{alert['alert_id']}").json()
    assert one["classification"] == "live_feed_metadata" and one["provenance"]["provider"]["kind"] == "model_inference"
    act(env, b, "start")
    time.sleep(0.5)
    two = env.c.get(f"/api/incidents/{alert['alert_id']}").json()
    assert two["classification"] == "repeated_source_simulation" and two["provenance"]["provider"]["kind"] == "repeated_source_simulation"   # real inference over a replayed file, never «scripted»
    assert two["evidence"]["synthetic"] is False and two["provenance"]["simulation"] is True
    act(env, a, "stop"); act(env, b, "stop")


def test_a_webcam_alert_is_not_called_a_scripted_simulation(fm):
    env = fm({1: always()}, frames=10)
    env.alerts.observe_session("session_aaaaaaaaaaaaaaaa", "cam", "test_source", [evt("evt_w", "RED")], {})
    (a,) = [x for x in env.alerts.alerts.values() if x["source"]["kind"] == "webcam_session"]
    inc = env.c.get(f"/api/incidents/{a['alert_id']}").json()
    assert inc["provenance"]["provider"]["kind"] == "webcam_model_inference" and inc["classification"] == "live_feed_metadata"      # MG-INCIDENT-036


def test_a_restored_incident_does_not_claim_the_running_engine_version(tmp_path):
    e, _ = _pub(tmp_path)
    e.flush()
    r = AlertEngine(tmp_path, wall=Clock(), mono=Clock())
    (a,) = r.alerts.values()
    inc = _build(r.public(a))
    assert inc["risk"]["engine_version"] is None and inc["risk"]["semantics_version"] is None and inc["risk"]["versions_basis"] == "unavailable_for_restored_record"
    live = _build(e.public(next(iter(e.alerts.values()))))
    assert live["risk"]["versions_basis"] == "running_build_at_projection" and live["risk"]["engine_version"] == "e"


def test_an_alert_whose_nested_event_differs_from_its_identity_is_refused_on_load(tmp_path):
    e, a = _pub(tmp_path)
    e.flush()
    doc = json.loads((tmp_path / "alerts.json").read_text())
    rows = doc["alerts"] if isinstance(doc, dict) and "alerts" in doc else doc
    rows[0]["event"]["event_id"] = "evt_other"
    (tmp_path / "alerts.json").write_text(json.dumps(doc))
    assert AlertEngine(tmp_path, wall=Clock(), mono=Clock()).alerts == {}                                     # MG-INCIDENT-037


def test_incident_is_inconsistent_when_its_event_is_not_in_the_stored_evaluation(iso):
    c = iso.client
    rid, aid = sim_incident(iso)
    ok = c.get(f"/api/incidents/{aid}").json()
    assert ok["evidence"]["local_consistency"] == "pass" and ok["evidence"]["conflicts"] == []
    alert = iso.client.app.state.st.alerts.alerts[aid]
    real = alert["event"]["rule_id"]
    alert["event"]["rule_id"] = "R_OTHER"
    try:
        bad = c.get(f"/api/incidents/{aid}").json()
        assert bad["evidence"]["local_consistency"] == "fail" and "mg.inc.conflict.event_not_in_run" in bad["evidence"]["conflicts"]
    finally:
        alert["event"]["rule_id"] = real
