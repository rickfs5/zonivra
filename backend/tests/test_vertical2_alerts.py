"""MG-VERTICAL-02 checkpoint C: the alert chain (risk event -> policy -> alert record -> dispatcher -> delivery status). Synthetic only; no network, no real notification."""
import json
import socket
import time
from types import SimpleNamespace

import pytest

from mineguard import alerts as am
from mineguard.alerts import AlertEngine
from test_vertical2_feeds import ZONE, PERSON_IN, VEHICLE_IN, always, act, create, fm, view, wait_state  # noqa: F401  (feed fixtures)


class Clock:
    def __init__(self):
        self.t = 1_800_000_000.0

    def __call__(self):
        return self.t

    def advance(self, s):
        self.t += s


def engine(tmp_path=None, clock=None):
    clock = clock or Clock()
    return AlertEngine(tmp_path, wall=clock, mono=clock), clock


def src(group="feed_a", sid="feed_a:r1", provenance="real_inference", kind="feed"):
    return {"source_id": sid, "group": group, "feed_id": group if kind == "feed" else None, "kind": kind, "label": "Rampa", "provenance": provenance,
            "repeated_source": provenance == "repeated_source_simulation", "physical_camera": False, "incident_class": "scenario_file", "video_id": "vid_x"}


def evt(eid, sev="RED", open_=True, zone="Z1", end=None):
    return {"event_id": eid, "zone_id": zone, "severity": sev, "rule_id": "R1" if sev == "RED" else "R2", "start_us": 1_000_000, "end_us": end, "open": open_, "tracks": [], "closed_reason": None if open_ else "condition_ended", "right_censored": False}


def state(s="GREEN", t=1_000_000, causes=()):
    return {"state": s, "pending": [], "causes": list(causes), "t_us": t}


# ------------------------------------------------------------------ the chain: event -> alert -> delivery
def test_a_red_event_becomes_a_high_priority_alert_with_every_required_field():
    e, _ = engine()
    e.observe_source(src(), [evt("evt_1", "RED")], {"Z1": state("RED")})
    (a,) = e.alerts.values()
    assert a["severity"] == "RED" and a["priority"] == "high" and a["status"] == "new" and a["kind"] == "risk_event"
    assert a["alert_id"].startswith("alt_") and a["event_id"] == "evt_1" and a["feed_id"] == "feed_a" and a["zone_id"] == "Z1"
    assert a["created_utc"].endswith("Z") and a["source"]["provenance"] == "real_inference" and a["source"]["physical_camera"] is False
    assert a["delivery"]["in_app"] == {"status": "stored", "at": None} and a["delivery"]["browser"]["status"] == "pending" and a["delivery"]["sound"]["status"] == "pending"
    assert a["delivery_state"] == "pending" and a["acknowledged_utc"] is None and a["simulation"] is False


def test_a_yellow_event_becomes_a_warning_alert_without_sound_by_default():
    e, _ = engine()
    e.observe_source(src(), [evt("evt_y", "YELLOW")], {"Z1": state("YELLOW")})
    (a,) = e.alerts.values()
    assert a["severity"] == "YELLOW" and a["priority"] == "warning" and a["delivery"]["sound"]["status"] == "disabled" and a["delivery"]["browser"]["status"] == "pending"


def test_an_ai_observation_alone_never_creates_an_alert():
    e, _ = engine()
    e.observe_source(src(), [], {"Z1": state("GREEN")})
    assert e.alerts == {} and e.counters["alerts_created"] == 0


def test_unknown_is_a_visible_low_priority_diagnostic_never_green_and_never_an_emergency():
    e, _ = engine()
    s = src()
    e.observe_source(s, [], {"Z1": state("UNKNOWN", 1_000_000, ["frame_not_analyzed"])})
    assert e.alerts == {}                                                       # a short uncertainty is not worth an alert
    e.observe_source(s, [], {"Z1": state("UNKNOWN", 12_000_000, ["frame_not_analyzed"])})
    (a,) = e.alerts.values()
    assert a["kind"] == "diagnostic" and a["severity"] == "UNKNOWN" and a["priority"] == "diagnostic" and a["event_id"] is None and a["causes"] == ["frame_not_analyzed"]
    assert a["delivery"]["browser"]["status"] == "disabled" and a["delivery"]["sound"]["status"] == "disabled"     # no popup, no beep for uncertainty
    assert a["severity"] not in ("RED", "GREEN")
    e.observe_source(s, [], {"Z1": state("GREEN", 13_000_000)})
    assert a["status"] == "resolved" and a["resolved_by"] == "state_left_unknown"


def test_unknown_uncertainty_is_closed_when_the_source_ends():
    e, _ = engine()
    s = src()
    e.observe_source(s, [], {"Z1": state("UNKNOWN", 0 + 1)})
    e.observe_source(s, [], {"Z1": state("UNKNOWN", 20_000_000)})
    (a,) = e.alerts.values()
    e.observe_source(s, [], {"Z1": state("UNKNOWN", 21_000_000)}, final=True)
    assert a["status"] == "resolved" and a["resolved_by"] == "source_closed"


