"""MG-VERTICAL-02 checkpoint A: the four cycle-20 findings (MG-WCAM-019 / 020 / 021 and the accounting of MG-EVIDENCE-019's sibling documents).
Everything is synthetic and isolated; no personal image is read, copied or deleted, and no real server is contacted."""
import json
import os
import threading
import time

import pytest

from mineguard import session_docs as sd
from mineguard import webcam
from test_privacy import image_like_files, start_default
from test_vs02_webcam_risk import W, H, jpeg, mk, post, scene, wait_decided  # noqa: F401  (fixtures and helpers of the webcam suite)

pytestmark = pytest.mark.usefixtures("legacy_delete_enabled")   # MG-DELETE-035: dormant removal logic, isolated test server only

IU = 100_000   # the declared cadence of start_default (100 ms)


def sess(env, sid):
    return env.app.state.st.session_mgr.sessions[sid]


def bundle(env, run_id):
    return env.c.get(f"/api/sessions/{run_id}").json()


def feed(env, seqs, privacy=None):
    sid = start_default(env.c, **({} if privacy is None else {"privacy": privacy})).json()["session_id"]
    for n, q in enumerate(seqs):
        assert post(env.c, sid, q).status_code == 202
        wait_decided(env.c, sid, n + 1)
    return sid


def verify(env, run_id):
    return env.c.get(f"/api/sessions/{run_id}/verify?deep=false").json()


# ------------------------------------------------------------------ MG-WCAM-021: initial, interior and terminal coverage
def test_a_session_that_begins_at_sequence_five_has_an_unknown_prefix_of_five_ticks(mk):
    env = mk(scene(person=range(0, 40)))
    sid = feed(env, [5, 6, 7])
    run = env.c.post(f"/api/webcam/sessions/{sid}/stop", headers={"X-MG-Next-Seq": "8"}).json()["run_id"]
    b = bundle(env, run)
    s = b["source"]["samples"]
    assert s[0]["reason"] == "client_loss" and s[0]["loss_position"] == "prefix" and s[0]["lost_frames"] == 5 and s[0]["seq"] is None and s[0]["capture_us"] == 0
    assert s[1]["seq"] == 5 and s[1]["capture_us"] == 5 * IU and s[2]["capture_us"] == 6 * IU      # real samples sit at their cadence position; the prefix is an EXTRAPOLATION, flagged below
    a = b["source"]["live_stats"]["accounting"]
    assert a["prefix_frames"] == 5 and a["origin_basis"] == "cadence_extrapolation" and a["never_observed_frames"] == 5 and a["accepted"] == 3 and a["unknown_intervals"] == 1
    assert verify(env, run)["ok"]
    ev = json.loads((env.runs / run / "risk" / "evaluation.json").read_text())
    unknown = sum(z["coverage_us"]["UNKNOWN"] for z in ev["zones"].values())
    assert unknown >= 5 * IU                                                                    # the whole prefix is UNKNOWN coverage, never silently GREEN or absent


def test_a_session_starting_at_sequence_zero_has_no_prefix_and_a_measured_origin(mk):
    env = mk(scene(person=range(0, 40)))
    sid = feed(env, [0, 1, 2])
    run = env.c.post(f"/api/webcam/sessions/{sid}/stop", headers={"X-MG-Next-Seq": "3"}).json()["run_id"]
    a = bundle(env, run)["source"]["live_stats"]["accounting"]
    assert a["prefix_frames"] == 0 and a["origin_basis"] == "measured" and a["never_observed_frames"] == 0 and a["unknown_intervals"] == 0 and a["tail_exact"] is True
    assert all(s["reason"] != "client_loss" for s in bundle(env, run)["source"]["samples"])


def test_five_lost_trailing_ticks_cover_five_intervals_not_a_half_interval(mk):
    env = mk(scene(person=range(0, 40)))
    sid = feed(env, [0])
    run = env.c.post(f"/api/webcam/sessions/{sid}/stop", headers={"X-MG-Next-Seq": "6"}).json()["run_id"]
    b = bundle(env, run)
    stub = b["source"]["samples"][-1]
    assert stub["loss_position"] == "tail" and stub["lost_frames"] == 5 and stub["capture_us"] == IU
    ft = b["source"]["frame_table"]
    assert ft["last_frame_duration_ticks"] == 5 * IU                                           # the declared extent, ending ~600 ms after the last observation
    a = b["source"]["live_stats"]["accounting"]
    assert a["tail_frames"] == 5 and a["tail_basis"] == "declared_by_stop" and a["tail_exact"] is True and a["closure"] == "clean_stop" and a["captured_declared"] == 6
    ev = json.loads((env.runs / run / "risk" / "evaluation.json").read_text())
    assert sum(z["coverage_us"]["UNKNOWN"] for z in ev["zones"].values()) >= 4 * IU
    assert verify(env, run)["ok"]


