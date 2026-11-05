"""Cycle-24 remediation vectors for MG-ALERT-024 (OAF-VERTICAL-01). Synthetic only. Each vector asserts the CORRECT behaviour; it failed on 147f139 (see the fail-before evidence)."""
import pytest

from mineguard.alerts import MAX_OCCURRENCES, valid_alert
from test_vertical2_alerts import engine, evt, good_record, src


def _st(t):
    return {"state": "UNKNOWN", "pending": [], "causes": ["frame_not_analyzed"], "t_us": t}


def _sessions():
    return src("webcam:Cam", "webcam:s1", kind="webcam_session"), src("webcam:Cam", "webcam:s2", kind="webcam_session")


# ---------------------------------------------------------------- MG-ALERT-024
def test_every_occurrence_of_a_merged_alert_keeps_the_identity_of_its_own_session():
    e, c = engine()
    s1, s2 = _sessions()
    e.observe_source(s1, [evt("a", "RED", False, end=5)], {})
    c.advance(2)
    e.observe_source(s2, [evt("b", "RED")], {})
    (a,) = e.alerts.values()
    assert [(o["event_id"], o["source_id"]) for o in a["occurrences"]] == [("a", "webcam:s1"), ("b", "webcam:s2")]
    pub = e.public(a)
    assert [o["source_id"] for o in pub["occurrences"]] == ["webcam:s1", "webcam:s2"]
    assert valid_alert(a)                                   # the richer record is itself valid and round-trips


def test_closing_one_session_event_is_still_attributed_to_that_session_and_does_not_resolve_the_other():
    e, c = engine()
    s1, s2 = _sessions()
    e.observe_source(s1, [evt("a", "RED")], {})
    c.advance(2)
    e.observe_source(s2, [evt("b", "RED")], {})
    e.observe_source(s1, [evt("a", "RED", False, end=9)], {})
    (a,) = e.alerts.values()
    assert a["status"] != "resolved"                        # s2's episode is still open
    assert {o["event_id"]: o["open"] for o in a["occurrences"]} == {"a": False, "b": True}


def test_suppressed_unknown_diagnostic_of_another_session_leaves_a_bounded_attributable_occurrence():
    e, c = engine()
    s1, s2 = _sessions()
    e.observe_source(s1, [], {"Z1": _st(1)})
    e.observe_source(s1, [], {"Z1": _st(20_000_000)})
    c.advance(2)
    e.observe_source(s2, [], {"Z1": _st(1)})
    for k in range(5):                                      # repeated observations of the same uncertainty must not grow the record
        e.observe_source(s2, [], {"Z1": _st(20_000_000 + k)})
    (d,) = e.alerts.values()
    assert [o["source_id"] for o in d["occurrences"]] == ["webcam:s2"]
    assert d["occurrence_total"] == 1 and d["suppressed_count"] == 1
    assert valid_alert(d)


def test_unknown_suppression_without_a_surviving_alert_creates_a_new_diagnostic_instead_of_losing_it():
    e, c = engine()
    s1, s2 = _sessions()
    e.observe_source(s1, [], {"Z1": _st(1)})
    e.observe_source(s1, [], {"Z1": _st(20_000_000)})
    (d1,) = e.alerts.values()
    e._drop(d1)                                              # e.g. capacity eviction of the alert that carried the group
    c.advance(2)
    e.observe_source(s2, [], {"Z1": _st(1)})
    e.observe_source(s2, [], {"Z1": _st(20_000_000)})
    assert [a["source"]["source_id"] for a in e.alerts.values()] == ["webcam:s2"]


def test_occurrence_history_stays_bounded_and_the_overflow_starts_a_new_alert_instead_of_being_only_counted():
    e, c = engine()
    for i in range(MAX_OCCURRENCES + 8):
        s = src("webcam:Cam", f"webcam:s{i}", kind="webcam_session")
        e.observe_source(s, [evt(f"e{i}", "RED", False, end=5)], {})
        c.advance(0.1)
    assert len(e.alerts) == 2 and all(len(a["occurrences"]) == a["occurrence_total"] <= MAX_OCCURRENCES for a in e.alerts.values())
    assert sum(len(a["occurrences"]) for a in e.alerts.values()) == MAX_OCCURRENCES + 8
    assert all(o["source_id"].startswith("webcam:s") for a in e.alerts.values() for o in a["occurrences"])


def test_legacy_stored_occurrences_without_a_source_id_still_load_and_are_reported_as_unattributed(tmp_path):
    e, _ = engine(tmp_path)
    e.ingest_simulation("run_aaaaaaaaaaaaaaaa", [evt("s1", "RED", False, end=9)])
    (a,) = e.alerts.values()
    for o in a["occurrences"]:
        o.pop("source_id", None)                             # an alert written by a previous version
    assert valid_alert(a)
    assert all(o["source_id"] is None for o in e.public(a)["occurrences"])


@pytest.mark.parametrize("bad", ["", 5, ["x"], "x" * 121])
def test_occurrence_source_id_is_strictly_validated(bad):
    e, _ = engine()
    rec = good_record(e)
    rec["occurrences"][0]["source_id"] = bad
    assert not valid_alert(rec)