# ------------------------------------------------------------------ storm control
def test_observing_the_same_episode_many_times_is_idempotent():
    e, _ = engine()
    for _ in range(200):
        e.observe_source(src(), [evt("evt_1", "RED")], {"Z1": state("RED")})
    assert len(e.alerts) == 1 and e.counters["alerts_created"] == 1 and e.counters["ignored_duplicates"] == 199
    assert e.rev <= 2


def test_alerts_of_the_same_group_inside_the_cooldown_are_merged_and_counted():
    e, c = engine()
    s = src()
    e.observe_source(s, [evt("evt_1", "YELLOW", False, end=2_000_000)], {})
    c.advance(5)
    for n in range(2, 7):                                                         # unstable tracking: new episodes keep opening for the same zone
        e.observe_source(s, [evt(f"evt_{n}", "YELLOW", True)], {})
        c.advance(1)
    assert len(e.alerts) == 1
    (a,) = e.alerts.values()
    assert a["suppressed_count"] == 5 and len(a["occurrences"]) == 6 and e.counters["merged"] == 5
    c.advance(120)                                                                 # beyond the 60 s YELLOW cooldown: a new alert
    e.observe_source(s, [evt("evt_late", "YELLOW", True)], {})
    assert len(e.alerts) == 2


def test_red_cooldown_is_short_and_separate_from_yellow():
    e, c = engine()
    s = src()
    e.observe_source(s, [evt("r1", "RED")], {})
    e.observe_source(s, [evt("y1", "YELLOW")], {})                                 # a different severity group is never merged into the RED alert
    c.advance(11)
    e.observe_source(s, [evt("r2", "RED")], {})                                    # RED cooldown is 10 s
    assert sorted(a["severity"] for a in e.alerts.values()) == ["RED", "RED", "YELLOW"]


def test_browser_reconnections_of_the_same_device_do_not_multiply_alerts(tmp_path):
    e, c = engine()
    for n in range(5):                                                             # five reconnections = five session ids, one device
        s = {**src(group="webcam:Insta360", sid=f"webcam:sess_{n}", kind="webcam_session")}
        e.observe_source(s, [evt(f"evt_{n}", "RED")], {})
        c.advance(2)
    assert len(e.alerts) == 1 and next(iter(e.alerts.values()))["suppressed_count"] == 4


def test_different_feeds_and_zones_never_merge():
    e, _ = engine()
    e.observe_source(src("feed_a", "feed_a:r"), [evt("e1", "RED")], {})
    e.observe_source(src("feed_b", "feed_b:r"), [evt("e2", "RED")], {})
    e.observe_source(src("feed_a", "feed_a:r"), [evt("e3", "RED", zone="Z2")], {})
    assert len(e.alerts) == 3 and {a["feed_id"] for a in e.alerts.values()} == {"feed_a", "feed_b"}


# ------------------------------------------------------------------ lifecycle
def test_the_alert_lifecycle_new_active_acknowledged_resolved():
    e, _ = engine()
    e.observe_source(src(), [evt("evt_1", "RED")], {})
    (aid,) = e.alerts
    assert e.alerts[aid]["status"] == "new"
    assert e.mark_seen([aid, "alt_unknown0000000000"]) == 1 and e.alerts[aid]["status"] == "active"
    assert e.mark_seen([aid]) == 0                                                  # already seen: no change
    e.acknowledge(aid)
    assert e.alerts[aid]["status"] == "acknowledged" and e.alerts[aid]["acknowledged_utc"]
    e.observe_source(src(), [evt("evt_1", "RED", False, end=9_000_000)], {})      # the episode ended
    assert e.alerts[aid]["status"] == "resolved" and e.alerts[aid]["resolved_by"] == "event_closed"
    with pytest.raises(ValueError):
        e.acknowledge(aid)                                                          # a resolved alert cannot be acknowledged
    assert e.mark_seen([aid]) == 0 and e.alerts[aid]["status"] == "resolved"


def test_an_operator_can_resolve_and_a_closed_source_resolves_its_open_alerts():
    e, _ = engine()
    s = src()
    e.observe_source(s, [evt("a", "RED"), evt("b", "YELLOW", zone="Z2")], {})
    a, b = list(e.alerts)
    e.resolve(a)
    assert e.alerts[a]["resolved_by"] == "operator"
    e.observe_source(s, [], {}, final=True)
    assert e.alerts[b]["status"] == "resolved" and e.alerts[b]["resolved_by"] == "source_closed"


# ------------------------------------------------------------------ delivery
def test_browser_permission_denied_is_recorded_honestly_and_in_app_still_works():
    e, _ = engine()
    e.observe_source(src(), [evt("evt_1", "RED")], {})
    (aid,) = e.alerts
    e.mark_seen([aid])                                                            # the operator view displayed it
    e.report_delivery(aid, "browser", "denied", "Notification.permission = denied")
    e.report_delivery(aid, "sound", "delivered")
    a = e.alerts[aid]
    assert a["delivery"]["browser"]["status"] == "denied" and a["delivery"]["in_app"]["status"] == "displayed" and a["delivery_state"] == "delivered"      # browser denied, sound delivered: the real delivery is reported (MG-ALERT-030)