def test_sparse_samples_account_each_interior_gap_separately(mk):
    env = mk(scene(person=range(0, 40)))
    sid = feed(env, [0, 3, 9])
    run = env.c.post(f"/api/webcam/sessions/{sid}/stop", headers={"X-MG-Next-Seq": "10"}).json()["run_id"]
    b = bundle(env, run)
    stubs = [s for s in b["source"]["samples"] if s["reason"] == "client_loss"]
    assert [(s["lost_frames"], s["loss_position"]) for s in stubs] == [(2, "gap"), (5, "gap")]
    assert [s["capture_us"] for s in stubs] == [IU, 4 * IU]                                    # UNKNOWN begins one cadence interval after the last observation
    a = b["source"]["live_stats"]["accounting"]
    assert a["gap_frames"] == 7 and a["unknown_intervals"] == 2 and a["accepted"] == 3 and a["submitted"] == 3
    assert verify(env, run)["ok"]


def test_reordered_and_duplicate_frames_are_refused_and_counted_as_submitted_not_accepted(mk):
    env = mk(scene(person=range(0, 40)))
    sid = feed(env, [0, 3])
    assert post(env.c, sid, 2).status_code == 409           # reordered: older than an accepted frame
    assert post(env.c, sid, 3).status_code == 409           # duplicate
    run = env.c.post(f"/api/webcam/sessions/{sid}/stop", headers={"X-MG-Next-Seq": "4"}).json()["run_id"]
    a = bundle(env, run)["source"]["live_stats"]["accounting"]
    assert a["accepted"] == 2 and a["submitted"] == 4 and a["gap_frames"] == 2
    assert verify(env, run)["ok"]


def test_a_vanished_client_without_any_declaration_is_unobserved_with_an_unknown_exact_count(mk, monkeypatch):
    monkeypatch.setattr(webcam, "IDLE_FINALIZE_S", 0.5)
    env = mk(scene(person=range(0, 40)))
    sid = feed(env, [0, 1])
    t = time.time()
    while time.time() - t < 8 and env.c.get(f"/api/webcam/sessions/{sid}").json()["state"] != "done":
        time.sleep(0.1)
    run = env.c.get(f"/api/webcam/sessions/{sid}").json()["run_id"]
    a = bundle(env, run)["source"]["live_stats"]["accounting"]
    assert a["closure"] == "unexpected_disconnect" and a["tail_basis"] == "none_right_censored" and a["tail_exact"] is False and a["captured_declared"] is None
    assert a["tail_frames"] == 0 and a["never_observed_frames"] == 0       # nothing is invented: the tail length is simply NOT KNOWN, and the record says so


def test_accounting_is_checked_against_the_sample_log_and_tampering_is_detected(mk):
    env = mk(scene(person=range(0, 40)))
    sid = feed(env, [0, 3])
    run = env.c.post(f"/api/webcam/sessions/{sid}/stop", headers={"X-MG-Next-Seq": "4"}).json()["run_id"]
    ls = bundle(env, run)["source"]["live_stats"]
    assert sd.accounting_consistent(ls, bundle(env, run)["source"]["samples"])
    for key, val in (("gap_frames", 9), ("never_observed_frames", 0), ("accepted", 7), ("unknown_intervals", 5), ("prefix_frames", 1)):
        forged = {**ls, "accounting": {**ls["accounting"], key: val}}
        assert not sd.accounting_consistent(forged, bundle(env, run)["source"]["samples"]), key


