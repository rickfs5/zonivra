"""Cycle-24 remediation vectors for MG-WCAM-024 (OAF-VERTICAL-01): every sequence-bearing request that reached the server has an OUTCOME in a bounded interval ledger, and gaps are reconciled from it:
an examined request is never relabelled as never observed. Synthetic only. Each vector asserts the CORRECT behaviour; they failed on 147f139."""
import pytest

from mineguard import session_docs as sd
from mineguard import webcam
from test_privacy import start_default
from test_vertical2_wcam import acct, bundle, post_raw, sess
from test_vs02_webcam_risk import jpeg, mk, post, scene, wait_decided  # noqa: F401


def keys(a):
    return {k: a[k] for k in ("submitted", "accepted", "prefix_frames", "gap_frames", "tail_frames", "never_observed_frames", "rejected_invalid_frames", "unclassified_frames", "invalid_requests")}


def finish(env, sid, next_seq):
    return env.c.post(f"/api/webcam/sessions/{sid}/stop", headers={"X-MG-Next-Seq": str(next_seq)}).json()["run_id"]


# ---------------------------------------------------------------- the ledger itself
def test_ledger_merges_adjacent_sequences_counts_inside_a_span_and_is_bounded():
    L = webcam.SeqLedger(cap=4)
    assert all(L.add(q) for q in (5, 6, 7, 3))                  # {3}, {5..7}
    assert L.ranges == [[3, 3], [5, 7]] and L.count_in(0, 10) == 4 and L.count_in(4, 5) == 1 and L.count_in(8, 20) == 0
    assert L.add(4) and L.ranges == [[3, 7]]                      # the hole closes: one range
    assert L.add(7) is True and L.count_in(3, 7) == 5             # idempotent
    assert all(L.add(q) for q in (20, 30, 40)) and L.add(50) is False and len(L.ranges) == 4   # capacity: a new island is refused, never merged across a hole
    assert L.count_in(41, 49) == 0


def test_ledger_never_claims_a_sequence_that_was_not_recorded():
    L = webcam.SeqLedger(cap=2)
    L.add(10); L.add(20); L.add(30)
    assert L.count_in(0, 100) == 2 and L.count_in(11, 19) == 0 and L.count_in(21, 29) == 0


# ---------------------------------------------------------------- reversed arrival
def test_reversed_arrival_does_not_relabel_a_received_frame_as_client_loss(mk):
    env = mk(scene(person=range(0, 40)))
    sid = start_default(env.c).json()["session_id"]
    assert post(env.c, sid, 1).status_code == 202
    wait_decided(env.c, sid, 1)
    assert post(env.c, sid, 0).status_code == 409                 # the late frame is still refused as non-monotonic ...
    run = finish(env, sid, 2)
    a = acct(env, run)
    assert keys(a) == {"submitted": 2, "accepted": 1, "prefix_frames": 1, "gap_frames": 0, "tail_frames": 0, "never_observed_frames": 0,
                       "rejected_invalid_frames": 1, "unclassified_frames": 0, "invalid_requests": 0}                    # ... but it is accounted as EXAMINED, not as never observed
    assert env.c.get(f"/api/sessions/{run}/verify?deep=false").json()["ok"] is True
    b = bundle(env, run)
    assert sd.accounting_consistent(b["source"]["live_stats"], b["source"]["samples"])


def test_reversed_arrival_inside_a_gap_and_genuine_loss_are_told_apart(mk):
    env = mk(scene(person=range(0, 40)))
    sid = start_default(env.c).json()["session_id"]
    for q in (0, 2):
        assert post(env.c, sid, q).status_code == 202
        wait_decided(env.c, sid, 1 if q == 0 else 2)
    assert post(env.c, sid, 1).status_code == 409                  # arrived late (examined)
    assert post(env.c, sid, 5).status_code == 202                  # seq 3 and 4 never arrive
    wait_decided(env.c, sid, 3)
    run = finish(env, sid, 6)
    a = acct(env, run)
    assert a["gap_frames"] == 3 and a["rejected_invalid_frames"] == 1 and a["never_observed_frames"] == 2 and a["unclassified_frames"] == 0
    b = bundle(env, run)
    assert sd.accounting_consistent(b["source"]["live_stats"], b["source"]["samples"])


def test_live_accounting_reflects_a_late_arrival_before_the_session_ends(mk):
    env = mk(scene(person=range(0, 40)))
    sid = start_default(env.c).json()["session_id"]
    post(env.c, sid, 1); wait_decided(env.c, sid, 1)
    post(env.c, sid, 0)
    a = sess(env, sid).accounting()
    assert a["rejected_invalid_frames"] == 1 and a["never_observed_frames"] == 0


