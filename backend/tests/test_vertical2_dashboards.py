"""MG-VERTICAL-02 checkpoint C: operator and developer dashboards. Aggregates are checked against the counters that feed them; synthetic data only."""
import json
import os
import time

from mineguard import monitor
from mineguard.alerts import AlertEngine
from test_vertical2_alerts import evt, src  # noqa: F401
from test_vertical2_feeds import PERSON_IN, VEHICLE_IN, always, act, create, fm, view, wait_state  # noqa: F401


def overview(env, **q):
    return env.c.get("/api/monitor/overview", params=q)


def test_an_empty_system_reports_empty_datasets_honestly(fm):
    env = fm({1: always()})
    r = overview(env)
    assert r.status_code == 200
    o = r.json()
    assert o["feeds"] == [] and o["active_feeds"] == 0 and o["alerts"]["total"] == 0 and o["recent_timeline"] == [] and o["events_per_source"] == {}
    assert o["trend"]["has_data"] is False and o["saved_feed_runs"] == 0
    assert o["counts_kept_apart"]["per_frame_detections"] == 0 and o["counts_kept_apart"]["unique_tracks"] == 0
    assert o["sessions_retention"] == {"frames": 0, "metadata_only": 0, "media_deleted": 0, "total": 0}
    assert o["health"]["scheduler_alive"] is False and o["health"]["model_loaded"] is False


def test_detections_tracks_events_and_deliveries_are_four_different_numbers(fm):
    env = fm({1: always(person=PERSON_IN, vehicle=VEHICLE_IN), 2: always(person=PERSON_IN), 3: always()}, frames=150)
    fids = [create(env, c, f"Fuente {c}") for c in (1, 2, 3)]
    for f in fids:
        act(env, f, "start")
    vs = wait_state(env, fids, ("ended",))
    o = overview(env).json()
    k = o["counts_kept_apart"]
    assert k["per_frame_detections"] == sum(v["counters"]["detections"] for v in vs) > 40
    vehicle_frames = vs[0]["class_hist"]["vehicle"]
    assert vehicle_frames > 10 and vs[0]["tracks_unique"]["vehicle"] == 1                                    # many detections of the same truck are ONE track, never many vehicles
    assert k["unique_tracks"] == sum(sum(v["tracks_unique"].values()) for v in vs) < k["per_frame_detections"]
    live = k["risk_events_live"]
    assert live["RED"] >= 1 and live["YELLOW"] >= 1 and live["RED"] + live["YELLOW"] < k["unique_tracks"] + 5
    assert k["notification_deliveries"]["in_app_stored"] == o["alerts"]["total"] > 0 and k["notification_deliveries"]["in_app_displayed"] == 0 and k["notification_deliveries"]["browser"] == 0     # nobody told the server a browser notification was shown
    by = o["events_per_source"]
    assert len(by) == 3 and by[f"Fuente 3 ({fids[2]})"]["RED"] == 0 and by[f"Fuente 1 ({fids[0]})"]["RED"] >= 1
    rows = {r["feed_id"]: r for r in o["feeds"]}
    assert rows[fids[2]]["risk"] in ("GREEN", "UNKNOWN") and rows[fids[0]]["provenance"] == "real_inference"
    assert o["saved_feed_runs"] == 3 and k["risk_events_in_saved_feed_history_real"]["RED"] >= 1


def test_repeated_source_events_are_never_mixed_with_real_ones(fm):
    env = fm({1: always(person=PERSON_IN, vehicle=VEHICLE_IN)}, frames=150)
    a, b = create(env, 1, "Original"), create(env, 1, "Copia")
    act(env, a, "start"); act(env, b, "start")
    wait_state(env, [a, b], ("ended",))
    k = overview(env).json()["counts_kept_apart"]
    assert k["risk_events_live"] == {"RED": 0, "YELLOW": 0}                                                    # both feeds replay the SAME file: a concurrency simulation
    assert k["risk_events_from_simulated_sources"]["RED"] >= 2
    assert k["risk_events_in_saved_feed_history_real"] == {"RED": 0, "YELLOW": 0}
    assert all(r["repeated_source"] and r["provenance"] == "repeated_source_simulation" for r in overview(env).json()["feeds"])
    assert all(x["simulation"] for x in env.c.get("/api/alerts").json()["alerts"])


def test_simulation_alerts_are_separated_in_the_summary_and_timeline(fm):
    env = fm({1: always()})
    env.alerts.ingest_simulation("run_0123456789abcdef", [evt("s1", "RED", False, end=9)])
    o = overview(env).json()
    assert o["alerts"]["by_provenance"] == {"synthetic_scripted": 1}
    assert o["recent_timeline"][0]["provenance"] == "synthetic_scripted" and o["recent_timeline"][0]["simulation"] is True


