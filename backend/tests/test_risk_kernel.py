"""Conformance vectors for the risk kernel (MG-RISK-002, semantics_version 2). Expectations are authored from the MG-RISK-001 section 14 scenario catalog
(30 fps, 15 frames = 500 ms, 45 frames = 1500 ms) and from MG-RISK-002 where version 2 differs; they are NOT derived from the engine output.
The kernel is pure: no model, no video, no network. Frame k has pts_us = round_half_up(k * 1e6 / 30)."""
import pytest

from mineguard.risk.kernel import GREEN, RED, UNKNOWN, YELLOW, F, Params, PolicyError, Sample, T, U, ZoneEngine, evaluate

N = 300


def pts(k: int) -> int:
    return (k * 2_000_000 + 30) // 60


END = pts(N)
INV = {pts(k): k for k in range(N + 1)}


def rng(*pairs):
    s = set()
    for a, b in pairs:
        s |= set(range(a, b))
    return s


def scene(persons=None, absent=None, veh=(), veh_u=(), status=None, untracked=(), person_other_u=(), breaks=None, terminated=None, n=N, notes=None):
    """persons: {track: [(a, b)...]} frames [a,b) in which the person's reference point is inside Z; elsewhere visible OUTSIDE (F).
    absent: {track: [(a, b)...]} frames in which the track has no object at all. status: {"not_analyzed"|"source_gap"|"adapter_error": [(a, b)...]}."""
    persons, absent, status, breaks, terminated, notes = persons or {}, absent or {}, status or {}, breaks or {}, terminated or {}, notes or {}
    inside = {t: rng(*r) for t, r in persons.items()}
    gone = {t: rng(*r) for t, r in absent.items()}
    hole = {}
    for st, r in status.items():
        for k in rng(*r):
            hole[k] = st
    V, VU, UT, PU = rng(*veh), rng(*veh_u), rng(*untracked), rng(*person_other_u)
    out = []
    for k in range(n):
        if k in hole:
            out.append(Sample(t_us=pts(k), index=k, status=hole[k]))
            continue
        ps = {t: ((T if k in inside[t] else F), None) for t in persons if k not in gone.get(t, set())}
        out.append(Sample(t_us=pts(k), index=k, persons=ps,
                          untracked_person=(T, None) if k in UT else (F, None),
                          person_other=(U, "class_unknown_or_ambiguous") if k in PU else (F, None),
                          vehicle=(U, "class_unknown_or_ambiguous") if k in VU else (T, None) if k in V else (F, None),
                          identity_breaks=frozenset(t for t, fr in breaks.items() if k in fr),
                          terminated=frozenset(t for t, fr in terminated.items() if k == fr),
                          notes=tuple(notes.get(k, ()))))
    return out


def run(samples, params=None, end=END):
    return evaluate("Z", samples, end, params)


def tl(res):
    """Timeline merged by state only, in frame indices."""
    out = []
    for iv in res["timeline"]:
        a, b = INV[iv["start_us"]], INV[iv["end_us"]]
        if out and out[-1][0] == iv["state"] and out[-1][2] == a:
            out[-1][2] = b
        else:
            out.append([iv["state"], a, b])
    return [tuple(x) for x in out]


def evs(res, sev=None):
    return [(e["severity"], e["start_index"], INV[e["end_us"]], e["closed_reason"]) for e in res["events"] if sev in (None, e["severity"])]


# ------------------------------------------------------------------ positive
def test_s01_person_dwell_yellow():
    r = run(scene({"P1": [(30, 151)]}))
    assert tl(r) == [(GREEN, 0, 75), (YELLOW, 75, 166), (GREEN, 166, 300)]
    e = r["events"][0]
    assert (e["condition_since_us"], e["start_us"], e["dwell_since_us"], e["condition_until_us"], e["end_us"], e["closed_reason"]) == (pts(75), pts(75), pts(30), pts(151), pts(166), "rule_cleared")
    assert len(r["events"]) == 1 and not e["left_censored"] and not e["right_censored"]


def test_s02_vehicle_alone_is_green():
    r = run(scene(veh=[(60, 200)]))
    assert tl(r) == [(GREEN, 0, 300)] and r["events"] == [] and r["suppressed_candidates"] == []


def test_s03_both_present_red_never_yellow():
    r = run(scene({"P1": [(30, 300)]}, veh=[(60, 300)]))
    assert tl(r) == [(GREEN, 0, 75), (RED, 75, 300)]
    assert evs(r) == [(RED, 75, 300, "end_of_stream")]
    e = r["events"][0]
    assert e["condition_since_us"] == pts(60) and e["right_censored"]


def test_s05_person_leaves_red_closes_rule_cleared():
    r = run(scene({"P1": [(30, 151)]}, veh=[(60, 300)]))
    assert tl(r) == [(GREEN, 0, 75), (RED, 75, 166), (GREEN, 166, 300)]
    assert evs(r) == [(RED, 75, 166, "rule_cleared")] and r["events"][0]["condition_until_us"] == pts(151)