def test_a_failed_delivery_marks_the_alert_delivery_failed():
    e, _ = engine()
    e.observe_source(src(), [evt("evt_1", "RED")], {})
    (aid,) = e.alerts
    e.report_delivery(aid, "browser", "failed", "constructor threw")
    assert e.alerts[aid]["delivery_state"] == "failed"
    e.report_delivery(aid, "sound", "delivered")
    assert e.alerts[aid]["delivery_state"] == "failed"                                # one failure keeps the state visible


def test_delivery_reports_are_validated_and_conflicts_refused():
    e, _ = engine()
    e.observe_source(src(), [evt("y", "YELLOW")], {})
    (aid,) = e.alerts
    with pytest.raises(ValueError):
        e.report_delivery(aid, "sound", "delivered")                                  # sound is disabled for YELLOW: a client cannot claim it
    with pytest.raises(ValueError):
        e.report_delivery(aid, "sms", "delivered")
    with pytest.raises(ValueError):
        e.report_delivery(aid, "browser", "sent")
    e.report_delivery(aid, "browser", "delivered")
    e.report_delivery(aid, "browser", "delivered")                                    # idempotent repeat
    with pytest.raises(ValueError):
        e.report_delivery(aid, "browser", "failed")                                   # a reported outcome cannot be rewritten


def test_credentials_in_delivery_details_are_redacted():
    assert am.redact({"token": "abc", "nested": {"api_key": "k", "ok": "fine"}, "u": "https://user:pw@example.org/x"}) == {"token": "<redacted>", "nested": {"api_key": "<redacted>", "ok": "fine"}, "u": "https://<redacted>@example.org/x"}
    e, _ = engine()
    e.observe_source(src(), [evt("evt_1", "RED")], {})
    (aid,) = e.alerts
    e.report_delivery(aid, "browser", "failed", "POST https://u:secretpw@hook.example/x failed")
    assert "secretpw" not in json.dumps(e.alerts[aid])


# ------------------------------------------------------------------ settings, persistence, simulation, no external transmission
def test_settings_are_strict_and_the_webhook_cannot_be_enabled(tmp_path):
    e, _ = engine(tmp_path)
    s = json.loads(json.dumps(e.settings))
    s["yellow"]["cooldown_s"] = 5
    assert e.set_settings(s)["yellow"]["cooldown_s"] == 5
    for bad in ({**s, "extra": 1}, {**s, "red": {"browser": "yes", "sound": True, "cooldown_s": 10}}, {**s, "red": {**s["red"], "cooldown_s": -1}}, {**s, "red": {**s["red"], "cooldown_s": True}}):
        with pytest.raises(ValueError):
            e.set_settings(bad)
    with pytest.raises(PermissionError):
        e.set_settings({**s, "webhook": {"enabled": True}})
    assert e.settings["webhook"] == {"enabled": False}
    e2, _ = engine(tmp_path)
    assert e2.settings["yellow"]["cooldown_s"] == 5                                    # settings survive a restart


def test_alerts_survive_a_restart_and_a_corrupted_store_starts_empty(tmp_path):
    e, _ = engine(tmp_path)
    e.ingest_simulation("run_bbbbbbbbbbbbbbbb", [evt("evt_1", "RED", False, end=4)])         # a drill belongs to the operator and survives a restart (a live source's alerts do not)
    (aid,) = e.alerts
    e.acknowledge(aid)
    e.flush()
    e2, _ = engine(tmp_path)
    assert e2.alerts[aid]["status"] == "acknowledged" and e2.rev == e.rev
    (tmp_path / "alerts.json").write_text("{broken")
    e3, _ = engine(tmp_path)
    assert e3.alerts == {}


def test_the_store_is_bounded():
    e, c = engine()
    for n in range(am.MAX_ALERTS + 40):
        e.observe_source(src(f"f{n}", f"f{n}:r"), [evt(f"e{n}", "RED", False, end=5)], {})
        c.advance(1)
    assert len(e.alerts) <= am.MAX_ALERTS


def test_simulation_alerts_are_labelled_and_never_counted_as_genuine():
    e, _ = engine()
    e.ingest_simulation("run_abcdabcdabcdabcd", [evt("sim_1", "YELLOW", False, end=3), evt("sim_2", "RED", False, end=9, zone="Z2")])
    e.observe_source(src(), [evt("real_1", "RED")], {})
    sims = e.list(simulated=True)["alerts"]
    real = e.list(simulated=False)["alerts"]
    assert len(sims) == 2 and all(a["simulation"] and a["source"]["provenance"] == "synthetic_scripted" and a["source"]["incident_class"] == "simulation" for a in sims)
    assert len(real) == 1 and real[0]["source"]["provenance"] == "real_inference"
    assert e.summary()["by_provenance"] == {"real_inference": 1, "synthetic_scripted": 2}