def test_interval_filters_are_validated(fm):
    env = fm({1: always()})
    assert overview(env, minutes=7).status_code == 422 and overview(env, minutes=60, bucket=7).status_code == 422 and overview(env, minutes=15, bucket=60).status_code == 422
    for m, b in ((15, 1), (60, 5), (240, 15), (1440, 60)):
        t = overview(env, minutes=m, bucket=b).json()["trend"]
        assert t["minutes"] == m and len(t["buckets"]) == m // b


def test_the_trend_buckets_count_alerts_by_severity_and_time_only_where_data_exists():
    now = 1_800_000_000.0
    mk = lambda ago, sev, sim=False: {"created_epoch": now - ago, "severity": sev, "simulation": sim}
    t = monitor.trend([mk(30, "RED"), mk(90, "YELLOW"), mk(100, "YELLOW", True), mk(3600 * 5, "RED")], 60, 5, now)
    assert len(t["buckets"]) == 12 and t["has_data"] is True
    assert t["buckets"][-1]["RED"] == 1 and t["buckets"][-1]["YELLOW"] == 1 and t["buckets"][-1]["simulated"] == 1     # the simulated YELLOW is counted apart, never in the severity series (cycle-25 MG-TREND-025)
    assert sum(b["RED"] for b in t["buckets"]) == 1                                                            # the alert from 5 hours ago is outside the window
    assert monitor.trend([], 60, 5, now)["has_data"] is False


def test_session_retention_status_comes_from_the_small_records_only(fm, tmp_path):
    env = fm({1: always()})
    runs = env.runs
    def sess(name, retention=None, tomb=False):
        d = runs / name
        d.mkdir(parents=True)
        doc = {"kind": "webcam"}
        if retention:
            doc["retention"] = {"mode": retention, "frames_persisted": retention == "frames"}
        (d / "source.json").write_text(json.dumps(doc))
        if tomb:
            (d / "media_deleted.json").write_text("{}")
    sess("session_" + "a" * 16, "metadata_only")
    sess("session_" + "b" * 16, "frames")
    sess("session_" + "c" * 16, "frames", tomb=True)
    sess("session_" + "d" * 16)                                                                               # older session without the field: frames were stored
    os.symlink(runs / ("session_" + "a" * 16), runs / ("session_" + "e" * 16))                                   # a symlink is never counted
    assert overview(env).json()["sessions_retention"] == {"frames": 2, "metadata_only": 1, "media_deleted": 1, "total": 4}


def test_the_developer_dashboard_reports_only_what_is_measured(fm, tmp_path):
    env = fm({1: always(person=PERSON_IN)}, frames=100, delay={1: 0.01})
    d0 = env.c.get("/api/monitor/developer").json()
    assert d0["feeds"] == [] and d0["model"]["loaded"] is False and d0["model"]["model_id"] and d0["aggregate_inference_fps"] == 0
    a = create(env, 1)
    act(env, a, "start")
    wait_state(env, [a], ("ended",))
    d = env.c.get("/api/monitor/developer").json()
    assert d["model"]["loaded"] is True and d["model"]["weights_sha256_prefix"] and len(d["model"]["weights_sha256_prefix"]) == 16
    v = d["versions"]
    assert v["risk_kernel_engine"].startswith("mg-risk-kernel/") and v["tracker"] and v["alert_api"] == "mg.alert-api/1" and v["limits"] == {"max_feeds": 5, "validated_feeds": 3, "feed_queue_max": 2}
    (f,) = d["feeds"]
    fr = f["frames"]
    assert fr["submitted"] == fr["accepted"] == fr["analyzed"] + fr["dropped"] + fr["adapter_errors"] and fr["decoded"] == fr["submitted"] + fr["skipped_by_sampling_intentional"]
    assert f["latency_ms"]["p50"] is not None and f["latency_ms"]["p95"] >= f["latency_ms"]["p50"] and f["class_distribution"]["person"] > 0 and f["tracks_unique"]["person"] >= 1
    assert f["trust"].startswith("live_summary") and f["queue_max"] == 2 and f["max_queue_depth"] <= 2
    r = d["resources"]
    assert r["measured"] is True and r["at_utc"].endswith("Z") and set(r) >= {"process_rss_mb", "host_pressure_level", "host_free_percent", "swap_used_mb", "decoder_service_mb"}
    assert "counters kept by the running process" in d["metrics_policy"]


def test_unmeasured_values_are_null_not_zero(fm):
    env = fm({1: always()}, frames=60)
    a = create(env, 1)
    f = env.c.get("/api/monitor/developer").json()["feeds"][0]
    assert f["inference_fps"] is None and f["latency_ms"]["p50"] is None and f["latency_ms"]["p95"] is None