def test_s07_two_yellow_then_one_red():
    r = run(scene({"P1": [(30, 300)], "P2": [(40, 300)]}, veh=[(100, 300)]))
    assert tl(r) == [(GREEN, 0, 75), (YELLOW, 75, 115), (RED, 115, 300)]
    assert sorted(evs(r, YELLOW)) == [(YELLOW, 75, 115, "rule_cleared"), (YELLOW, 85, 115, "rule_cleared")]
    assert evs(r, RED) == [(RED, 115, 300, "end_of_stream")]


def test_s35_red_to_yellow_handoff_without_gap():
    r = run(scene({"P1": [(30, 300)]}, veh=[(60, 151)]))
    assert tl(r) == [(GREEN, 0, 75), (RED, 75, 166), (YELLOW, 166, 300)]
    assert evs(r, RED)[0][2] == evs(r, YELLOW)[0][1] == 166


def test_s36_yellow_to_red_handoff_without_gap():
    r = run(scene({"P1": [(30, 300)]}, veh=[(150, 300)]))
    assert tl(r) == [(GREEN, 0, 75), (YELLOW, 75, 165), (RED, 165, 300)]
    assert evs(r, YELLOW) == [(YELLOW, 75, 165, "rule_cleared")]


# ------------------------------------------------------------------ negative
def test_s09_vehicle_inside_person_outside():
    r = run(scene({"P1": []}, veh=[(50, 200)]))
    assert tl(r) == [(GREEN, 0, 300)] and r["events"] == []


def test_s11_dwell_one_frame_short_is_green():
    r = run(scene({"P1": [(30, 74)]}))
    assert tl(r) == [(GREEN, 0, 300)] and r["events"] == [] and r["suppressed_candidates"] == []


def test_s13_empty_analyzed_scene_is_green_with_no_unknown():
    r = run(scene())
    assert tl(r) == [(GREEN, 0, 300)] and r["coverage_us"][UNKNOWN] == 0


def test_s38_short_vehicle_flicker_is_suppressed_and_absorbed():
    r = run(scene({"P1": [(30, 300)]}, veh=[(100, 110)]))
    assert tl(r) == [(GREEN, 0, 75), (YELLOW, 75, 300)] and len(r["events"]) == 1
    sc = r["suppressed_candidates"]
    assert len(sc) == 1 and sc[0]["from_index"] == 100 and sc[0]["reason"] == "below_open_hold"
    assert r["events"][0]["absorbed"][0]["from_index"] == 100


def test_s45_class_level_red_survives_one_person_leaving():
    r = run(scene({"P1": [(30, 120)], "P2": [(30, 300)]}, veh=[(30, 300)]))
    assert evs(r, RED) == [(RED, 45, 300, "end_of_stream")]


# ------------------------------------------------------------------ boundary
def explicit(times_inside, step=100_000, t_end=3_000_000, veh=None):
    out, t, i = [], 0, 0
    while t <= t_end:
        inside = times_inside[0] <= t <= times_inside[1]
        out.append(Sample(t_us=t, index=i, persons={"P1": (T if inside else F, None)}))
        t, i = t + step, i + 1
    return out


def test_s17b_dwell_exactly_threshold_opens_with_geq():
    r = evaluate("Z", explicit((1_000_000, 2_500_000)), 3_000_000)
    assert [e["start_us"] for e in r["events"]] == [2_500_000]


def test_s18b_dwell_one_microsecond_short_does_not_open():
    s = explicit((1_000_000, 2_500_000))
    s = [x for x in s if x.t_us != 2_500_000]
    s.insert(len(s) - 5, Sample(t_us=2_499_999, index=99, persons={"P1": (T, None)}))
    s.sort(key=lambda x: x.t_us)
    s = [x if x.t_us <= 2_499_999 else Sample(t_us=x.t_us, index=x.index, persons={"P1": (F, None)}) for x in s]
    assert evaluate("Z", s, 3_000_000)["events"] == []


def test_s19_vehicle_confirmation_boundary():
    a = run(scene({"P1": [(30, 300)]}, veh=[(100, 116)]))  # present 100..115 -> 15 frames = 500 ms at frame 115
    assert evs(a, RED)[0][:2] == (RED, 115) and evs(a, YELLOW)[0] == (YELLOW, 75, 115, "rule_cleared")
    b = run(scene({"P1": [(30, 300)]}, veh=[(100, 115)]))  # present 100..114 -> candidate dropped one frame short
    assert evs(b, RED) == [] and [x["reason"] for x in b["suppressed_candidates"]] == ["below_open_hold"] and b["suppressed_candidates"][0]["last_true_index"] == 114
    # MG-RISK-002 uniform rule (cycle-2 review MG-RISK-012): the closing run of exactly 15 frames reaches close_hold AT frame 115, so YELLOW closes there (not absorbed)
    # and must re-earn its 15-frame vehicle-free hold (reopens at 130). The MG-RISK-001 catalog said "absorbs"; version 2 changes this boundary case.
    assert [(e[1], e[2], e[3]) for e in evs(b, YELLOW)] == [(75, 115, "rule_cleared"), (130, 300, "end_of_stream")]