def test_the_chain_works_with_all_network_access_blocked(fm, monkeypatch):
    def deny(*a, **k):
        raise AssertionError("network access attempted")
    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket, "getaddrinfo", deny)
    env = fm({1: always(person=PERSON_IN, vehicle=VEHICLE_IN)}, frames=120)
    a = create(env, 1)
    act(env, a, "start")
    wait_state(env, [a], ("ended",))
    assert any(x["severity"] == "RED" for x in env.alerts.alerts.values()) and am.WEBHOOK_DELIVERY_ENABLED is False


def test_webhook_destinations_follow_the_ssrf_rules_even_though_delivery_is_disabled():
    ok = lambda u, al=("hooks.example.org",): am.validate_webhook_destination(u, list(al))
    assert ok("https://hooks.example.org/x") == (True, "ok")
    assert ok("https://other.example.org/x")[1] == "host_not_in_allowlist"
    assert ok("https://user:pw@hooks.example.org/x")[1] == "credentials_in_url"
    assert ok("http://hooks.example.org/x")[1] == "scheme_not_allowed"
    assert ok("ftp://hooks.example.org/x")[1] == "scheme_not_allowed"
    for ip in ("127.0.0.1", "10.0.0.5", "169.254.169.254", "192.168.1.1", "0.0.0.0", "224.0.0.1"):
        assert ok(f"https://{ip}/x", (ip,))[1] == "private_or_reserved_address", ip
    assert ok("http://localhost/x", ("localhost",)) == (True, "ok_loopback_http")
    assert ok("https://*.example.org/x", ("*.example.org",))[0] is False
    assert ok("not a url")[0] is False
    c = am.webhook_contract()
    assert c["delivery_enabled"] is False and "DISABLED" in c["status"]


# ------------------------------------------------------------------ integration through feeds and the HTTP API
def test_events_of_each_feed_become_alerts_attributed_to_that_feed_only(fm):
    env = fm({1: always(person=PERSON_IN), 2: always(person=PERSON_IN, vehicle=VEHICLE_IN), 3: always()}, frames=150)
    fids = [create(env, c, f"Feed {c}") for c in (1, 2, 3)]
    for f in fids:
        act(env, f, "start")
    wait_state(env, fids, ("ended",))
    r = env.c.get("/api/alerts").json()
    assert r["api_version"] == "mg.alert-api/1"
    by_feed = {}
    for a in r["alerts"]:
        by_feed.setdefault(a["feed_id"], set()).add(a["severity"])
        assert a["source"]["kind"] == "feed" and a["source"]["physical_camera"] is False and a["simulation"] is False and a["status"] == "resolved"
    assert by_feed[fids[0]] == {"YELLOW"} and "RED" in by_feed[fids[1]] and fids[2] not in by_feed


def test_the_http_alert_api_covers_filters_acknowledgement_delivery_and_settings(fm):
    env = fm({1: always(person=PERSON_IN, vehicle=VEHICLE_IN)}, frames=150)
    a = create(env, 1)
    act(env, a, "start")
    wait_state(env, [a], ("ended",))
    al = env.c.get("/api/alerts?severity=RED").json()["alerts"]
    assert al and all(x["severity"] == "RED" for x in al)
    aid = al[0]["alert_id"]
    assert env.c.get("/api/alerts?severity=PURPLE").status_code == 422 and env.c.get("/api/alerts?status=bogus").status_code == 422
    assert env.c.post("/api/alerts/seen", json={"ids": [aid]}).json()["marked"] in (0, 1)
    assert env.c.post(f"/api/alerts/{aid}/ack").status_code == 409                                      # already resolved when its episode closed
    assert env.c.post("/api/alerts/alt_0000000000000000/ack").status_code == 404 and env.c.post("/api/alerts/bad/ack").status_code == 404
    d = env.c.post(f"/api/alerts/{aid}/delivery", json={"channel": "browser", "status": "denied", "detail": "permission denied"})
    assert d.status_code == 200 and d.json()["delivery_state"] in ("in_app_only", "pending")
    assert env.c.post(f"/api/alerts/{aid}/delivery", json={"channel": "email", "status": "delivered"}).status_code == 422
    s = env.c.get("/api/alerts/settings").json()
    assert s["webhook_contract"]["delivery_enabled"] is False
    bad = json.loads(json.dumps(s["settings"])); bad["webhook"] = {"enabled": True}
    assert env.c.put("/api/alerts/settings", json=bad).status_code == 403
    ok = json.loads(json.dumps(s["settings"])); ok["yellow"]["sound"] = True
    assert env.c.put("/api/alerts/settings", json=ok).json()["settings"]["yellow"]["sound"] is True
    since = env.c.get("/api/alerts").json()["rev"]
    assert env.c.get(f"/api/alerts?after={since}").json()["alerts"] == []                                # the incremental feed used by future adapters