def test_a_transport_invalid_tail_request_is_examined_not_lost(mk):
    env = mk(scene(person=range(0, 40)))
    sid = start_default(env.c).json()["session_id"]
    post(env.c, sid, 0); wait_decided(env.c, sid, 1)
    assert post_raw(env, sid, 2, b"junk").status_code == 400
    run = finish(env, sid, 3)
    a = acct(env, run)
    assert a["tail_frames"] == 2 and a["rejected_invalid_frames"] == 1 and a["never_observed_frames"] == 1


# ---------------------------------------------------------------- capacity
def test_consecutive_refused_requests_beyond_the_old_limit_are_all_classified(mk):
    env = mk(scene(person=range(0, 40)))
    sid = start_default(env.c).json()["session_id"]
    s = sess(env, sid)
    for q in range(5000):
        s.note_invalid(q)                                         # one contiguous island: a single ledger range
    assert post(env.c, sid, 5000, body=jpeg(0)).status_code == 202
    wait_decided(env.c, sid, 1)
    a = acct(env, finish(env, sid, 5001))
    assert a["invalid_requests"] == 5000 and a["rejected_invalid_frames"] == 5000 and a["never_observed_frames"] == 0 and a["unclassified_frames"] == 0


def test_beyond_ledger_capacity_the_remainder_is_explicitly_unclassified_never_never_observed(mk):
    env = mk(scene(person=range(0, 40)))
    sid = start_default(env.c).json()["session_id"]
    s = sess(env, sid)
    n_inv = webcam.MAX_LEDGER_RANGES + 904
    for k in range(n_inv):
        s.note_invalid(2 * k)                                     # every even sequence is examined and refused; every odd one never arrives
    assert len(s.ledger.ranges) <= webcam.MAX_LEDGER_RANGES
    top = 2 * n_inv
    assert post(env.c, sid, top, body=jpeg(0)).status_code == 202
    wait_decided(env.c, sid, 1)
    run = finish(env, sid, top + 1)
    a = acct(env, run)
    assert a["invalid_requests"] == n_inv and a["rejected_invalid_frames"] == webcam.MAX_LEDGER_RANGES
    assert a["unclassified_frames"] == 904 and a["never_observed_frames"] == top - n_inv - 0        # exactly the odd sequences
    assert a["prefix_frames"] == a["never_observed_frames"] + a["rejected_invalid_frames"] + a["unclassified_frames"]
    b = bundle(env, run)
    assert sd.accounting_consistent(b["source"]["live_stats"], b["source"]["samples"])
    assert env.c.get(f"/api/sessions/{run}/verify?deep=false").json()["ok"] is True


# ---------------------------------------------------------------- the verifier re-derives the new split
def test_forged_unclassified_or_rejected_split_is_detected(mk):
    env = mk(scene(person=range(0, 40)))
    sid = start_default(env.c).json()["session_id"]
    s = sess(env, sid)
    for k in range(webcam.MAX_LEDGER_RANGES + 10):
        s.note_invalid(2 * k)
    post(env.c, sid, 2 * (webcam.MAX_LEDGER_RANGES + 10), body=jpeg(0)); wait_decided(env.c, sid, 1)
    b = bundle(env, finish(env, sid, 2 * (webcam.MAX_LEDGER_RANGES + 10) + 1))
    ls, samples = b["source"]["live_stats"], b["source"]["samples"]
    a = ls["accounting"]
    assert a["unclassified_frames"] == 10 and sd.accounting_consistent(ls, samples)
    for edit in ({"unclassified_frames": 0, "never_observed_frames": a["never_observed_frames"] + 10},          # hide the uncertainty as loss
                 {"unclassified_frames": 11}, {"rejected_invalid_frames": a["rejected_invalid_frames"] + 10, "unclassified_frames": 0}):
        assert not sd.accounting_consistent({**ls, "accounting": {**a, **edit}}, samples)
    forged = [{**x, "unclassified_frames": 0, "lost_frames": x["lost_frames"]} if x.get("reason") == "client_loss" else x for x in samples]
    assert not sd.accounting_consistent(ls, forged)


def test_accounting_shapes_stored_before_this_change_still_validate():
    v2 = {"submitted": 3, "accepted": 2, "analyzed": 2, "dropped": 0, "adapter_error": 0, "never_observed_frames": 0, "rejected_invalid_frames": 1, "invalid_requests": 1, "prefix_frames": 0, "gap_frames": 1,
          "tail_frames": 0, "unknown_intervals": 1, "captured_declared": 3, "origin_basis": "measured", "tail_basis": "declared_by_stop", "tail_exact": True, "closure": "clean_stop"}
    assert sd.accounting_ok(v2)