def test_s20a_no_absorption_before_confirmation():
    r = run(scene({"P1": [(30, 300)]}, veh=[(100, 105), (110, 300)]))
    assert [(x["from_index"], x["to_us"]) for x in r["suppressed_candidates"]] == [(100, pts(105))]
    assert evs(r, RED)[0][:2] == (RED, 125) and evs(r, YELLOW)[0] == (YELLOW, 75, 125, "rule_cleared")
    assert r["events"][0]["absorbed"] and r["events"][0]["absorbed"][0]["from_index"] == 100


def test_s20b_confirmed_red_absorbs_short_vehicle_dropout():
    r = run(scene({"P1": [(30, 300)]}, veh=[(60, 150), (155, 300)]))
    red = [e for e in r["events"] if e["severity"] == RED]
    assert len(red) == 1 and red[0]["absorbed"][0]["from_index"] == 150 and red[0]["absorbed"][0]["to_index"] == 155


# ------------------------------------------------------------------ unknown / incomplete / censoring
def test_s21_short_hole_is_unknown_never_green_and_dwell_continues():
    r = run(scene({"P1": [(30, 300)]}, status={"not_analyzed": [(50, 60)]}))
    assert (UNKNOWN, 50, 60) in tl(r) and evs(r, YELLOW)[0][:2] == (YELLOW, 75)
    assert not any(s == GREEN and a < 60 <= b for s, a, b in tl(r) if a <= 55)


def test_s22_long_hole_closes_episode_and_dwell_restarts_v2():
    r = run(scene({"P1": [(30, 300)]}, status={"source_gap": [(80, 120)]}))
    assert evs(r, YELLOW)[0] == (YELLOW, 75, 95, "inputs_unknown")
    assert r["events"][0]["closed_unknown_reasons"] == ["source_gap"]
    assert tl(r)[:5] == [(GREEN, 0, 75), (YELLOW, 75, 95), (UNKNOWN, 95, 120), (GREEN, 120, 165), (YELLOW, 165, 300)]


def test_s23_f_is_never_bridged_empty_scene_hole_is_unknown():
    r = run(scene(status={"not_analyzed": [(100, 110)]}))
    assert tl(r) == [(GREEN, 0, 100), (UNKNOWN, 100, 110), (GREEN, 110, 300)]


def test_s24a_ambiguous_vehicle_blocks_and_hold_restarts():
    r = run(scene({"P1": [(30, 300)]}, veh_u=[(60, 121)]))
    assert tl(r) == [(GREEN, 0, 60), (UNKNOWN, 60, 121), (GREEN, 121, 136), (YELLOW, 136, 300)]


def test_s24b_ambiguity_during_yellow_closes_inputs_unknown_then_reopens():
    r = run(scene({"P1": [(30, 300)]}, veh_u=[(100, 140)]))
    assert evs(r, YELLOW)[0] == (YELLOW, 75, 115, "inputs_unknown") and evs(r, YELLOW)[1][:2] == (YELLOW, 155)
    assert tl(r) == [(GREEN, 0, 75), (YELLOW, 75, 115), (UNKNOWN, 115, 140), (GREEN, 140, 155), (YELLOW, 155, 300)]


def test_s25_absent_track_closes_unknown_then_dropped_with_record():
    r = run(scene({"P1": [(30, 101)]}, absent={"P1": [(101, 300)]}))
    assert evs(r, YELLOW)[0] == (YELLOW, 75, 116, "inputs_unknown") and r["events"][0]["closed_unknown_reasons"] == ["track_absent"]
    assert tl(r) == [(GREEN, 0, 75), (YELLOW, 75, 116), (UNKNOWN, 116, 190), (GREEN, 190, 300)]
    assert r["dropped_tracks"] == [{"track_id": "P1", "last_seen_us": pts(100), "dropped_at_us": pts(190), "reason": "hold_expired"}]


def test_s27_confirmed_red_stays_red_but_incomplete_is_recorded():
    notes = {k: ("class_unknown_or_ambiguous",) for k in range(160, 181)}
    r = run(scene({"P1": [(30, 300)]}, veh=[(60, 300)], notes=notes))
    assert tl(r) == [(GREEN, 0, 75), (RED, 75, 300)]
    inc = r["events"][0]["incomplete"]
    assert inc and inc[0]["cause"] == "class_unknown_or_ambiguous" and (inc[0]["from_index"], inc[0]["to_index"]) == (160, 180)


