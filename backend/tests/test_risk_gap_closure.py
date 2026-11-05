"""MG-RISK-028 (cycle-28 review): an episode whose close hold runs out INSIDE an unobserved silence ends at the hold boundary, not at the returning sample.
Expectations are authored from MG-RISK-002 (step 6: close_hold uniform `>=` rule; H-6: a silence is UNKNOWN) and from the review's counterexample, not from engine output.
Times are integer microseconds; max_sample_gap_us = 100 ms, debounce = close_hold = 500 ms, dwell = 1500 ms (defaults). Synthetic samples only."""
import pytest

from mineguard.risk.kernel import GREEN, RED, UNKNOWN, YELLOW, F, PolicyError, Sample, T, ZoneEngine, evaluate

STEP = 100_000


def both(t, i):
    return Sample(t_us=t, index=i, persons={"P1": (T, None)}, vehicle=(T, None))


def alone(t, i):
    return Sample(t_us=t, index=i, persons={"P1": (T, None)}, vehicle=(F, None))


def series(make, upto_us):
    return [make(k * STEP, k) for k in range(upto_us // STEP + 1)]


def red_then(next_t, end=None):
    s = series(both, 500_000) + [both(next_t, 6)]
    return evaluate("Z", s, end if end is not None else next_t + STEP)


def tl(r):
    """Timeline merged by state (the engine also splits on cause changes)."""
    out = []
    for i in r["timeline"]:
        if out and out[-1][0] == i["state"] and out[-1][2] == i["start_us"]:
            out[-1] = (i["state"], out[-1][1], i["end_us"])
        else:
            out.append((i["state"], i["start_us"], i["end_us"]))
    return out


def test_review_counterexample_red_closes_at_hold_boundary_and_the_gap_is_unknown():
    r = red_then(3_000_000, end=3_100_000)
    red = [e for e in r["events"] if e["severity"] == RED]
    assert [(e["start_us"], e["end_us"], e["closed_reason"], e["right_censored"]) for e in red[:1]] == [(500_000, 1_100_000, "inputs_unknown", False)]
    assert red[0]["end_us"] >= red[0]["start_us"]
    t = tl(r)
    assert (RED, 500_000, 1_100_000) in t and (UNKNOWN, 1_100_000, 3_000_000) in t
    assert not any(s == RED and b > 1_100_000 and a < 3_000_000 for s, a, b in t)          # no RED over unobserved time
    assert r["coverage_us"]["partition_check"] and r["unknown_by_cause_us"]["source_gap"] >= 1_900_000


def test_returning_sample_never_extends_an_unobserved_episode_but_may_open_a_new_candidate():
    r = red_then(3_000_000, end=4_000_000)
    ids = [e["event_id"] for e in r["events"]]
    assert len(ids) == len(set(ids))                                 # no duplicate closure / identity
    first = r["events"][0]
    assert first["end_us"] == 1_100_000
    # the sample at 3.0 s only starts a NEW candidate; it is not part of the first episode
    assert all(e["start_us"] != 500_000 or e is first for e in r["events"])


def test_no_subsequent_observation_ever_arrives():
    e = ZoneEngine("Z")
    for s in series(both, 500_000):
        e.push(s)
    e.finish(3_000_000)
    r = e.result()
    ev = r["events"][0]
    assert (ev["end_us"], ev["closed_reason"], ev["right_censored"]) == (1_100_000, "inputs_unknown", False)
    assert (UNKNOWN, 1_100_000, 3_000_000) in tl(r)
    assert r["coverage_us"]["partition_check"] and r["coverage_us"]["total"] == 3_000_000


def test_stream_end_inside_the_hold_is_right_censored_and_not_closed_early():
    e = ZoneEngine("Z")
    for s in series(both, 500_000):
        e.push(s)
    e.finish(900_000)                                                # 600 ms pseudo-sample + 500 ms hold = 1.1 s: not yet expired
    ev = e.result()["events"][0]
    assert (ev["end_us"], ev["closed_reason"], ev["right_censored"]) == (900_000, "end_of_stream", True)


def test_stream_end_exactly_at_the_boundary_closes_by_rule_not_by_censoring():
    e = ZoneEngine("Z")
    for s in series(both, 500_000):
        e.push(s)
    e.finish(1_100_000)
    ev = e.result()["events"][0]
    assert (ev["end_us"], ev["closed_reason"], ev["right_censored"]) == (1_100_000, "inputs_unknown", False)


def test_a_trailing_silence_is_unknown_whatever_the_episode_state():
    """MG-RISK-029: the review's counterexample (empty scene, samples at 0, 100 ms, 200 ms, stream end at 2 s) was required to be GREEN to the end."""
    e = ZoneEngine("Z")
    for k in range(3):
        e.push(Sample(t_us=k * STEP, index=k))
    e.finish(2_000_000)
    r = e.result()
    assert tl(r) == [(GREEN, 0, 300_000), (UNKNOWN, 300_000, 2_000_000)] and r["coverage_us"]["partition_check"] and r["coverage_us"]["total"] == 2_000_000
    assert r["unknown_by_cause_us"]["source_gap"] == 1_700_000


def test_a_pending_candidate_does_not_hide_a_trailing_silence():
    e = ZoneEngine("Z")
    for s in series(both, 300_000):                                   # person + vehicle: RED candidate pending (needs 500 ms)
        e.push(s)
    e.finish(3_000_000)
    r = e.result()
    assert r["events"] == [] and tl(r)[-1] == (UNKNOWN, 400_000, 3_000_000) and r["suppressed_candidates"]
    assert r["coverage_us"]["partition_check"]


def test_a_stream_that_ends_within_the_sample_gap_is_unchanged():
    e = ZoneEngine("Z")
    for k in range(3):
        e.push(Sample(t_us=k * STEP, index=k))
    e.finish(300_000)                                                 # end - last sample = 100 ms = max_sample_gap_us: not a silence
    assert tl(e.result()) == [(GREEN, 0, 300_000)]


def test_boundary_exactly_reached_at_a_sample_closes_there():
    r = red_then(1_100_000, end=1_200_000)
    ev = r["events"][0]
    assert (ev["end_us"], ev["closed_reason"]) == (1_100_000, "inputs_unknown")


def test_sample_just_before_the_boundary_is_an_observation_that_absorbs_the_silence():
    r = red_then(1_099_999, end=1_200_000)
    first = r["events"][0]
    assert first["absorbed"] and first["absorbed"][0]["to_us"] == 1_099_999   # existing rule: absorption only strictly before the boundary


def test_sample_just_after_the_boundary_does_not_revive_the_episode():
    r = red_then(1_100_001, end=1_200_000)
    first = r["events"][0]
    assert first["end_us"] == 1_100_000 and not first["absorbed"]
    assert (UNKNOWN, 1_100_000, 1_100_001) in tl(r)


def test_yellow_episode_has_the_same_boundary():
    s = series(alone, 2_000_000) + [alone(5_000_000, 21)]
    r = evaluate("Z", s, 5_100_000)
    y = [e for e in r["events"] if e["severity"] == YELLOW][0]
    assert (y["start_us"], y["end_us"], y["closed_reason"]) == (1_500_000, 2_600_000, "inputs_unknown")
    assert (UNKNOWN, 2_600_000, 5_000_000) in tl(r)


def test_streaming_equals_offline_replay_and_is_deterministic():
    s = series(both, 500_000) + [both(3_000_000, 6), both(3_100_000, 7)]
    a = evaluate("Z", s, 3_200_000)
    e = ZoneEngine("Z")
    for x in s:
        e.push(x)
    e.finish(3_200_000)
    assert e.result() == a == evaluate("Z", s, 3_200_000)


def test_zones_and_sources_are_independent():
    gap, steady = ZoneEngine("A"), ZoneEngine("B")
    for s in series(both, 500_000):
        gap.push(s)
        steady.push(s)
    gap.push(both(3_000_000, 6))
    for k in range(6, 31):                                           # the other engine keeps receiving samples through the same period
        steady.push(both(k * STEP, k))
    gap.finish(3_100_000)
    steady.finish(3_100_000)
    assert gap.result()["events"][0]["end_us"] == 1_100_000
    assert steady.result()["events"][0]["end_us"] == 3_100_000 and steady.result()["events"][0]["right_censored"]


def test_a_late_or_replayed_sample_is_refused_and_leaves_the_closed_episode_alone():
    e = ZoneEngine("Z")
    for s in series(both, 500_000) + [both(3_000_000, 6)]:
        e.push(s)
    n = len(e.events)
    with pytest.raises(PolicyError):
        e.push(both(2_000_000, 7))
    assert len(e.events) == n and e.events[0]["end_us"] == 1_100_000


def test_a_running_episode_with_samples_every_100ms_is_never_closed_by_the_new_rule():
    r = evaluate("Z", series(both, 3_000_000), 3_100_000)
    assert [e["closed_reason"] for e in r["events"]] == ["end_of_stream"] and r["events"][0]["end_us"] == 3_100_000