def test_a_live_alert_is_acknowledged_through_the_api_before_its_episode_ends(fm):
    env = fm({1: always(person=PERSON_IN, vehicle=VEHICLE_IN)}, frames=400, delay={1: 0.01})
    a = create(env, 1, loop=True)
    act(env, a, "start")
    t = time.time()
    while time.time() - t < 30 and not env.alerts.alerts:
        time.sleep(0.05)
    (aid, *_) = list(env.alerts.alerts)
    assert env.c.post("/api/alerts/seen", json={"ids": [aid]}).json()["marked"] == 1
    r = env.c.post(f"/api/alerts/{aid}/ack")
    assert r.status_code == 200 and r.json()["status"] == "acknowledged" and "created_epoch" not in r.json()
    act(env, a, "stop")
    assert env.c.get("/api/alerts").json()["alerts"][0]["status"] == "resolved"                          # stopping the source closes what it left open


def test_two_different_scripted_scenarios_never_merge_into_one_alert():
    e, _ = engine()
    e.ingest_simulation("run_aaaaaaaaaaaaaaaa", [evt("s1", "RED", False, end=9)])
    e.ingest_simulation("run_bbbbbbbbbbbbbbbb", [evt("s2", "RED", False, end=9)])
    assert len(e.alerts) == 2 and all(a["status"] == "new" for a in e.alerts.values())     # drills stay open until the operator acts


def test_pressing_the_same_drill_twice_creates_two_labelled_drills():
    e, c = engine()
    e.ingest_simulation("run_aaaaaaaaaaaaaaaa", [evt("s1", "RED", False, end=9)])
    c.advance(1)
    e.ingest_simulation("run_aaaaaaaaaaaaaaaa", [evt("s1", "RED", False, end=9)])
    assert len(e.alerts) == 2 and all(a["simulation"] for a in e.alerts.values())


# ------------------------------------------------------------------ cycle-21 review A: MG-ALERT-021 / 022 / 023
def long_cooldown(e):
    s = json.loads(json.dumps(e.settings))
    s["yellow"]["cooldown_s"] = 3600
    s["red"]["cooldown_s"] = 3600
    e.set_settings(s)


def test_an_alert_whose_occurrence_list_is_full_stops_merging_and_the_next_episode_gets_its_own_alert():
    """MG-ALERT-021 / cycle-25 MG-ALERT-024: the occurrence list never silently drops an episode: when it is full, the next episode starts a NEW alert, and each alert resolves by its own episodes."""
    e, c = engine()
    long_cooldown(e)
    s = src()
    for n in range(am.MAX_OCCURRENCES + 5):
        e.observe_source(s, [evt(f"e{n}", "YELLOW", False, end=n + 10)], {})
        c.advance(1)
    first, second = sorted(e.alerts.values(), key=lambda x: x["created_epoch"])
    assert len(first["occurrences"]) == am.MAX_OCCURRENCES == first["occurrence_total"] and len(second["occurrences"]) == 5 == second["occurrence_total"]
    e.observe_source(s, [evt("late", "YELLOW", True)], {})                           # a later open episode joins the alert that still has room
    assert second["status"] != "resolved" and second["occurrence_total"] == 6 and first["status"] == "resolved"
    e.observe_source(s, [evt("late", "YELLOW", False, end=99)], {})
    assert second["status"] == "resolved" and second["resolved_by"] == "event_closed"


def test_several_simultaneously_open_episodes_keep_the_alert_open_until_the_last_closes():
    e, _ = engine()
    long_cooldown(e)
    s = src()
    e.observe_source(s, [evt("a", "RED"), evt("b", "RED", zone="Z1")], {})
    (alert,) = e.alerts.values()
    e.observe_source(s, [evt("a", "RED", False, end=5)], {})
    assert alert["status"] != "resolved"
    e.observe_source(s, [evt("b", "RED", False, end=6)], {})
    assert alert["status"] == "resolved"