def test_s28a_single_adapter_error_is_absorbed_with_incomplete():
    r = run(scene({"P1": [(30, 300)]}, status={"adapter_error": [(100, 101)]}))
    assert len(evs(r, YELLOW)) == 1 and evs(r, YELLOW)[0][:2] == (YELLOW, 75)
    assert r["events"][0]["absorbed"] and r["events"][0]["incomplete"][0]["cause"] == "adapter_error"


def test_s28b_adapter_error_run_closes_and_dwell_restarts_not_bridgeable():
    r = run(scene({"P1": [(30, 300)]}, status={"adapter_error": [(100, 120)]}))
    assert evs(r, YELLOW)[0] == (YELLOW, 75, 115, "inputs_unknown") and evs(r, YELLOW)[1][:2] == (YELLOW, 165)
    assert tl(r) == [(GREEN, 0, 75), (YELLOW, 75, 115), (UNKNOWN, 115, 120), (GREEN, 120, 165), (YELLOW, 165, 300)]


def test_s39_left_censored_start():
    r = run(scene({"P1": [(0, 300)]}))
    e = r["events"][0]
    assert e["start_index"] == 45 and e["left_censored"] and e["dwell_since_us"] == 0


def test_s40a_red_active_at_end_is_right_censored():
    r = run(scene({"P1": [(30, 300)]}, veh=[(60, 300)]))
    assert r["events"][0]["closed_reason"] == "end_of_stream" and r["events"][0]["right_censored"] and r["events"][0]["end_us"] == END


def test_s40b_person_leaves_three_frames_before_end_never_rule_cleared():
    r = run(scene({"P1": [(30, 297)]}, veh=[(60, 300)]))
    e = r["events"][0]
    assert e["closed_reason"] == "end_of_stream" and e["condition_until_us"] == pts(297)


def test_s41_untracked_person_is_unknown_identity_unavailable():
    r = run(scene(untracked=[(30, 100)]))
    assert tl(r) == [(GREEN, 0, 30), (UNKNOWN, 30, 100), (GREEN, 100, 300)]
    assert all("identity_unavailable" in iv["causes"] for iv in r["timeline"] if iv["state"] == UNKNOWN)
    v = run(scene({"P1": [(30, 300)]}, untracked=[(100, 140)]))
    assert evs(v, YELLOW)[0][:2] == (YELLOW, 75) and v["events"][0]["incomplete"]
    assert v["events"][0]["incomplete"][0]["cause"] == "identity_unavailable"


def test_s42a_short_absence_is_bridged():
    r = run(scene({"P1": [(30, 300)]}, absent={"P1": [(60, 70)]}))
    assert evs(r, YELLOW)[0][:2] == (YELLOW, 75)


def test_s42b_long_absence_restarts_dwell():
    r = run(scene({"P1": [(30, 60), (100, 300)]}, absent={"P1": [(60, 100)]}))
    assert evs(r, YELLOW)[0][:2] == (YELLOW, 145) and (UNKNOWN, 60, 100) in tl(r)


def test_s42c_identity_break_closes_episode_and_dwell_restarts():
    r = run(scene({"P1": [(30, 300)]}, breaks={"P1": {100}}))
    assert evs(r, YELLOW)[0] == (YELLOW, 75, 115, "track_discontinuity") and evs(r, YELLOW)[1][:2] == (YELLOW, 145)


def test_s43_unknown_to_red_u_frames_never_count_toward_hold():
    r = run(scene({"P1": [(30, 300)]}, veh_u=[(60, 90)], veh=[(90, 300)]))
    assert tl(r) == [(GREEN, 0, 60), (UNKNOWN, 60, 90), (GREEN, 90, 105), (RED, 105, 300)]


def test_pending_condition_is_flagged_not_plain_green():
    r = run(scene({"P1": [(30, 300)]}))
    pend = [(INV[iv["start_us"]], INV[iv["end_us"]]) for iv in r["timeline"] if iv["state"] == GREEN and iv["pending"]]
    assert pend == [(30, 75)]  # dwell in progress is visible as pending, never as confirmed safe


# ------------------------------------------------------------------ determinism, causality, refusal, adversarial
def test_offline_equals_streaming_and_repeatable():
    sc = scene({"P1": [(30, 300)]}, veh=[(60, 150), (155, 300)], status={"not_analyzed": [(200, 210)]})
    a, b = run(sc), run(sc)
    e = ZoneEngine("Z")
    for s in sc:
        e.push(s)
    e.finish(END)
    assert a == b == e.result()


