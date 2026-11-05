"""MG-PROV-028 (cycle-28 review): the run-level repeated-source label stays conservative, but the run keeps WHEN the overlap was present (bounded by sample times) so the period before it,
the period during it and the period after it stay distinguishable on every surface; a record without that evidence is `unknown`, never `alone`. Synthetic data only."""
import copy
import time

import pytest

from mineguard import monitor
from mineguard.alerts import AlertEngine, valid_alert
from test_vertical05_provenance import wait_alert
from test_vertical2_alerts import Clock, evt, src
from test_vertical2_feeds import PERSON_IN, VEHICLE_IN, always, act, create, fm, view  # noqa: F401

try:
    from mineguard import overlap
except ImportError:      # the previous checkpoint has no such module: tests then FAIL on the missing behaviour (they are not collection errors), which is the fail-before record
    overlap = None


def doc(intervals, launch=False):
    return {"launch_repeated": launch, "intervals": intervals, "evidence": "sample_time_bounds", "truncated": False}


# ---------------------------------------------------------------- the pure phase function
@pytest.mark.parametrize("start,intervals,launch,want", [
    (500, [], False, "alone"),
    (500, [{"from_after_us": 1000, "until_before_us": None}], False, "before_overlap"),
    (1000, [{"from_after_us": 1000, "until_before_us": None}], False, "before_overlap"),          # committed at or before the sample at which the overlap was noticed
    (1001, [{"from_after_us": 1000, "until_before_us": None}], False, "during_overlap"),
    (5000, [{"from_after_us": 1000, "until_before_us": 4000}], False, "after_overlap"),
    (3999, [{"from_after_us": 1000, "until_before_us": 4000}], False, "during_overlap"),
    (4000, [{"from_after_us": 1000, "until_before_us": 4000}], False, "after_overlap"),           # the sample at which it was found over is alone again
    (4500, [{"from_after_us": 1000, "until_before_us": 4000}, {"from_after_us": 6000, "until_before_us": None}], False, "between_overlaps"),
    (6500, [{"from_after_us": 1000, "until_before_us": 4000}, {"from_after_us": 6000, "until_before_us": None}], False, "during_overlap"),
    (0, [{"from_after_us": None, "until_before_us": None}], False, "during_overlap"),               # noticed before any sample: everything committed is inside it
    (10, [], True, "from_launch"),
    (10, [{"from_after_us": 100, "until_before_us": None}], True, "from_launch"),
])
def test_phase_of(start, intervals, launch, want):
    assert overlap.phase_of(start, doc(intervals, launch)) == want


@pytest.mark.parametrize("bad", [None, {}, {"launch_repeated": 1, "intervals": [], "evidence": "sample_time_bounds", "truncated": False}, {"launch_repeated": False, "intervals": [], "evidence": "sample_time_bounds"}, {"launch_repeated": False, "intervals": [], "evidence": "sample_time_bounds", "truncated": 1}, {"launch_repeated": False, "intervals": [], "evidence": "exact"},
                                 doc([{"from_after_us": 5, "until_before_us": 5}]), doc([{"from_after_us": 5, "until_before_us": None}, {"from_after_us": 9, "until_before_us": None}]),
                                 doc([{"from_after_us": 5, "until_before_us": 9}, {"from_after_us": 7, "until_before_us": None}]), doc([{"from_after_us": 1.5, "until_before_us": None}]),
                                 doc([{"from_after_us": 1, "until_before_us": None, "x": 1}]), doc([{"from_after_us": True, "until_before_us": None}])])
def test_a_malformed_overlap_document_is_unknown_never_alone(bad):
    assert overlap.valid_doc(bad) is False and overlap.phase_of(100, bad) == "unknown"


def test_no_interval_evidence_means_unknown_for_a_repeated_record_and_alone_for_one_that_never_overlapped():
    assert overlap.legacy_phase(True) == "unknown" and overlap.legacy_phase(False) == "alone"