# ------------------------------------------------------------------ MG-WCAM-019: acknowledged, idempotent lifecycle messages and the bounded late declaration
def test_a_retried_control_message_gets_the_same_answer_and_changes_nothing(mk):
    env = mk(scene(person=range(0, 40)))
    sid = feed(env, [0])
    h = {"X-MG-Next-Seq": "4", "X-MG-Msg-Seq": "7"}
    first = env.c.post(f"/api/webcam/sessions/{sid}/watermark", headers=h).json()
    again = env.c.post(f"/api/webcam/sessions/{sid}/watermark", headers=h).json()
    assert first["ack"] == 7 and first["duplicate"] is False and again["ack"] == 7 and again["duplicate"] is True and again["watermark"] == first["watermark"] == 4
    older = env.c.post(f"/api/webcam/sessions/{sid}/watermark", headers={"X-MG-Next-Seq": "2", "X-MG-Msg-Seq": "3"}).json()
    assert older["reordered"] is True and older["watermark"] == 4              # a message that arrives late can never lower the cumulative watermark
    s, mgr = sess(env, sid), env.app.state.st.session_mgr
    assert s.watermark == 4 and set(mgr.ctl[sid]) == {7, 3}


@pytest.mark.parametrize("bad", ["-1", "x", "2000000001", ""])
def test_control_message_numbers_are_validated(mk, bad):
    env = mk(scene(person=range(0, 40)))
    sid = feed(env, [0])
    assert env.c.post(f"/api/webcam/sessions/{sid}/watermark", headers={"X-MG-Next-Seq": "1", "X-MG-Msg-Seq": bad}).status_code == 400


def test_the_control_cache_is_bounded(mk):
    env = mk(scene(person=range(0, 40)))
    sid = feed(env, [0])
    for n in range(webcam.CTL_CACHE + 20):
        env.c.post(f"/api/webcam/sessions/{sid}/watermark", headers={"X-MG-Next-Seq": "1", "X-MG-Msg-Seq": str(n)})
    assert len(env.app.state.st.session_mgr.ctl[sid]) == webcam.CTL_CACHE


def test_a_duplicated_stop_is_one_finalization_with_one_trust_grant(mk):
    env = mk(scene(person=range(0, 40)))
    sid = feed(env, [0, 1])
    h = {"X-MG-Next-Seq": "2", "X-MG-Msg-Seq": "1"}
    a = env.c.post(f"/api/webcam/sessions/{sid}/stop", headers=h).json()
    b = env.c.post(f"/api/webcam/sessions/{sid}/stop", headers=h).json()
    assert a["ack"] == b["ack"] == 1 and a["run_id"] == b["run_id"]
    assert env.c.get(f"/api/sessions/{a['run_id']}").json()["trust"] == "session"


def disconnect(env, monkeypatch, frames=(0,), idle=0.5):
    monkeypatch.setattr(webcam, "IDLE_FINALIZE_S", idle)
    sid = feed(env, list(frames))
    t = time.time()
    while time.time() - t < 8 and env.c.get(f"/api/webcam/sessions/{sid}").json()["state"] != "done":
        time.sleep(0.05)
    v = env.c.get(f"/api/webcam/sessions/{sid}").json()
    assert v["state"] == "done" and v["closed_reason"] == "client_disconnected"
    return sid, v["run_id"]


def test_a_late_stop_after_unexpected_finalization_is_admitted_as_a_sidecar_and_the_evidence_is_untouched(mk, monkeypatch):
    env = mk(scene(person=range(0, 40)))
    sid, run = disconnect(env, monkeypatch)
    before = {p.name: p.read_bytes() for p in (env.runs / run).iterdir() if p.is_file()}
    time.sleep(0.8)                                                          # ticks kept being issued while the connection was down
    r = env.c.post(f"/api/webcam/sessions/{sid}/stop", headers={"X-MG-Next-Seq": "6", "X-MG-Msg-Seq": "9"})
    assert r.status_code == 200 and r.json()["late_declaration"]["admitted"] is True and r.json()["late_declaration"]["digest_bound"] is False
    after = {p.name: p.read_bytes() for p in (env.runs / run).iterdir() if p.is_file()}
    assert set(after) - set(before) == {"late_declaration.json"}
    assert all(after[k] == v for k, v in before.items())                     # NOT ONE digest-bound document was rewritten
    b = bundle(env, run)
    assert b["late_declaration"]["declarations"][-1]["unobserved_tail_frames_declared"] == 5 and b["late_declaration"]["digest_bound"] is False
    assert b["manifest"]["counters"]["client_lost_frames"] == 0 and b["source"]["live_stats"]["accounting"]["tail_exact"] is False   # the original record keeps its honest limit
    v = verify(env, run)
    assert v["ok"] and any(c["id"] == "late_declaration_sidecar_is_wellformed_and_not_part_of_the_identity" and c["ok"] for c in v["checks"])