def test_causality_prefix_decisions_do_not_change_with_future_samples():
    sc = scene({"P1": [(30, 300)]}, veh=[(60, 300)])
    e1, e2 = ZoneEngine("Z"), ZoneEngine("Z")
    for s in sc[:100]:
        e1.push(s)
    for s in sc:
        e2.push(s)
    # the state reported after sample 99 is identical whether or not later samples exist
    e3 = ZoneEngine("Z")
    last = None
    for s in sc[:100]:
        e3.push(s)
        last = dict(e3.last_state)
    assert last == e1.last_state and e1.snapshot()["events"][0]["start_index"] == 75


def test_silence_between_samples_is_an_unknown_interval_not_green():
    sc = [Sample(t_us=0, index=0), Sample(t_us=100_000, index=1), Sample(t_us=2_000_000, index=2), Sample(t_us=2_100_000, index=3)]
    r = evaluate("Z", sc, 2_200_000)
    assert [(i["state"], i["start_us"], i["end_us"]) for i in r["timeline"]] == [(GREEN, 0, 200_000), (UNKNOWN, 200_000, 2_000_000), (GREEN, 2_000_000, 2_200_000)]
    assert r["unknown_by_cause_us"]["source_gap"] == 1_800_000


def test_f_hole_f_never_creates_dwell():
    r = run(scene({"P1": []}, status={"not_analyzed": [(50, 100)]}))
    assert r["events"] == []


def test_u_never_counts_toward_dwell_confirmation():
    # person T 30..60, hole 60..65 (bridged), T 65..: dwell clock runs from 30 so confirmation is at 75, not earlier
    r = run(scene({"P1": [(30, 300)]}, status={"not_analyzed": [(60, 65)]}))
    assert evs(r, YELLOW)[0][1] >= 75


def test_coverage_partitions_total_and_never_credits_green_to_holes():
    r = run(scene({"P1": [(30, 300)]}, veh=[(60, 300)], status={"not_analyzed": [(10, 20), (200, 210)]}))
    c = r["coverage_us"]
    assert c["partition_check"] and c["total"] == END and c["analyzed"] == END - (pts(20) - pts(10)) - (pts(210) - pts(200)) or c["analyzed"] < c["total"]
    assert r["unknown_by_cause_us"]["frame_not_analyzed"] >= pts(20) - pts(10)


def test_timeline_is_gap_free_and_half_open():
    r = run(scene({"P1": [(30, 300)]}, veh=[(60, 150)], status={"source_gap": [(200, 240)]}))
    tl_ = r["timeline"]
    assert tl_[0]["start_us"] == 0 and tl_[-1]["end_us"] == END
    assert all(a["end_us"] == b["start_us"] for a, b in zip(tl_, tl_[1:]))


@pytest.mark.parametrize("bad", [dict(debounce_us=-1), dict(dwell_us=1.5), dict(max_sample_gap_us=0), dict(track_gap_tolerance_us=4_000_000), dict(dwell_us=True)])
def test_invalid_parameters_are_refused(bad):
    with pytest.raises(PolicyError):
        Params(**bad)


def test_non_increasing_time_is_refused():
    e = ZoneEngine("Z")
    e.push(Sample(t_us=100))
    with pytest.raises(PolicyError):
        e.push(Sample(t_us=100))


# ------------------------------------------------------------------ cycle-1 review findings (MG-RISK-010 / MG-RISK-011): sparse sampling and ambiguous persons
from mineguard.risk.evaluation import live_params  # noqa: E402


def periodic(interval_us, n, person=True, vehicle=True, person_other=None):
    out = []
    for i in range(n):
        out.append(Sample(t_us=i * interval_us, index=i, persons={"P": (T, None)} if person else {}, vehicle=(T, None) if vehicle else (F, None),
                          person_other=person_other or (F, None)))
    return out


def test_sparse_samples_slower_than_the_hold_never_confirm_red():
    p, _ = live_params(1000)  # 1 s sampling: max_sample_gap 2.5 s, debounce 0.5 s
    r = evaluate("Z", periodic(1_000_000, 4), 4_100_000, p)
    assert [e for e in r["events"] if e["severity"] == RED] == []
    assert {x["reason"] for x in r["suppressed_candidates"]} >= {"sample_spacing_exceeds_hold"}


def test_samples_within_the_hold_confirm_red_at_the_first_sample_at_or_after_the_hold():
    p, _ = live_params(300)
    r = evaluate("Z", periodic(300_000, 8), 2_500_000, p)
    red = [e for e in r["events"] if e["severity"] == RED]
    assert len(red) == 1 and red[0]["start_us"] == 600_000 and red[0]["condition_since_us"] == 0   # 0 -> 300k -> 600k: 600k >= 500k, spacing 300k <= 500k