def test_the_acceptance_state_is_taken_from_a_recorded_file_and_labelled_as_such(fm, tmp_path):
    env = fm({1: always()})
    none = monitor.developer_overview(env.st, env.feeds, env.alerts, env.runs, tmp_path)
    assert none["acceptance_state"]["available"] is False
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "MG-ACCEPTANCE-STATE.json").write_text(json.dumps({"commit": "abc", "gate": "NOT RUN"}))
    got = monitor.developer_overview(env.st, env.feeds, env.alerts, env.runs, tmp_path)["acceptance_state"]
    assert got["commit"] == "abc" and "can be older than the running code" in got["note"]


def test_the_dashboards_leak_no_image_data_token_or_environment(fm):
    env = fm({1: always(person=PERSON_IN)}, frames=80, delay={1: 0.02})
    a = create(env, 1)
    act(env, a, "start")
    time.sleep(0.4)
    texts = [env.c.get(u).text for u in ("/api/monitor/overview", "/api/monitor/developer", "/api/feeds", "/api/alerts", "/api/feeds/history")]
    wait_state(env, [a], ("ended",))
    texts += [env.c.get(u).text for u in ("/api/monitor/overview", "/api/monitor/developer", "/api/feeds/history")]
    token = env.app.state.session_token
    for t in texts:
        assert "/9j/" not in t and "iVBOR" not in t and "base64" not in t
        assert token not in t
        for name in ("HOME", "PATH", "USER", "SHELL"):
            assert f'"{name}"' not in t
        assert os.path.expanduser("~") not in t


def test_saved_summaries_count_as_real_only_when_this_process_wrote_exactly_those_bytes(fm):
    """MG-STORE-022: a summary from before a restart, an edited one or a planted one is `unverified` and never counts as real history."""
    env = fm({1: always(person=PERSON_IN, vehicle=VEHICLE_IN)}, frames=150)
    a = create(env, 1)
    act(env, a, "start")
    (v,) = wait_state(env, [a], ("ended",))
    rows = env.c.get("/api/feeds/history").json()["runs"]
    assert [r["trust"] for r in rows] == ["live_summary"] and env.c.get("/api/monitor/overview").json()["counts_kept_apart"]["risk_events_in_saved_feed_history_real"]["RED"] >= 1
    f = env.runs / "feeds" / f"{v['result_id']}.json"
    doc = json.loads(f.read_text())
    doc["events"] = doc["events"] * 5                                                         # an edit: same file name, inflated content
    f.write_text(json.dumps(doc, indent=1, ensure_ascii=False))
    o = env.c.get("/api/monitor/overview").json()["counts_kept_apart"]
    assert env.c.get("/api/feeds/history").json()["runs"][0]["trust"] == "unverified"
    assert o["risk_events_in_saved_feed_history_real"] == {"RED": 0, "YELLOW": 0} and o["risk_events_in_saved_feed_history_unverified"]["RED"] >= 5
    env.feeds.granted.clear()                                                                  # a restart: the grant is in memory only
    assert env.c.get("/api/feeds/history").json()["runs"][0]["trust"] == "unverified"


def test_a_planted_or_malformed_summary_is_not_listed_at_all(fm):
    env = fm({1: always()})
    d = env.runs / "feeds"
    d.mkdir(parents=True)
    good = {"schema": "mg.feedrun.v1", "id": "feedrun_" + "a" * 16, "feed_id": "feed_" + "b" * 8, "label": "X", "source": {"kind": "mp4_file", "source_id": "s", "video_id": "v", "filename": "f.mp4", "sha256_prefix": "0" * 16,
            "repeated_source": False, "provenance": "real_inference", "physical_camera": False, "note": "n"}, "config": {"sample_fps": 2.0, "interval_ms": 500, "threshold_permille": 300, "mode": "realtime", "speed": 1.0, "loop": False},
            "counters": {"analyzed": 1}, "latency_ms": {"p50": 1, "p95": 2, "max": 3}, "queue_wait_ms": {"p50": 1, "p95": 2}, "max_queue_depth": 0,
            "tracks_unique": {}, "class_hist": {}, "events": [], "zones": [], "zones_revision": 1, "closed_reason": "end_of_stream", "error": None, "passes": 0, "source_time_us": 0, "producer": {"adapter": "a", "kernel": "k", "tracker": "t", "app": "x"},
            "origin": "model_inference_live_feed", "trust": "live_summary", "retention": {"mode": "metadata_only", "frames_persisted": False}, "started_utc": None, "ended_utc": "2026-01-01T00:00:00Z"}
    (d / f"feedrun_{'a' * 16}.json").write_text(json.dumps(good))
    bads = {"c": {**good, "id": "feedrun_" + "c" * 16, "origin": "camera"}, "d": {**good, "id": "feedrun_" + "d" * 16, "retention": {"mode": "frames", "frames_persisted": True}},
            "e": {**good, "id": "feedrun_" + "e" * 16, "extra": 1}, "f": {**good, "id": "feedrun_" + "f" * 16, "source": {**good["source"], "physical_camera": True}},
            "9": {**good, "id": "feedrun_" + "9" * 16, "events": [{"severity": "RED"}] * 3}}
    for k, doc in bads.items():
        (d / f"feedrun_{k * 16}.json").write_text(json.dumps(doc))
    (d / f"feedrun_{'1' * 16}.json").write_text(json.dumps({**good, "id": "feedrun_" + "2" * 16}))      # an id that does not match its file name
    ids = {r["id"] for r in env.c.get("/api/feeds/history").json()["runs"]}
    assert ids == {"feedrun_" + "a" * 16} and env.c.get("/api/feeds/history").json()["runs"][0]["trust"] == "unverified"