def test_late_declarations_are_monotonic_plausible_and_only_for_unexpected_closures(mk, monkeypatch):
    env = mk(scene(person=range(0, 40)))
    sid, run = disconnect(env, monkeypatch)
    time.sleep(0.8)
    h = lambda n: {"X-MG-Next-Seq": str(n)}
    assert env.c.post(f"/api/webcam/sessions/{sid}/watermark", headers=h(5)).json()["late_declaration"]["admitted"] is True
    assert env.c.post(f"/api/webcam/sessions/{sid}/watermark", headers=h(5)).json()["late_declaration"]["reason"] == "not_monotonic"
    assert env.c.post(f"/api/webcam/sessions/{sid}/watermark", headers=h(3)).json()["late_declaration"]["reason"] == "not_monotonic"
    assert env.c.post(f"/api/webcam/sessions/{sid}/watermark", headers=h(5000)).json()["late_declaration"]["reason"] == "declaration_not_plausible_for_elapsed_time"
    assert env.c.post(f"/api/webcam/sessions/{sid}/watermark", headers=h(0)).json()["late_declaration"]["reason"] == "declaration_precedes_last_accepted_frame"
    assert len(bundle(env, run)["late_declaration"]["declarations"]) == 1


def test_the_late_window_is_bounded_and_server_measured(mk, monkeypatch):
    env = mk(scene(person=range(0, 40)))
    sid, run = disconnect(env, monkeypatch)
    time.sleep(0.8)
    monkeypatch.setattr(webcam, "ADDENDUM_WINDOW_S", 0.0)
    r = env.c.post(f"/api/webcam/sessions/{sid}/stop", headers={"X-MG-Next-Seq": "4"}).json()
    assert r["late_declaration"] == {"admitted": False, "reason": "declaration_window_closed"}
    assert not (env.runs / run / "late_declaration.json").exists()
    assert webcam.ADDENDUM_WINDOW_S == 0.0 and 0 < 300.0                         # documented default: 300 s (see MG-VERTICAL2 docs)


def test_a_clean_stop_is_final_and_accepts_no_late_declaration(mk):
    env = mk(scene(person=range(0, 40)))
    sid = feed(env, [0, 1])
    run = env.c.post(f"/api/webcam/sessions/{sid}/stop", headers={"X-MG-Next-Seq": "2"}).json()["run_id"]
    time.sleep(0.3)
    r = env.c.post(f"/api/webcam/sessions/{sid}/stop", headers={"X-MG-Next-Seq": "9"}).json()
    assert r["late_declaration"] == {"admitted": False, "reason": "session_was_closed_cleanly"}
    assert not (env.runs / run / "late_declaration.json").exists() and bundle(env, run)["late_declaration"] is None


def test_a_tampered_sidecar_fails_verification(mk, monkeypatch):
    env = mk(scene(person=range(0, 40)))
    sid, run = disconnect(env, monkeypatch)
    time.sleep(0.8)
    env.c.post(f"/api/webcam/sessions/{sid}/stop", headers={"X-MG-Next-Seq": "5"})
    f = env.runs / run / "late_declaration.json"
    doc = json.loads(f.read_text())
    doc["declarations"].append({"declared_next_seq": 1})                          # non-monotonic forged entry
    f.write_text(json.dumps(doc))
    assert not verify(env, run)["ok"]
    f.write_text("not json")
    assert not verify(env, run)["ok"]


def test_deleting_a_session_removes_its_late_declaration_too(mk, monkeypatch):
    env = mk(scene(person=range(0, 40)))
    sid, run = disconnect(env, monkeypatch)
    time.sleep(0.8)
    env.c.post(f"/api/webcam/sessions/{sid}/stop", headers={"X-MG-Next-Seq": "5"})
    assert (env.runs / run / "late_declaration.json").exists()
    r = env.c.post("/api/sessions/delete", json={"ids": [run], "confirm": "ELIMINAR", "expected_count": 1})
    assert r.status_code == 200 and not (env.runs / run).exists()


# ------------------------------------------------------------------ MG-WCAM-020: freshness at the commit boundary
def spool_dirs(env):
    return [p for p in env.runs.glob("_tmp_*/frames/*")] + [p for p in env.runs.glob("session_*/frames/*")]