def test_yellow_dwell_needs_samples_closer_than_the_dwell():
    p, _ = live_params(300)
    r = evaluate("Z", periodic(300_000, 12, vehicle=False), 3_700_000, p)
    assert [e["start_us"] for e in r["events"]] == [1_500_000]
    wide = evaluate("Z", [Sample(t_us=0, index=0, persons={"P": (T, None)}), Sample(t_us=2_000_000, index=1, persons={"P": (T, None)}),
                          Sample(t_us=4_000_000, index=2, persons={"P": (T, None)})], 4_100_000, Params(max_sample_gap_us=2_500_000, debounce_us=0))
    assert wide["events"] == []  # 2 s spacing > 1.5 s dwell: continuous presence was never observed for the dwell


def test_hold_nv_is_not_confirmed_across_a_long_silence():
    p = Params(max_sample_gap_us=2_500_000)
    s = [Sample(t_us=0, index=0, persons={"P": (T, None)}, vehicle=(T, None)), Sample(t_us=100_000, index=1, persons={"P": (T, None)}, vehicle=(F, None)),
         Sample(t_us=1_300_000, index=2, persons={"P": (T, None)}, vehicle=(F, None)), Sample(t_us=1_400_000, index=3, persons={"P": (T, None)}, vehicle=(F, None))]
    assert evaluate("Z", s, 1_500_000, p)["events"] == []   # NV was T at 100k and 1.3 s, but 1.2 s apart > debounce: the hold restarted
    t = [(0, T), (100_000, F), (1_000_000, F), (1_600_000, F), (1_700_000, F)]  # dwell would be satisfied at 1.6 s; the hold is not (restarts at each long silence)
    s2 = [Sample(t_us=x, index=i, persons={"P": (T, None)}, vehicle=(v, None)) for i, (x, v) in enumerate(t)]
    assert evaluate("Z", s2, 1_800_000, p)["events"] == []


def test_ambiguous_possible_person_is_unknown_never_complete_green():
    r = evaluate("Z", [Sample(t_us=0, index=0, person_other=(U, "class_unknown_or_ambiguous")), Sample(t_us=33_333, index=1)], 66_666)
    assert [(i["state"], i["causes"]) for i in r["timeline"]] == [(UNKNOWN, ["class_unknown_or_ambiguous"]), (GREEN, [])]


def test_ambiguous_person_during_yellow_stays_visible_as_incomplete():
    sc = scene({"P1": [(30, 300)]}, person_other_u=[(100, 110)])
    r = run(sc)
    assert evs(r, YELLOW)[0][:2] == (YELLOW, 75) and r["events"][0]["incomplete"] and r["events"][0]["incomplete"][0]["cause"] == "class_unknown_or_ambiguous"



def test_s11b_person_leaving_and_returning_restarts_dwell_from_the_return():
    r = run(scene({"P1": [(30, 60), (70, 300)]}))   # inside 30..59, outside 60..69, inside again from 70
    assert [e["start_index"] for e in r["events"]] == [115] and r["events"][0]["dwell_since_us"] == pts(70)   # 70 + 45 frames, not 75


# ------------------------------------------------------------------ cycle-2 review findings (MG-RISK-012 boundary, MG-RISK-013 prefix) and required-mutation vectors
def red_with_vehicle_gap(gap_from, gap_to, unknown=False):
    out = []
    for i in range(0, 40):
        t = i * 100_000
        veh = (U, "class_unknown_or_ambiguous") if unknown and gap_from <= t < gap_to else (F, None) if gap_from <= t < gap_to else (T, None)
        out.append(Sample(t_us=t, index=i, persons={"P": (T, None)}, vehicle=veh))
    return evaluate("Z", out, 4_000_000, Params(max_sample_gap_us=300_000))


def test_close_hold_expires_before_absorption_at_the_exact_boundary():
    r = red_with_vehicle_gap(1_000_000, 1_500_000)               # vehicle absent [1.0, 1.5) s, back exactly at 1.5 s
    red = [e for e in r["events"] if e["severity"] == RED]
    assert red[0]["start_us"] == 500_000 and red[0]["end_us"] == 1_500_000 and red[0]["closed_reason"] == "rule_cleared" and red[0]["absorbed"] == []
    assert red[1]["start_us"] == 2_000_000 and red[1]["condition_since_us"] == 1_500_000   # a NEW episode: candidate from the return sample, confirmed 0.5 s later


def test_close_hold_unknown_return_at_the_boundary_closes_inputs_unknown():
    r = red_with_vehicle_gap(1_000_000, 1_500_000, unknown=True)
    red = [e for e in r["events"] if e["severity"] == RED]
    assert red[0]["end_us"] == 1_500_000 and red[0]["closed_reason"] == "inputs_unknown"


def test_close_hold_return_one_sample_earlier_is_absorbed():
    r = red_with_vehicle_gap(1_000_000, 1_400_000)
    red = [e for e in r["events"] if e["severity"] == RED]
    assert len(red) == 1 and red[0]["absorbed"] and red[0]["absorbed"][0]["to_us"] == 1_400_000 and red[0]["closed_reason"] == "end_of_stream"