def test_more_open_episodes_than_one_alert_can_name_are_spread_over_alerts_and_none_resolves_too_early():
    e, _ = engine()
    long_cooldown(e)
    s = src()
    n = am.MAX_TRACKED_OPEN + 20
    e.observe_source(s, [evt(f"o{i}", "YELLOW") for i in range(n)], {})
    assert len(e.alerts) == -(-n // am.MAX_OCCURRENCES) and all(len(a["occurrences"]) <= am.MAX_OCCURRENCES for a in e.alerts.values())
    assert sum(len(a["occurrences"]) for a in e.alerts.values()) == n                       # every episode is named by exactly one alert (cycle-25 MG-ALERT-024)
    e.observe_source(s, [evt(f"o{i}", "YELLOW", False, end=9) for i in range(n - 1)], {})
    assert any(a["status"] != "resolved" for a in e.alerts.values())                        # the last episode is still open: its alert is not resolved
    e.observe_source(s, [], {}, final=True)
    assert all(a["status"] == "resolved" for a in e.alerts.values())


def test_the_idempotence_memory_is_trimmed_without_forgetting_open_episodes():
    e, _ = engine()
    long_cooldown(e)
    s = src()
    e.observe_source(s, [evt("keep-open", "RED")], {})
    for n in range(5200):                                                               # closed episodes pile up past the memory bound
        e.observe_source(s, [evt(f"x{n}", "YELLOW", False, end=n + 2, zone="Z2")], {})
    (red,) = [a for a in e.alerts.values() if a["severity"] == "RED"]
    e.observe_source(s, [evt("keep-open", "RED", False, end=7)], {})
    assert red["status"] == "resolved" and len([a for a in e.alerts.values() if a["severity"] == "RED"]) == 1      # the late close found ITS alert (no bogus second alert)


def test_the_incremental_feed_is_paginated_in_revision_order_and_loses_nothing():
    e, c = engine()
    for n in range(450):
        e.observe_source(src(f"f{n}", f"f{n}:r"), [evt(f"e{n}", "RED")], {})
    seen, cursor, pages = {}, 0, 0
    while True:
        r = e.list(after=cursor, limit=100, order="asc")
        pages += 1
        for a in r["alerts"]:
            assert a["rev"] > cursor
            seen[a["alert_id"]] = a["rev"]
        cursor = r["next_after"]
        if pages == 2:
            e.observe_source(src("late", "late:r"), [evt("late", "RED")], {})          # new alert created between two pages
            first = next(iter(e.alerts))
            e.acknowledge(first)                                                          # and an old one changed
        if not r["more"]:
            break
    assert pages == 5 and len(seen) == 451 and cursor == e.rev                          # drained: the cursor equals the global revision only now
    assert e.list(after=cursor, order="asc")["alerts"] == []
    revs = list(seen.values())
    assert len(set(seen)) == 451 and max(revs) <= e.rev


def test_the_http_feed_returns_a_continuation_cursor_and_the_ids_present(fm):
    env = fm({1: always()})
    for n in range(7):
        env.alerts.observe_source(src(f"f{n}", f"f{n}:r"), [evt(f"e{n}", "RED")], {})
    r = env.c.get("/api/alerts?order=asc&after=0&limit=3").json()
    assert len(r["alerts"]) == 3 and r["more"] is True and r["next_after"] == r["alerts"][-1]["rev"]
    r2 = env.c.get(f"/api/alerts?order=asc&after={r['next_after']}&limit=10").json()
    assert len(r2["alerts"]) == 4 and r2["more"] is False
    assert len(env.c.get("/api/alerts/ids").json()["ids"]) == 7 and env.c.get("/api/alerts?order=sideways").status_code == 422


def test_the_store_enforces_its_capacity_and_never_drops_a_genuine_open_alert_for_a_drill():
    e, c = engine()
    for n in range(30):
        e.observe_source(src(f"g{n}", f"g{n}:r"), [evt(f"g{n}", "RED")], {})
        c.advance(1)
    genuine = {a["alert_id"] for a in e.alerts.values()}
    for n in range(600):                                                                  # 600 presses of the drill button, never resolved
        e.ingest_simulation(f"run_{n:016x}", [evt("s1", "RED", False, end=9), evt("s2", "YELLOW", False, end=9, zone="Z2")])
        c.advance(1)
    assert len(e.alerts) <= am.MAX_ALERTS and genuine <= set(e.alerts)                  # every genuine alert survived
    drills = [a for a in e.alerts.values() if a["simulation"]]
    assert len(drills) <= am.MAX_OPEN_DRILLS + 2 and e.summary()["capacity"]["open_drills"] == len(drills)


def test_beyond_the_hard_ceiling_new_alerts_are_refused_visibly_not_silently():
    e, c = engine()
    for n in range(am.HARD_MAX_ALERTS + 25):
        e.observe_source(src(f"h{n}", f"h{n}:r"), [evt(f"h{n}", "RED")], {})
    cap = e.summary()["capacity"]
    assert len(e.alerts) == am.HARD_MAX_ALERTS and cap["overflow_dropped"] == 25 and cap["degraded"] is True and e.counters["overflow_dropped"] == 25
    assert len(e.alerts) > am.MAX_ALERTS                                                  # between the soft and the hard limit genuine alerts ARE stored


def test_resolved_alerts_make_room_first():
    e, c = engine()
    for n in range(am.MAX_ALERTS):
        e.observe_source(src(f"r{n}", f"r{n}:r"), [evt(f"r{n}", "RED", False, end=3)], {})
        c.advance(1)
    first = min(e.alerts.values(), key=lambda a: a["created_epoch"])["alert_id"]
    e.observe_source(src("new", "new:r"), [evt("n1", "RED")], {})
    assert len(e.alerts) == am.MAX_ALERTS and first not in e.alerts and e.summary()["capacity"]["overflow_dropped"] == 0


def test_alerts_of_a_live_source_are_closed_after_a_restart_and_drills_stay_open(tmp_path):
    e, _ = engine(tmp_path)
    e.observe_source(src(), [evt("live", "RED")], {})
    e.ingest_simulation("run_aaaaaaaaaaaaaaaa", [evt("s1", "RED", False, end=9)])
    e.flush()
    e2, _ = engine(tmp_path)
    by = {a["source"]["kind"]: a for a in e2.alerts.values()}
    assert by["feed"]["status"] == "resolved" and by["feed"]["resolved_by"] == "server_restart"
    assert by["simulation"]["status"] == "new"                                           # the operator still owns the drill


# ------------------------------------------------------------------ cycle-22 review B: ALERT-024 / 026, SIM-022, STORE-022
def test_the_soft_limit_never_evicts_an_open_repeated_source_alert():
    """MG-ALERT-024: only scripted drills may be evicted; a repeated-source kernel alert is simulation=true but genuine kernel output."""
    e, c = engine()
    for n in range(am.MAX_ALERTS):
        e.observe_source(src(f"r{n}", f"r{n}:run", provenance="repeated_source_simulation"), [evt(f"r{n}", "RED")], {})
        c.advance(1)
    assert len(e.alerts) == am.MAX_ALERTS and all(a["simulation"] and a["source"]["kind"] == "feed" for a in e.alerts.values())
    first = min(e.alerts.values(), key=lambda a: a["created_epoch"])["alert_id"]
    e.observe_source(src("new", "new:run"), [evt("n1", "RED")], {})
    assert first in e.alerts and len(e.alerts) == am.MAX_ALERTS + 1 and e.summary()["capacity"]["degraded"] is True and e.summary()["capacity"]["overflow_dropped"] == 0
    e.ingest_simulation("run_cccccccccccccccc", [evt("d1", "RED", False, end=3)])          # a drill CAN be evicted later, a kernel alert cannot
    for n in range(3):
        e.ingest_simulation(f"run_{n:016x}", [evt("d1", "RED", False, end=3)])
    assert first in e.alerts


def test_two_drill_presses_in_the_same_millisecond_are_two_drills():
    e, _ = engine()                                                                          # the clock does not move between the calls
    e.ingest_simulation("run_aaaaaaaaaaaaaaaa", [evt("s1", "RED", False, end=9)])
    e.ingest_simulation("run_aaaaaaaaaaaaaaaa", [evt("s1", "RED", False, end=9)])
    e.ingest_simulation("run_aaaaaaaaaaaaaaaa", [evt("s1", "RED", False, end=9)])
    assert len(e.alerts) == 3 and len({a["source"]["source_id"] for a in e.alerts.values()}) == 3


def test_an_alert_is_not_a_displayed_in_app_notification_until_the_operator_view_says_so():
    e, _ = engine()
    e.observe_source(src(), [evt("e1", "RED"), evt("e2", "YELLOW", zone="Z2")], {})
    a, b = list(e.alerts.values())
    assert a["delivery"]["in_app"]["status"] == b["delivery"]["in_app"]["status"] == "stored" and a["delivery"]["in_app"]["at"] is None
    e.mark_seen([a["alert_id"]])
    assert a["delivery"]["in_app"]["status"] == "displayed" and a["delivery"]["in_app"]["at"] and b["delivery"]["in_app"]["status"] == "stored"
    e.acknowledge(b["alert_id"])                                                              # acknowledging presupposes it was shown
    assert b["delivery"]["in_app"]["status"] == "displayed"


def good_record(e):
    e.observe_source(src(), [evt("e1", "RED")], {})
    (a,) = e.alerts.values()
    return json.loads(json.dumps(a))


@pytest.mark.parametrize("forge", [
    lambda a: a["source"].update(kind="mystery"),                                           # a source kind this program never writes
    lambda a: a.update(simulation=False) or a["source"].update(kind="simulation", provenance="synthetic_scripted"),
    lambda a: a["source"].update(provenance="real_inference") or a["source"].update(kind="simulation"),
    lambda a: a.update(alert_id="alt_" + "0" * 16),                                         # an id that does not bind to the content
    lambda a: a.update(extra_member=1),
    lambda a: a.update(severity="CRITICAL"),
    lambda a: a.update(priority="high") or a.update(severity="YELLOW"),
    lambda a: a["delivery"].update(in_app={"status": "delivered"}),                         # the old, assumed-delivery vocabulary
    lambda a: a.update(created_epoch="soon"),
    lambda a: a["source"].update(physical_camera=True),
    lambda a: a["source"].update(label="x" * 500),
    lambda a: a.update(occurrences=[{"event_id": "e", "open": True}] * 500),
    lambda a: a.update(suppressed_count=-1),
])
def test_a_forged_or_malformed_persisted_alert_is_rejected_and_counted(tmp_path, forge):
    e, _ = engine(tmp_path)
    rec = good_record(e)
    forge(rec)
    (tmp_path / "alerts.json").write_text(json.dumps({"schema": "mg.alerts.store.v1", "rev": 5, "alerts": [rec]}))
    e2, _ = engine(tmp_path)
    assert e2.alerts == {} and e2.rejected_on_load == 1 and e2.summary()["rejected_on_load"] == 1


def test_a_valid_looking_planted_genuine_alert_cannot_drive_the_open_genuine_view(tmp_path):
    e, _ = engine(tmp_path)
    rec = good_record(e)
    rec["status"] = "new"
    (tmp_path / "alerts.json").write_text(json.dumps({"schema": "mg.alerts.store.v1", "rev": 5, "alerts": [rec]}))
    e2, _ = engine(tmp_path)
    (a,) = e2.alerts.values()
    assert a["status"] == "resolved" and a["resolved_by"] == "server_restart" and a["loaded_from_store"] is True                # its live source cannot exist any more
    s = e2.summary()
    assert s["loaded_from_disk_unverified"] == 1 and s["by_provenance"] == {} and s["open"] == 0                                # not counted as produced by this process


def test_loaded_drills_stay_open_but_are_marked_as_loaded(tmp_path):
    e, _ = engine(tmp_path)
    e.ingest_simulation("run_dddddddddddddddd", [evt("s1", "RED", False, end=9)])
    e.flush()
    e2, _ = engine(tmp_path)
    (a,) = e2.alerts.values()
    assert a["status"] == "new" and a["simulation"] is True and a["loaded_from_store"] is True


# ------------------------------------------------------------------ cycle-23 review C: MG-ALERT-023 (cooldown never mixes runs or provenances) and MG-STORE-023 (alerts)
def test_a_restart_with_another_provenance_inside_the_cooldown_is_a_new_alert_not_a_laundered_one():
    e, c = engine()
    e.observe_source(src("feed_same", "feed_same:run1"), [evt("g1", "RED", False, end=5)], {})
    c.advance(2)
    e.observe_source(src("feed_same", "feed_same:run2", provenance="repeated_source_simulation"), [evt("r1", "RED")], {})
    genuine, repeated = sorted(e.alerts.values(), key=lambda a: a["created_epoch"])
    assert genuine["simulation"] is False and repeated["simulation"] is True and repeated["source"]["source_id"] == "feed_same:run2"
    assert [o["event_id"] for o in genuine["occurrences"]] == ["g1"] and [o["event_id"] for o in repeated["occurrences"]] == ["r1"]
    assert len(e.list(simulated=False)["alerts"]) == 1 and e.counters["merged"] == 0


def test_the_reverse_restart_repeated_then_genuine_is_also_kept_apart():
    e, c = engine()
    e.observe_source(src("feed_same", "feed_same:run1", provenance="repeated_source_simulation"), [evt("r1", "RED", False, end=5)], {})
    c.advance(2)
    e.observe_source(src("feed_same", "feed_same:run2"), [evt("g1", "RED")], {})
    assert sorted(a["simulation"] for a in e.alerts.values()) == [False, True]


def test_a_restarted_run_of_the_same_provenance_is_its_own_alert_but_one_run_still_merges_its_flapping():
    e, c = engine()
    e.observe_source(src("feed_same", "feed_same:run1"), [evt("a1", "YELLOW", False, end=5)], {})
    c.advance(2)
    e.observe_source(src("feed_same", "feed_same:run1"), [evt("a2", "YELLOW")], {})            # the same run: merged (flapping)
    assert len(e.alerts) == 1
    c.advance(2)
    e.observe_source(src("feed_same", "feed_same:run2"), [evt("b1", "YELLOW")], {})            # a new run: never folded into the old run's alert
    assert len(e.alerts) == 2


def test_nested_extra_members_in_a_persisted_alert_are_rejected():
    base = AlertEngine(None, wall=Clock(), mono=Clock())
    base.observe_source(src(), [evt("e1", "RED")], {})
    (rec0,) = base.alerts.values()
    good = json.loads(json.dumps(rec0))
    assert am.valid_alert(good)
    for edit in (lambda a: a["event"].update(attacker_extra={"token": "x"}), lambda a: a["delivery"]["browser"].update(attacker_extra=1), lambda a: a["delivery"]["in_app"].update(x=1),
                 lambda a: a["event"].update(tracks=["t"] * 50), lambda a: a["delivery"]["sound"].update(detail="d" * 500), lambda a: a["event"].pop("rule_id")):
        bad = json.loads(json.dumps(good))
        edit(bad)
        assert not am.valid_alert(bad)


def test_the_alert_projection_never_returns_members_the_program_does_not_define():
    e, _ = engine()
    e.observe_source(src(), [evt("e1", "RED")], {})
    (a,) = e.alerts.values()
    a["internal_secret"] = "S"                                                              # something that should never leave, however it got there
    a["event"]["leak"] = {"token": "T"}
    a["delivery"]["browser"]["leak"] = 1
    a["source"]["leak"] = 1
    out = e.public(a)
    text = json.dumps(out)
    assert "internal_secret" not in text and "leak" not in text and "token" not in text and "created_epoch" not in out
    assert set(out["delivery"]["browser"]) == {"status", "at", "detail"} and set(out["event"]) == {"event_id", "rule_id", "start_us", "end_us", "open", "tracks", "closed_reason", "right_censored"}