# ---------------------------------------------------------------- alerts: run-level label stays conservative, the phase keeps the pre-overlap evidence
def feed_src(overlap_doc, provenance="real_inference"):
    s = src(provenance=provenance)
    s["overlap"] = overlap_doc
    return s


def test_an_alert_made_alone_keeps_its_pre_overlap_phase_while_the_run_label_turns_conservative(tmp_path):
    e = AlertEngine(tmp_path, wall=Clock(), mono=Clock())
    e1 = {**evt("e1", "RED", False, end=2_000_000), "start_us": 1_000_000}
    e.observe_source(feed_src(doc([])), [e1], {})
    (a,) = e.alerts.values()
    assert e.public(a)["overlap_phase"] == "alone" and a["simulation"] is False
    # a second feed of the file starts: noticed after the sample at 3.0 s
    d2 = doc([{"from_after_us": 3_000_000, "until_before_us": None}])
    e2 = {**evt("e2", "YELLOW", True), "start_us": 3_500_000}
    e.observe_source(feed_src(d2, "repeated_source_simulation"), [e1, e2], {})
    by = {x["event_id"]: x for x in e.alerts.values()}
    assert by["e1"]["simulation"] is True and by["e1"]["source"]["provenance"] == "repeated_source_simulation"                    # run-level: conservative for EVERY alert of the run
    assert e.public(by["e1"])["overlap_phase"] == "before_overlap" and e.public(by["e2"])["overlap_phase"] == "during_overlap"      # but the period before it stays distinguishable
    # the overlap ends at the sample at 6.0 s; a later event is after it; earlier phases never change
    d3 = doc([{"from_after_us": 3_000_000, "until_before_us": 6_000_000}])
    e3 = {**evt("e3", "RED", True, zone="Z2"), "start_us": 7_000_000}
    e.observe_source(feed_src(d3, "repeated_source_simulation"), [e1, e2, e3], {})
    by = {x["event_id"]: x for x in e.alerts.values()}
    assert [e.public(by[k])["overlap_phase"] for k in ("e1", "e2", "e3")] == ["before_overlap", "during_overlap", "after_overlap"]
    assert all(v["simulation"] is True for v in by.values())                                                                       # nothing is ever upgraded back to genuine
    assert all(valid_alert(v) for v in by.values())


def test_an_alert_that_merged_episodes_of_different_phases_says_mixed_and_stays_so(tmp_path):
    e = AlertEngine(tmp_path, wall=Clock(), mono=Clock())
    d = doc([{"from_after_us": 3_000_000, "until_before_us": None}])
    e1 = {**evt("e1", "RED", False, end=2_000_000), "start_us": 1_000_000}
    e4 = {**evt("e4", "RED", True), "start_us": 4_000_000}                                  # same zone and severity inside the cooldown: merged into e1's alert
    e.observe_source(feed_src(d, "repeated_source_simulation"), [e1, e4], {})
    (a,) = e.alerts.values()
    assert a["occurrence_total"] == 2 and e.public(a)["overlap_phase"] == "mixed" and valid_alert(a)
    e.observe_source(feed_src(doc([{"from_after_us": 3_000_000, "until_before_us": 5_000_000}]), "repeated_source_simulation"), [e1, e4], {})
    assert e.public(a)["overlap_phase"] == "mixed"                                           # later, more precise bounds never erase it