def test_a_result_that_turns_stale_while_it_is_spooled_is_rejected_and_leaves_no_file(mk, monkeypatch):
    monkeypatch.setattr(webcam, "MAX_AGE_US", 300_000)
    env = mk(scene(person=range(0, 40)))
    real, n = webcam.sha256_hex, {"c": 0}

    def slow_hash(b):
        n["c"] += 1
        if n["c"] == 1:
            time.sleep(0.6)       # the time between "inference done" and "commit": the JPEG is persisted (Privacy OFF) and hashed after the model returned
        return real(b)
    monkeypatch.setattr(webcam, "sha256_hex", slow_hash)
    sid = start_default(env.c, privacy="off").json()["session_id"]
    post(env.c, sid, 0)
    t = time.time()
    while time.time() - t < 10 and env.c.get(f"/api/webcam/sessions/{sid}").json()["counters"]["dropped_stale"] < 1:
        time.sleep(0.05)
    v = env.c.get(f"/api/webcam/sessions/{sid}").json()
    assert v["counters"]["dropped_stale"] == 1 and v["counters"]["analyzed"] == 0 and v["latest"]["seq"] is None and v["latest"]["objects"] == []
    assert [p.name for p in spool_dirs(env)] == []                               # the test-owned JPEG of the rejected result was removed: no .part, no .jpg
    for q in (1, 2, 3):
        post(env.c, sid, q); wait_decided(env.c, sid, q + 1)
    run = env.c.post(f"/api/webcam/sessions/{sid}/stop", headers={"X-MG-Next-Seq": "4"}).json()["run_id"]
    b = bundle(env, run)
    assert b["source"]["samples"][0]["reason"] == "stale" and b["source"]["samples"][0]["jpeg_sha256"] is None
    assert sorted(p.name for p in (env.runs / run / "frames").iterdir()) == ["000001.jpg", "000002.jpg", "000003.jpg"]    # evidence exists only for committed frames
    assert verify(env, run)["ok"]


def test_a_result_descheduled_before_the_commit_lock_is_judged_at_the_lock(mk, monkeypatch):
    monkeypatch.setattr(webcam, "MAX_AGE_US", 300_000)
    env = mk(scene(person=range(0, 40)))
    inside, proceed, real = threading.Event(), threading.Event(), env.det.predict

    def signalling(rgb, thr):
        inside.set()
        assert proceed.wait(5)                                       # the model "returns" only after the test holds the session lock
        return real(rgb, thr)
    monkeypatch.setattr(env.det, "predict", signalling)
    sid = start_default(env.c).json()["session_id"]
    post(env.c, sid, 0)
    assert inside.wait(5)
    s = sess(env, sid)
    with s.lock:                      # the worker finishes inference and now waits for the session lock (a stand-in for being descheduled)
        proceed.set()
        time.sleep(0.6)
    t = time.time()
    while time.time() - t < 10 and env.c.get(f"/api/webcam/sessions/{sid}").json()["counters"]["dropped_stale"] < 1:
        time.sleep(0.05)
    v = env.c.get(f"/api/webcam/sessions/{sid}").json()
    assert v["counters"]["dropped_stale"] == 1 and v["counters"]["analyzed"] == 0 and v["latest"]["objects"] == [] and v["risk"] == {"Z1": v["risk"].get("Z1")}
    assert v["risk"]["Z1"]["state"] in ("UNKNOWN", "GREEN") and v["risk"]["Z1"]["state"] != "RED"


def test_a_fresh_result_still_commits_atomically_with_its_evidence(mk):
    env = mk(scene(person=range(0, 40)))
    sid = start_default(env.c, privacy="off").json()["session_id"]
    for q in range(4):
        post(env.c, sid, q); wait_decided(env.c, sid, q + 1)
    run = env.c.post(f"/api/webcam/sessions/{sid}/stop", headers={"X-MG-Next-Seq": "4"}).json()["run_id"]
    b = bundle(env, run)
    assert b["manifest"]["counters"]["analyzed"] == 4 and len(b["manifest"]["evidence"]) == 4 and verify(env, run)["ok"]