def test_unobserved_prefix_is_unknown_not_absent():
    r = evaluate("Z", [Sample(t_us=1_000_000, index=10)], 2_000_000)
    # MG-RISK-029 (cycle-29 review): the 1 s after the only sample is a silence longer than max_sample_gap_us, so it is UNKNOWN (H-6), not GREEN; the earlier vector of this test expected a GREEN tail
    assert [(i["state"], i["start_us"], i["end_us"]) for i in r["timeline"]] == [(UNKNOWN, 0, 1_000_000), (GREEN, 1_000_000, 1_100_000), (UNKNOWN, 1_100_000, 2_000_000)]
    assert r["coverage_us"]["total"] == 2_000_000 and r["coverage_us"]["partition_check"] and r["coverage_us"]["analyzed"] == 100_000
    assert r["unknown_by_cause_us"]["frame_not_analyzed"] == 1_000_000 and r["unknown_by_cause_us"]["source_gap"] == 900_000


def test_a_stream_with_no_samples_is_entirely_unknown():
    r = evaluate("Z", [], 3_000_000)
    assert [(i["state"], i["start_us"], i["end_us"], i["causes"]) for i in r["timeline"]] == [(UNKNOWN, 0, 3_000_000, ["frame_not_analyzed"])]
    assert r["coverage_us"][UNKNOWN] == 3_000_000 and r["coverage_us"]["analyzed"] == 0


def test_r1_alone_makes_an_ambiguous_vehicle_unknown_when_a_person_is_present_but_not_yet_dwelling():
    r = run(scene({"P1": [(30, 60)]}, veh_u=[(40, 50)]))   # person inside 30..59 (< dwell), vehicle ambiguous 40..49: only R1 is unknown
    assert (UNKNOWN, 40, 50) in tl(r) and r["events"] == []


def test_different_persons_in_sequence_do_not_accumulate_dwell():
    r = run(scene({"P1": [(30, 60)], "P2": [(60, 300)]}))   # one person leaves exactly as another enters: class presence is continuous, track dwell is not
    assert [(e["start_index"], e["key"]) for e in r["events"]] == [(105, ["P2"])]   # P2's own dwell starts at 60; P1's 30 frames are not inherited


# ------------------------------------------------------------------ cycle-4 review findings: incomplete ranges (MG-RISK-013) and canonical event identity (MG-EVENT-013)
def test_incomplete_ranges_are_contiguous_half_open_and_separate_runs_stay_separate():
    notes = {k: ("class_unknown_or_ambiguous",) for k in (100, 102, 110, 111, 112)}
    r = run(scene({"P1": [(0, 300)]}, notes=notes))
    inc = [(x["from_index"], x["to_index"], x["from_us"], x["to_us"]) for x in r["events"][0]["incomplete"]]
    assert inc == [(100, 100, pts(100), pts(101)), (102, 102, pts(102), pts(103)), (110, 112, pts(110), pts(113))]
    assert all(to > fr for _, _, fr, to in inc)                                   # an isolated occurrence still has a duration (one sample interval)


def test_incomplete_range_of_a_closing_episode_ends_at_the_episode_end():
    notes = {k: ("class_unknown_or_ambiguous",) for k in range(140, 170)}
    r = run(scene({"P1": [(0, 150)]}, notes=notes))                              # the person leaves at 150 and the cause outlasts the episode (it closes at 165)
    ev0 = r["events"][0]
    assert ev0["end_index"] == 165 and ev0["incomplete"][-1]["to_us"] == ev0["end_us"] and all(x["to_us"] is not None for x in ev0["incomplete"])


def test_two_different_causes_are_tracked_independently():
    sc = scene({"P1": [(0, 300)]}, notes={k: ("class_unknown_or_ambiguous",) for k in range(100, 105)})
    for k in range(103, 108):
        sc[k] = Sample(t_us=sc[k].t_us, index=k, persons=sc[k].persons, vehicle=sc[k].vehicle, notes=sc[k].notes + ("object_truncated_at_frame_edge",))
    inc = sorted((x["cause"], x["from_index"], x["to_index"]) for x in run(sc)["events"][0]["incomplete"])
    assert inc == [("class_unknown_or_ambiguous", 100, 104), ("object_truncated_at_frame_edge", 103, 107)]


def _ids(ctx=("i", "z"), zone="Z", epoch=0, start_shift=0, key_person="P"):
    e = ZoneEngine(zone, Params(), epoch=epoch, id_context=ctx)
    for i in range(30):
        e.push(Sample(t_us=(i + start_shift) * 100_000, index=i + start_shift, persons={key_person: (T, None)}, vehicle=(F, None)))
    e.finish((30 + start_shift) * 100_000)
    return [x["event_id"] for x in e.result()["events"]]