def test_a_forged_or_foreign_phase_in_a_stored_alert_is_refused_and_an_old_alert_reads_unknown_or_alone(tmp_path):
    e = AlertEngine(tmp_path, wall=Clock(), mono=Clock())
    e.observe_source(feed_src(doc([])), [{**evt("e1", "RED", False, end=5), "start_us": 1}], {})
    (a,) = e.alerts.values()
    bad = copy.deepcopy(a); bad["overlap_phase"] = "genuine"
    assert valid_alert(a) and not valid_alert(bad)
    old = copy.deepcopy(a); old.pop("overlap_phase")
    assert valid_alert(old) and e.public(old)["overlap_phase"] == "alone"                                                           # never overlapped: alone
    old["source"]["repeated_source"], old["source"]["provenance"], old["simulation"] = True, "repeated_source_simulation", True
    assert e.public(old)["overlap_phase"] == "unknown"                                                                             # repeated and no interval evidence: unknown
    sim = AlertEngine(tmp_path / "s", wall=Clock(), mono=Clock())
    sim.ingest_simulation("run_dddddddddddddddd", [evt("s1", "RED", False, end=9)])
    assert [sim.public(x)["overlap_phase"] for x in sim.alerts.values()] == [None]                                                 # a scripted simulation has no overlap notion


# ---------------------------------------------------------------- the real feed: intervals bounded by sample times, restart resets, summaries and history agree
def test_a_feed_records_when_the_overlap_began_and_ended_and_keeps_the_period_before_it(fm):
    env = fm({1: always(person=PERSON_IN, vehicle=VEHICLE_IN)}, frames=400, delay={1: 0.01})
    a = create(env, 1, loop=True)
    act(env, a, "start")
    (first,) = wait_alert(env, a)[:1]
    v = view(env, a)
    assert v["overlap"] == {"launch_repeated": False, "intervals": [], "evidence": "sample_time_bounds", "truncated": False} and {e["overlap_phase"] for e in v["events"]} == {"alone"}
    t_before = v["source_time_us"]
    b = create(env, 1, label="B", loop=True)
    act(env, b, "start"); time.sleep(0.5)
    v = view(env, a)
    (iv,) = v["overlap"]["intervals"]
    assert iv["until_before_us"] is None and iv["from_after_us"] is not None and iv["from_after_us"] >= t_before                       # noticed after the sample we had seen alone
    assert v["shared_now"] is True and v["source"]["provenance"] == "repeated_source_simulation"
    assert {e["overlap_phase"] for e in v["events"]} == {"before_overlap"}                                                           # the episode that began alone is still distinguishable
    assert env.alerts.public(first)["overlap_phase"] == "before_overlap" and env.alerts.public(first)["source"]["provenance"] == "repeated_source_simulation"
    act(env, b, "stop"); time.sleep(0.5)                                                                                              # A keeps committing samples: the end is found
    v = view(env, a)
    (iv,) = v["overlap"]["intervals"]
    assert v["shared_now"] is False and v["shared_during_run"] is True and iv["until_before_us"] is not None and iv["until_before_us"] > iv["from_after_us"]
    assert v["source"]["provenance"] == "repeated_source_simulation"                                                                  # the present relationship ended; the evidentiary label did not
    assert v["counters"]["analyzed"] > 0 and v["state"] == "playing"                                                                  # no reset of counters, tracking or risk
    act(env, b, "start"); time.sleep(0.5)                                                                                             # a rejoin is a SECOND interval
    assert len(view(env, a)["overlap"]["intervals"]) == 2
    act(env, a, "stop"); act(env, b, "stop")
    hist = [r for r in env.c.get("/api/feeds/history").json()["runs"] if r["feed_id"] == a]
    assert hist and hist[0]["overlap"]["intervals"] and {e["overlap_phase"] for e in hist[0]["events"]} == {"before_overlap"}        # the saved history agrees with the live view


def test_the_second_feed_that_launched_as_a_repeat_is_repeated_from_launch(fm):
    env = fm({1: always(person=PERSON_IN, vehicle=VEHICLE_IN)}, frames=400, delay={1: 0.01})
    a = create(env, 1, loop=True); b = create(env, 1, label="B", loop=True)
    act(env, a, "start"); time.sleep(0.2); act(env, b, "start"); time.sleep(0.5)
    vb = view(env, b)
    assert vb["overlap"]["launch_repeated"] is True and {e["overlap_phase"] for e in vb["events"]} <= {"from_launch"}
    act(env, a, "stop"); act(env, b, "stop")