def test_privacy_on_never_spools_a_pixel_even_when_results_are_rejected(mk, monkeypatch):
    monkeypatch.setattr(webcam, "MAX_AGE_US", 200_000)
    env = mk(scene(person=range(0, 40)), delay=0.5)         # every frame is too slow: all are rejected as stale
    sid = start_default(env.c).json()["session_id"]
    for q in range(3):
        post(env.c, sid, q)
    time.sleep(2.0)
    assert image_like_files(env.tmp) == [] and not list(env.runs.glob("_tmp_*/frames"))
    k = env.c.get(f"/api/webcam/sessions/{sid}").json()["counters"]
    assert k["analyzed"] == 0 and k["dropped_stale"] >= 1


# ------------------------------------------------------------------ MG-WCAM-022: stops are numbered, acknowledged and idempotent too (live sessions AND finished ones)
def test_a_retried_stop_returns_the_original_answer_and_changes_nothing(mk):
    env = mk(scene(person=range(0, 40)))
    sid = feed(env, [0, 1])
    h = {"X-MG-Next-Seq": "2", "X-MG-Msg-Seq": "11"}
    a = env.c.post(f"/api/webcam/sessions/{sid}/stop", headers=h).json()
    b = env.c.post(f"/api/webcam/sessions/{sid}/stop", headers=h).json()           # the session is a tombstone by now: the cache survived the conversion
    assert a["duplicate"] is False and b["duplicate"] is True and a["run_id"] == b["run_id"] and a["ack"] == b["ack"] == 11
    assert env.c.get(f"/api/sessions/{a['run_id']}").json()["trust"] == "session"


def test_a_retried_numbered_late_stop_after_the_reaper_is_a_duplicate_not_a_second_declaration(mk, monkeypatch):
    env = mk(scene(person=range(0, 40)))
    sid, run = disconnect(env, monkeypatch)
    time.sleep(0.8)
    h = {"X-MG-Next-Seq": "6", "X-MG-Msg-Seq": "9"}
    first = env.c.post(f"/api/webcam/sessions/{sid}/stop", headers=h).json()
    retry = env.c.post(f"/api/webcam/sessions/{sid}/stop", headers=h).json()
    assert first["late_declaration"]["admitted"] is True and first["duplicate"] is False
    assert retry["duplicate"] is True and retry["late_declaration"] == first["late_declaration"]                # the SAME acknowledged result, not "not_monotonic"
    assert len(bundle(env, run)["late_declaration"]["declarations"]) == 1


def test_stops_and_heartbeats_report_reordering_by_message_number(mk):
    env = mk(scene(person=range(0, 40)))
    sid = feed(env, [0])
    assert env.c.post(f"/api/webcam/sessions/{sid}/watermark", headers={"X-MG-Next-Seq": "3", "X-MG-Msg-Seq": "8"}).json()["reordered"] is False
    r = env.c.post(f"/api/webcam/sessions/{sid}/stop", headers={"X-MG-Next-Seq": "3", "X-MG-Msg-Seq": "4"}).json()
    assert r["reordered"] is True and r["ack"] == 4 and r["state"] == "done"                                    # an older number still carries a valid, terminal stop


def test_the_same_message_number_with_a_different_payload_is_refused(mk):
    env = mk(scene(person=range(0, 40)))
    sid = feed(env, [0])
    ok = env.c.post(f"/api/webcam/sessions/{sid}/watermark", headers={"X-MG-Next-Seq": "4", "X-MG-Msg-Seq": "2"})
    bad = env.c.post(f"/api/webcam/sessions/{sid}/watermark", headers={"X-MG-Next-Seq": "5", "X-MG-Msg-Seq": "2"})
    assert ok.status_code == 202 and bad.status_code == 409 and bad.json()["detail"]["msg_id"] == "mg.refusal.control_message_conflict"
    assert sess(env, sid).watermark == 4                                                                         # the conflicting copy changed nothing
    s1 = env.c.post(f"/api/webcam/sessions/{sid}/stop", headers={"X-MG-Next-Seq": "4", "X-MG-Msg-Seq": "9"})
    s2 = env.c.post(f"/api/webcam/sessions/{sid}/stop", headers={"X-MG-Next-Seq": "7", "X-MG-Msg-Seq": "9"})
    assert s1.status_code == 200 and s2.status_code == 409


