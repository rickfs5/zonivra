"""Cycle-24 remediation vectors for MG-STORE-024 (OAF-VERTICAL-01). Synthetic only. Each vector asserts the CORRECT behaviour; it failed on 147f139 (see the fail-before evidence)."""
import math

import pytest

from mineguard import monitor
from mineguard.alerts import valid_alert
from test_vertical2_alerts import engine, evt, good_record
from test_vertical2_dashboards import planted_good


# ---------------------------------------------------------------- MG-STORE-024
@pytest.mark.parametrize("field,value", [
    ("reopened_count", {"attacker_extra": {"token": "secret"}}), ("reopened_count", -1), ("reopened_count", True), ("reopened_count", 1.5), ("reopened_count", "3"),
    ("reopened_count", 10**10), ("pinned_open", {"x": 1}), ("pinned_open", 1), ("pinned_open", "yes"), ("loaded_from_store", {"x": 1}), ("loaded_from_store", 1), ("loaded_from_store", None)])
def test_every_optional_alert_field_is_an_exact_bounded_primitive(field, value):
    e, _ = engine()
    rec = good_record(e)
    rec[field] = value
    assert not valid_alert(rec)


def test_valid_optional_values_are_accepted_and_nothing_else_leaves_through_public():
    e, _ = engine()
    rec = good_record(e)
    rec.update(reopened_count=3, pinned_open=True, loaded_from_store=False)
    assert valid_alert(rec)
    out = e.public(rec)
    assert out["reopened_count"] == 3 and out["pinned_open"] is True and out["loaded_from_store"] is False


def test_public_never_echoes_a_planted_object_even_if_validation_is_bypassed():
    e, _ = engine()
    rec = good_record(e)
    rec["reopened_count"] = {"attacker_extra": {"token": "secret"}}
    rec["pinned_open"] = {"x": 1}
    out = e.public(rec)
    assert "secret" not in repr(out) and out.get("reopened_count", 0) == 0 and out.get("pinned_open", False) is False


def test_restored_records_are_excluded_from_every_delivery_aggregate(tmp_path):
    e, _ = engine(tmp_path)
    e.ingest_simulation("run_aaaaaaaaaaaaaaaa", [evt("s1", "RED", False, end=9)])
    e.flush()
    e2, _ = engine(tmp_path)
    s = e2.summary()
    assert s["loaded_from_disk_unverified"] == 1 and s["by_delivery_state"] == {} and s["total"] == 1


@pytest.mark.parametrize("edit", [
    lambda g: g["config"].update(sample_fps=float("nan")), lambda g: g["config"].update(sample_fps=float("inf")), lambda g: g["config"].update(sample_fps=0.0),
    lambda g: g["config"].update(sample_fps=11.0), lambda g: g["config"].update(sample_fps=-1), lambda g: g["config"].update(speed=10.0 ** 300), lambda g: g["config"].update(speed=3.0),
    lambda g: g["config"].update(speed=float("nan")), lambda g: g["latency_ms"].update(p50=-5), lambda g: g["latency_ms"].update(max=-1), lambda g: g["queue_wait_ms"].update(p95=-1),
    lambda g: g.update(counters={"k" * 5000: 1}), lambda g: g.update(counters={"k" * 41: 1}), lambda g: g["counters"].update({"a": -1})])
def test_feed_summary_numbers_must_be_finite_in_range_nonnegative_and_keys_bounded(edit):
    g = planted_good()
    edit(g)
    assert not monitor.valid_feedrun(g)


def test_a_normal_feed_summary_is_still_valid_and_projects_finite_numbers():
    g = planted_good()
    assert monitor.valid_feedrun(g)
    p = monitor.project_feedrun(g)
    assert math.isfinite(p["config"]["sample_fps"]) and p["config"]["speed"] == 1.0