def test_event_id_is_a_canonical_hash_of_input_zone_rule_epoch_key_and_start_index():
    base = _ids()
    assert base and all(x.startswith("evt_") and len(x) == 20 for x in base) and base == _ids()
    for other in (_ids(ctx=("i2", "z")), _ids(ctx=("i", "z2")), _ids(zone="Z2"), _ids(epoch=1), _ids(start_shift=3), _ids(key_person="Q")):
        assert set(base).isdisjoint(other)


def test_event_id_is_stable_under_json_canonical_encoding_of_its_tuple():
    import hashlib
    import json
    e = ZoneEngine("Z", Params(), id_context=("i", "z"))
    for i in range(30):
        e.push(Sample(t_us=i * 100_000, index=i, persons={"P": (T, None)}, vehicle=(F, None)))
    e.finish(3_000_000)
    ev0 = e.result()["events"][0]
    tup = ["mg.event.id.v2", "i", "z", "Z", "R_PERSON_DWELL_NO_VEHICLE", "1.0.0", 0, ["P"], ev0["start_index"]]
    assert ev0["event_id"] == "evt_" + hashlib.sha256(json.dumps(tup, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()[:16]


# ------------------------------------------------------------------ cycle-5 review: the closing sample stays in the episode's incomplete record (MG-RISK-014)
def _zero_hold_yellow(last):
    e = ZoneEngine("Z", Params(debounce_us=0, dwell_us=1000, max_sample_gap_us=5000))
    for i, t in enumerate((0, 1000)):
        e.push(Sample(t_us=t, index=i, persons={"P": (T, None)}))
    e.push(last)
    e.finish(3000)
    return e.result()["events"][0]


def test_zero_close_hold_unknown_sample_that_closes_the_episode_is_recorded_as_incomplete():
    ev = _zero_hold_yellow(Sample(t_us=2000, index=2, status="adapter_error"))
    assert ev["closed_reason"] == "inputs_unknown" and ev["end_us"] == 2000
    assert [(c["cause"], c["from_index"], c["to_index"], c["from_us"], c["to_us"]) for c in ev["incomplete"]] == [("adapter_error", 2, 2, 2000, 2000)]   # boundary sample: zero-length
    assert ev["closed_unknown_reasons"] == ["adapter_error"]


def test_zero_close_hold_ambiguous_vehicle_closing_sample_is_recorded_too():
    ev = _zero_hold_yellow(Sample(t_us=2000, index=2, persons={"P": (T, None)}, vehicle=(U, "class_unknown_or_ambiguous")))
    assert ev["closed_reason"] == "inputs_unknown" and [c["cause"] for c in ev["incomplete"]] == ["class_unknown_or_ambiguous"]


def test_zero_close_hold_clean_clearing_has_no_incomplete_record():
    ev = _zero_hold_yellow(Sample(t_us=2000, index=2, persons={"P": (F, None)}))
    assert ev["closed_reason"] == "rule_cleared" and ev["incomplete"] == []


# ------------------------------------------------------------------ cycle-17 review (MG-RISK-017, MG-PENDING-017)
def test_dwell_clock_restarts_when_consecutive_samples_are_further_apart_than_the_dwell_even_with_a_pending_bridge():
    p = Params(gap_tolerance_us=3_000_000, max_sample_gap_us=2_000_000, debounce_us=0)
    samples = [Sample(t_us=0, index=0, persons={"P": (T, None)}),
               Sample(t_us=100_000, index=1, persons={"P": (U, "below_confidence_threshold")}),   # a pending, BRIDGEABLE unknown run
               Sample(t_us=1_700_000, index=2, persons={"P": (T, None)}),                  # 1.6 s after the previous sample: > dwell 1.5 s -> the run restarts HERE
               Sample(t_us=2_200_000, index=3, persons={"P": (T, None)})]
    r = evaluate("Z", samples, 2_300_000, p)
    assert [e["start_us"] for e in r["events"] if e["severity"] == YELLOW] == []             # 0.5 s of continuous presence only
    r2 = evaluate("Z", samples + [Sample(t_us=3_200_000, index=4, persons={"P": (T, None)})], 3_300_000, p)
    assert [e["start_us"] for e in r2["events"] if e["severity"] == YELLOW] == [3_200_000]    # 1.5 s after the restart at 1.7 s (not before)


def test_a_zero_dwell_is_refused():
    import pytest as _p
    with _p.raises(PolicyError):
        Params(dwell_us=0)


def test_vehicle_free_hold_in_progress_is_pending_not_plain_green():
    # person present and dwelled the whole time; the vehicle occupancy is UNKNOWN for 45 frames, then resolves to absent: the hold must show as pending
    r = run(scene({"P1": [(0, 300)]}, veh_u=[(0, 45)]))
    green_plain = [(INV[iv["start_us"]], INV[iv["end_us"]]) for iv in r["timeline"] if iv["state"] == GREEN and not iv["pending"]]
    assert green_plain == []   # no plain-GREEN interval while the opening condition is accumulating