def planted_good():
    return {"schema": "mg.feedrun.v1", "id": "feedrun_" + "a" * 16, "feed_id": "feed_" + "b" * 8, "label": "X", "source": {"kind": "mp4_file", "source_id": "s", "video_id": "v", "filename": "f.mp4", "sha256_prefix": "0" * 16,
            "repeated_source": False, "provenance": "real_inference", "physical_camera": False, "note": "n"}, "config": {"sample_fps": 2.0, "interval_ms": 500, "threshold_permille": 300, "mode": "realtime", "speed": 1.0, "loop": False},
            "counters": {"analyzed": 1}, "latency_ms": {"p50": 1, "p95": 2, "max": 3}, "queue_wait_ms": {"p50": 1, "p95": 2}, "max_queue_depth": 0, "tracks_unique": {}, "class_hist": {}, "events": [], "zones": [],
            "zones_revision": 1, "closed_reason": "end_of_stream", "error": None, "passes": 0, "source_time_us": 0, "producer": {"adapter": "a", "kernel": "k", "tracker": "t", "app": "x"}, "origin": "model_inference_live_feed",
            "trust": "live_summary", "retention": {"mode": "metadata_only", "frames_persisted": False}, "started_utc": None, "ended_utc": "2026-01-01T00:00:00Z"}


def test_nested_members_of_a_saved_summary_are_validated_and_nothing_extra_can_come_back_out(fm):
    """MG-STORE-023: every nested record has exact members; the API returns a fixed projection."""
    env = fm({1: always()})
    d = env.runs / "feeds"
    d.mkdir(parents=True)
    assert monitor.valid_feedrun(planted_good())
    variants = {}
    for i, (name, edit) in enumerate([("config", lambda x: x["config"].update(evil="x")), ("latency", lambda x: x["latency_ms"].update(token="t")), ("producer", lambda x: x["producer"].update(secret="s")),
                                      ("queue", lambda x: x["queue_wait_ms"].update(x=1)), ("class_hist", lambda x: x["class_hist"].update({"k" * 80: 1})),
                                      ("zones", lambda x: x["zones"].append({"zone_id": "Z", "digest": "d", "extra": 1})), ("source_note", lambda x: x["source"].update(note="n" * 5000)),
                                      ("event", lambda x: x["events"].append({"event_id": "e", "zone_id": "Z", "rule_id": "r", "severity": "RED", "start_us": 1, "end_us": None, "closed_reason": None, "tracks": [],
                                                                               "right_censored": None, "left_censored": None, "attacker": 1})),
                                      ("counters", lambda x: x["counters"].update({"a": "str"}))]):
        doc = planted_good()
        doc["id"] = "feedrun_" + format(i + 1, "x") * 16
        edit(doc)
        assert not monitor.valid_feedrun(doc), name
        (d / f"{doc['id']}.json").write_text(json.dumps(doc))
    (d / f"feedrun_{'a' * 16}.json").write_text(json.dumps(planted_good()))
    rows = env.c.get("/api/feeds/history").json()["runs"]
    assert [r["id"] for r in rows] == ["feedrun_" + "a" * 16] and set(rows[0]) == set(monitor.project_feedrun(planted_good())) | {"trust"}


def test_restored_alerts_do_not_count_as_deliveries_and_are_reported_apart(fm, tmp_path):
    env = fm({1: always()})
    env.alerts.ingest_simulation("run_aaaaaaaaaaaaaaaa", [evt("s1", "RED", False, end=9)])
    env.alerts.flush()
    reloaded = AlertEngine(env.runs / "alerts")
    env.st.alerts = reloaded
    env.feeds.alerts = reloaded
    k = monitor.operator_overview(env.st, env.feeds, reloaded, env.runs)["counts_kept_apart"]["notification_deliveries"]
    assert k["in_app_stored"] == 0 and k["loaded_from_disk_unverified"] == 1