def test_a_stop_that_failed_is_not_cached_so_the_same_message_can_be_retried(mk, monkeypatch):
    env = mk(scene(person=range(0, 40)))
    sid = feed(env, [0, 1])
    s = sess(env, sid)
    real = s.stop
    n = {"c": 0}

    def flaky(reason="client_stop", next_seq=None):
        n["c"] += 1
        if n["c"] == 1:
            raise webcam.SessionError(503, "mg.refusal.session_worker_busy")
        return real(reason, next_seq)
    monkeypatch.setattr(s, "stop", flaky)
    h = {"X-MG-Next-Seq": "2", "X-MG-Msg-Seq": "5"}
    assert env.c.post(f"/api/webcam/sessions/{sid}/stop", headers=h).status_code == 503
    r = env.c.post(f"/api/webcam/sessions/{sid}/stop", headers=h)
    assert r.status_code == 200 and r.json()["duplicate"] is False and r.json()["run_id"]


def test_control_state_is_released_with_the_session_tombstone(mk):
    env = mk(scene(person=range(0, 40)))
    mgr = env.app.state.st.session_mgr
    for n in range(webcam.MAX_TOMBSTONES + 5):
        sid = start_default(env.c).json()["session_id"]
        post(env.c, sid, 0); wait_decided(env.c, sid, 1)
        env.c.post(f"/api/webcam/sessions/{sid}/stop", headers={"X-MG-Next-Seq": "1", "X-MG-Msg-Seq": "1"})
    assert len(mgr.ctl) <= webcam.MAX_TOMBSTONES + 1 and set(mgr.ctl) <= set(mgr.sessions)


# ------------------------------------------------------------------ cycle-23 review C: MG-WCAM-023 (a refused frame is not client loss) and MG-LIFE-023
def post_raw(env, sid, seq, body, cap_us=None):
    return env.c.post(f"/api/webcam/sessions/{sid}/frames", content=body, headers={"X-MG-Seq": str(seq), "X-MG-Capture-Us": str(cap_us if cap_us is not None else 5_000_000 + seq * 100_000), "Content-Type": "image/jpeg"})


def acct(env, run):
    return bundle(env, run)["source"]["live_stats"]["accounting"]


def test_an_invalid_first_frame_is_a_refused_prefix_not_client_loss(mk):
    env = mk(scene(person=range(0, 40)))
    sid = start_default(env.c).json()["session_id"]
    assert post_raw(env, sid, 0, b"this is not a jpeg").status_code == 400
    assert post(env.c, sid, 1).status_code == 202
    wait_decided(env.c, sid, 1)
    run = env.c.post(f"/api/webcam/sessions/{sid}/stop", headers={"X-MG-Next-Seq": "2"}).json()["run_id"]
    a = acct(env, run)
    assert a["invalid_requests"] == 1 and a["rejected_invalid_frames"] == 1 and a["never_observed_frames"] == 0 and a["prefix_frames"] == 1 and a["submitted"] == 2 and a["accepted"] == 1
    stub = bundle(env, run)["source"]["samples"][0]
    assert stub["reason"] == "client_loss" and stub["lost_frames"] == 1 and stub["rejected_frames"] == 1                       # the interval is still UNKNOWN, but it is explained
    assert verify(env, run)["ok"]


def test_refused_and_lost_ticks_are_told_apart_inside_one_gap_and_one_tail(mk):
    env = mk(scene(person=range(0, 40)))
    sid = start_default(env.c).json()["session_id"]
    post(env.c, sid, 0); wait_decided(env.c, sid, 1)
    assert post_raw(env, sid, 1, b"\xff\xd8\xff garbage").status_code == 400            # seq 1 reached the server and was refused; seq 2 never arrived
    post(env.c, sid, 3); wait_decided(env.c, sid, 2)
    assert post_raw(env, sid, 4, jpeg(4, w=W + 10)).status_code == 400                    # wrong size, refused
    run = env.c.post(f"/api/webcam/sessions/{sid}/stop", headers={"X-MG-Next-Seq": "7"}).json()["run_id"]      # seq 5 and 6 never arrived (tail)
    a = acct(env, run)
    assert a["gap_frames"] == 2 and a["tail_frames"] == 3 and a["rejected_invalid_frames"] == 2 and a["never_observed_frames"] == 3
    assert a["prefix_frames"] + a["gap_frames"] + a["tail_frames"] == a["never_observed_frames"] + a["rejected_invalid_frames"] and a["invalid_requests"] == 2 and a["submitted"] == 4
    assert verify(env, run)["ok"]