def test_a_restart_is_a_new_run_with_a_fresh_overlap_history(fm):
    env = fm({1: always()}, frames=60)
    from test_vertical2_feeds import wait_state
    a, b = create(env, 1), create(env, 1, label="B")
    act(env, a, "start"); act(env, b, "start")
    wait_state(env, [a, b], ("ended",))
    env.c.delete(f"/api/feeds/{b}")
    act(env, a, "start"); time.sleep(0.2)
    assert view(env, a)["overlap"] == {"launch_repeated": False, "intervals": [], "evidence": "sample_time_bounds", "truncated": False}
    act(env, a, "stop")


def test_a_saved_summary_without_overlap_evidence_reads_unknown_when_repeated_and_alone_when_not(fm):
    env = fm({1: always(person=PERSON_IN, vehicle=VEHICLE_IN)}, frames=400, delay={1: 0.01})
    a = create(env, 1, loop=True)
    act(env, a, "start"); wait_alert(env, a); act(env, a, "stop")
    f = env.feeds.feeds[a]
    d = {**f.summary_doc(), "id": "feedrun_" + "0" * 16}
    assert monitor.valid_feedrun(d) and {e["overlap_phase"] for e in monitor.project_feedrun(d)["events"]} == {"alone"}
    legacy = {k: v for k, v in d.items() if k != "overlap"}
    assert monitor.valid_feedrun(legacy) and {e["overlap_phase"] for e in monitor.project_feedrun(legacy)["events"]} == {"alone"}
    rep = copy.deepcopy(legacy); rep["source"]["repeated_source"], rep["source"]["provenance"] = True, "repeated_source_simulation"
    assert {e["overlap_phase"] for e in monitor.project_feedrun(rep)["events"]} == {"unknown"}
    forged = copy.deepcopy(d); forged["overlap"] = {"launch_repeated": False, "intervals": [{"from_after_us": 9, "until_before_us": 3}], "evidence": "sample_time_bounds", "truncated": False}
    assert monitor.valid_feedrun(forged) is False


# ---------------------------------------------------------------- MG-PROV-029: the interval bound is never silent
def test_a_truncated_history_makes_every_later_phase_unknown(monkeypatch):
    d = doc([{"from_after_us": 1000, "until_before_us": 2000}])
    d["truncated"] = True
    assert overlap.valid_doc(d)
    assert overlap.phase_of(500, d) == "before_overlap" and overlap.phase_of(1500, d) == "during_overlap"          # what was recorded stays precise
    assert overlap.phase_of(2500, d) == "unknown"                                                                   # not «after_overlap»: later overlaps were not recorded
    assert overlap.phase_of(2500, doc([{"from_after_us": 1000, "until_before_us": 2000}])) == "after_overlap"


def test_the_65th_overlap_sets_the_marker_instead_of_being_discarded_silently(fm, monkeypatch):
    monkeypatch.setattr(overlap, "MAX_INTERVALS", 2)
    env = fm({1: always(person=PERSON_IN, vehicle=VEHICLE_IN)}, frames=400, delay={1: 0.01})
    a = create(env, 1, loop=True)
    act(env, a, "start"); wait_alert(env, a)
    b = create(env, 1, label="B", loop=True)
    for _ in range(3):                                                                # three disjoint overlaps with a bound of two
        act(env, b, "start"); time.sleep(0.4); act(env, b, "stop"); time.sleep(0.4)
    v = view(env, a)
    assert len(v["overlap"]["intervals"]) == 2 and v["overlap"]["truncated"] is True
    assert v["source"]["provenance"] == "repeated_source_simulation" and v["shared_during_run"] is True        # still never laundered into genuine
    assert {e["overlap_phase"] for e in v["events"]} == {"before_overlap"}                                    # the episode that began alone stays distinguishable
    act(env, a, "stop"); act(env, b, "stop")
    assert overlap.valid_doc(view(env, a)["overlap"])