def test_an_oversize_frame_is_refused_and_accounted_before_its_body_is_read(mk):
    env = mk(scene(person=range(0, 40)))
    sid = start_default(env.c).json()["session_id"]
    r = env.c.post(f"/api/webcam/sessions/{sid}/frames", content=b"x" * (webcam.MAX_FRAME_BYTES + 10), headers={"X-MG-Seq": "0", "X-MG-Capture-Us": "5000000", "Content-Type": "image/jpeg"})
    assert r.status_code == 413
    post(env.c, sid, 1); wait_decided(env.c, sid, 1)
    run = env.c.post(f"/api/webcam/sessions/{sid}/stop", headers={"X-MG-Next-Seq": "2"}).json()["run_id"]
    a = acct(env, run)
    assert a["rejected_invalid_frames"] == 1 and a["never_observed_frames"] == 0 and a["invalid_requests"] == 1


def test_a_request_without_usable_headers_is_still_refused_and_never_creates_a_sequence_hole(mk):
    env = mk(scene(person=range(0, 40)))
    sid = start_default(env.c).json()["session_id"]
    assert env.c.post(f"/api/webcam/sessions/{sid}/frames", content=jpeg(0), headers={"X-MG-Capture-Us": "1"}).status_code == 400
    assert post_raw(env, sid, 99_999_999, jpeg(0)).status_code == 400
    post(env.c, sid, 0); wait_decided(env.c, sid, 1)
    run = env.c.post(f"/api/webcam/sessions/{sid}/stop", headers={"X-MG-Next-Seq": "1"}).json()["run_id"]
    assert acct(env, run)["invalid_requests"] == 0 and acct(env, run)["prefix_frames"] == 0


def test_the_tracking_of_refused_sequences_is_bounded(mk):
    env = mk(scene(person=range(0, 40)))
    sid = start_default(env.c).json()["session_id"]
    s = sess(env, sid)
    for q in range(0, 2 * (webcam.MAX_LEDGER_RANGES + 50), 2):      # disjoint islands: the worst case for the ledger
        s.note_invalid(q)
    assert len(s.ledger.ranges) == webcam.MAX_LEDGER_RANGES and s.invalid_requests == webcam.MAX_LEDGER_RANGES + 50 and s.unrecorded == 50


def test_the_refused_count_is_part_of_the_verified_accounting_and_a_forged_split_is_detected(mk):
    env = mk(scene(person=range(0, 40)))
    sid = start_default(env.c).json()["session_id"]
    post(env.c, sid, 0); wait_decided(env.c, sid, 1)
    post_raw(env, sid, 1, b"junk")
    post(env.c, sid, 2); wait_decided(env.c, sid, 2)
    run = env.c.post(f"/api/webcam/sessions/{sid}/stop", headers={"X-MG-Next-Seq": "3"}).json()["run_id"]
    b = bundle(env, run)
    ls, samples = b["source"]["live_stats"], b["source"]["samples"]
    assert sd.accounting_consistent(ls, samples)
    forged = {**ls, "accounting": {**ls["accounting"], "rejected_invalid_frames": 0, "never_observed_frames": 1}}               # re-labelling refused frames as client loss
    assert not sd.accounting_consistent(forged, samples)
    forged2 = [{**x, "rejected_frames": 0} if x.get("reason") == "client_loss" else x for x in samples]
    assert not sd.accounting_consistent(ls, forged2)


def test_accounting_of_sessions_stored_before_the_split_still_validates():
    old = {"submitted": 3, "accepted": 3, "analyzed": 3, "dropped": 0, "adapter_error": 0, "never_observed_frames": 0, "prefix_frames": 0, "gap_frames": 0, "tail_frames": 0, "unknown_intervals": 0,
           "captured_declared": 3, "origin_basis": "measured", "tail_basis": "declared_by_stop", "tail_exact": True, "closure": "clean_stop"}
    samples = [{"seq": i, "capture_us": i, "status": "analyzed", "reason": None, "jpeg_sha256": None} for i in range(3)]
    assert sd.accounting_ok(old) and sd.accounting_consistent({"accounting": old}, samples)


def test_one_stop_request_never_waits_longer_than_a_few_seconds_for_a_busy_worker():
    assert webcam.WORKER_JOIN_S <= 5.0                                                      # a client's total retry time is its own wall-clock deadline (lifecycle.ts retryBusy), not 120 s per attempt
