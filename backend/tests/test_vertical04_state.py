"""MG-PRODUCT-VERTICAL-04: source lifecycle publishes CURRENT risk apart from LAST OBSERVED risk, unknown is never green, and the build/OAF identity is never invented. Synthetic data only."""
import json
import time

import pytest

from mineguard import buildinfo, feeds as feeds_mod
from test_vertical2_feeds import PERSON_IN, always, act, create, fm, view, wait_state  # noqa: F401
from test_vs02_webcam_risk import mk  # noqa: F401


def _analysed(env, fid, n=2, timeout=30):
    end = time.time() + timeout
    while time.time() < end and env.feeds.feeds[fid].counters["analyzed"] < n:
        time.sleep(0.05)
    assert env.feeds.feeds[fid].counters["analyzed"] >= n


def test_the_lifecycle_matrix_never_asserts_a_current_state_for_an_unobserved_feed(fm):
    env = fm({1: always(), 2: always()}, frames=400, delay={1: 0.01, 2: 0.01})
    a, b = create(env, 1, loop=True), create(env, 2, loop=True)
    v0 = view(env, a)
    assert v0["state"] == "idle" and v0["risk_state"] == "UNKNOWN" and v0["unknown_reason"] == "mg.unknown.idle" and v0["last_observed_risk"] is None
    act(env, a, "start"); act(env, b, "start")
    _analysed(env, a); _analysed(env, b)
    v = view(env, a)
    assert v["state"] == "playing" and v["unknown_reason"] is None and v["observation"]["fresh"] is True and v["risk_state"] == v["last_observed_risk"] != "UNKNOWN"
    act(env, a, "pause")
    v = view(env, a)
    assert v["state"] == "paused" and v["risk_state"] == "UNKNOWN" and v["unknown_reason"] == "mg.unknown.paused" and v["last_observed_risk"] in ("GREEN", "YELLOW", "RED")
    assert all(c["state"] == "UNKNOWN" for c in v["risk_current"].values())
    assert view(env, b)["state"] == "playing" and view(env, b)["unknown_reason"] is None                       # the other source is unaffected
    act(env, a, "resume")
    _analysed(env, a, env.feeds.feeds[a].counters["analyzed"] + 2)
    assert view(env, a)["unknown_reason"] is None
    act(env, a, "stop")
    v = view(env, a)
    assert v["state"] == "stopped" and v["risk_state"] == "UNKNOWN" and v["unknown_reason"] == "mg.unknown.stopped" and v["last_observed_risk"] is not None
    assert all("feed_closed" in z["causes"] for z in v["risk"].values())                                       # raw legacy field carries the closing cause
    assert view(env, b)["state"] == "playing" and view(env, b)["risk_state"] != "UNKNOWN"
    old = env.feeds.feeds[a].run_uid
    act(env, a, "start")
    v = view(env, a)
    assert env.feeds.feeds[a].run_uid != old and v["last_observed_risk"] is None                               # a restart inherits no history from the earlier run
    assert v["risk_state"] == "UNKNOWN" or v["observation"]["fresh"]                                          # unknown until a NEW sample exists
    act(env, a, "stop"); act(env, b, "stop")


def test_a_failed_feed_is_unknown_not_green_and_the_other_feed_keeps_its_state(fm, monkeypatch):
    env = fm({1: always(person=PERSON_IN), 2: always(person=PERSON_IN)}, frames=300, delay={1: 0.01, 2: 0.01})
    a, b = create(env, 1, loop=True), create(env, 2, loop=True)
    real = feeds_mod.Feed.process

    def boom(self, item, det):
        if self.fid == a and self.counters["analyzed"] >= 3:
            raise RuntimeError("injected")
        return real(self, item, det)
    monkeypatch.setattr(feeds_mod.Feed, "process", boom)
    act(env, a, "start"); act(env, b, "start")
    wait_state(env, [a], ("failed",))
    va = view(env, a)
    assert va["state"] == "failed" and va["risk_state"] == "UNKNOWN" and va["unknown_reason"] == "mg.unknown.failed" and va["risk_state"] != "GREEN"
    assert view(env, b)["state"] == "playing" and view(env, b)["unknown_reason"] is None
    act(env, b, "stop")


def test_a_playing_feed_whose_newest_sample_is_old_is_unknown_with_a_stale_reason(fm, monkeypatch):
    env = fm({1: always()}, frames=400, delay={1: 0.01})
    a = create(env, 1, loop=True)
    act(env, a, "start")
    _analysed(env, a)
    monkeypatch.setattr(feeds_mod, "STALE_OBSERVATION_S", 0.0)
    time.sleep(0.05)
    v = view(env, a)
    assert v["state"] == "playing" and v["risk_state"] == "UNKNOWN" and v["unknown_reason"] == "mg.unknown.stale" and v["observation"]["fresh"] is False
    assert v["last_observed_risk"] is not None
    act(env, a, "stop")


def test_a_fresh_playing_feed_whose_kernel_cannot_evaluate_says_so_instead_of_claiming_it_is_waiting(fm):
    env = fm({1: always()}, frames=400, delay={1: 0.01})
    a = create(env, 1, loop=True)
    act(env, a, "start")
    _analysed(env, a)
    f = env.feeds.feeds[a]
    with f.lock:                                              # RLock: the view is read in the same critical section, so the running feed cannot overwrite the injected state first
        f.risk_now = {z: {**s, "state": "UNKNOWN"} for z, s in f.risk_now.items()}
        v = f.view()
    assert v["state"] == "playing" and v["risk_state"] == "UNKNOWN" and v["unknown_reason"] == "mg.unknown.insufficient" and v["observation"]["fresh"] is True
    act(env, a, "stop")


@pytest.mark.parametrize("state", ["idle", "paused", "stopped", "ended", "failed", "weird"])
def test_no_non_playing_state_can_produce_a_current_risk(state):
    for last in ("GREEN", "YELLOW", "RED", "UNKNOWN"):
        assert feeds_mod.current_risk(state, last) == "UNKNOWN"
        assert feeds_mod.unknown_reason(state, True, 0.0) is not None
    assert feeds_mod.unknown_reason("playing", False, None) == "mg.unknown.no_sample_yet"
    assert feeds_mod.unknown_reason("playing", True, 0.1) is None


# ---------------------------------------------------------------- build identity / OAF record
SHA, OTHER = "a" * 40, "b" * 40


def _verdict(commit=SHA, **kw):
    """A SYNTHETIC, complete, COHERENT and correctly sealed verdict in the evaluator's exact output shape (never a real owner record): inputs bound to the contract and policy digests,
    conditions derived from the reasons the way the evaluator derives them, and the OWNER_AUTHORITY_NOT_ENFORCED reason an unenforced boundary always carries."""
    cdig, pdig = "sha256:" + "1" * 64, "sha256:" + "2" * 64
    d = {"schema": "oaf.verdict/1", "candidate_commit": commit, "verdict": "OWNER_DECISION_REQUIRED", "contract_id": "X-TEST", "contract_revision": 1, "contract_digest": cdig,
         "policy_digest": pdig, "release_accepted": False, "owner_authority": {"enforced": False, "source": "none"},
         "evaluator": {"name": "oaf-eval", "version": "0.1.0-candidate"},
         "inputs": [{"kind": "candidate", "digest": "sha256:" + "3" * 64}, {"kind": "contract", "digest": cdig}, {"kind": "policy", "digest": pdig}],
         "conditions": {f"C{i}": ("MET" if i >= 3 else "NOT_MET") for i in range(1, 11)},          # every listed gate passes (C8-C10 MET); frozen/admitted (C1, C2) are not
         "requirements": [{"id": "R-1", "status": "NOT_ADMITTED", "gate_results": [{"gate": "G-X", "state": "PASS"}], "open_findings": []}],
         "reasons": [{"rank": 2, "code": "OWNER_AUTHORITY_NOT_ENFORCED", "ref": "owner-authority"}, {"rank": 2, "code": "REQUIREMENT_NOT_ADMITTED", "ref": "R-1"}],
         "advisories": [], "owner_decisions_relied_on": []}
    d.update(kw)
    d.pop("digest", None)
    d["digest"] = buildinfo.verdict_digest(d)
    return d


def _passing(**kw):
    """A synthetic CONTRACT_PASS (all conditions met, no reasons, every requirement satisfied) under an ENFORCED boundary; accepted unless overridden."""
    base = {"verdict": "CONTRACT_PASS", "release_accepted": True, "reasons": [], "conditions": {f"C{i}": "MET" for i in range(1, 11)}, "owner_authority": {"enforced": True, "source": "synthetic-fixture"},
            "requirements": [{"id": "R-1", "status": "SATISFIED", "gate_results": [{"gate": "G-X", "state": "PASS"}], "open_findings": []}]}
    base.update(kw)
    if base["release_accepted"] is True and "owner_decisions_relied_on" not in base:         # the flag exists only through a relied-on FINAL_ACCEPTANCE decision that came in as an owner-decision input
        base["owner_decisions_relied_on"] = [{"decision_id": "D-FINAL", "kind": "FINAL_ACCEPTANCE", "source_ref": "synthetic-fixture"}]
        base["inputs"] = sorted(base.get("inputs", _verdict()["inputs"]) + [{"kind": "owner-decision", "digest": "sha256:" + "4" * 64}], key=lambda i: (i["kind"], i["digest"]))
    return _verdict(**base)


def _put(root, commit, doc, raw=None):
    p = root / "logs" / "oaf_vertical01" / "ledger" / "records" / commit
    p.mkdir(parents=True, exist_ok=True)
    (p / "VERDICT.json").write_text(raw if raw is not None else json.dumps(doc))
    return p / "VERDICT.json"


def test_the_local_canonical_digest_is_the_oaf_digest():
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from oaf.canon import digest_of
    d = _verdict()
    assert digest_of(d) == d["digest"] == buildinfo.verdict_digest(d)
    real = Path(__file__).resolve().parents[2] / "logs" / "oaf_vertical01"
    for p in list(real.glob("*/records/*/VERDICT.json"))[:3]:                  # real recorded verdicts (read-only) use the same canonical form
        doc = json.loads(p.read_text())
        assert digest_of(doc) == doc["digest"] == buildinfo.verdict_digest(doc)


def test_a_valid_non_accepted_record_is_shown_as_recorded_and_not_accepted(tmp_path):
    _put(tmp_path, SHA, _verdict())
    r = buildinfo.recorded_oaf(SHA, tmp_path)
    assert r["recorded"] is True and r["validated"] is True and r["release_accepted_claimed"] is False and r["acceptance"] == "not_accepted" and r["authority_verified_by_app"] is False
    assert r["verdict"] == "OWNER_DECISION_REQUIRED" and r["bound_to_commit"] == SHA


@pytest.mark.parametrize("value", ["false", "true", "False", 0, 1, None, "", [], {}, "no"])
def test_release_accepted_must_be_a_strict_boolean(tmp_path, value):
    _put(tmp_path, SHA, _verdict(release_accepted=value))
    r = buildinfo.recorded_oaf(SHA, tmp_path)
    assert r["recorded"] is False and r["invalid_why"] == "release_accepted_not_boolean" and "release_accepted" not in r


def test_a_missing_release_accepted_is_invalid(tmp_path):
    d = _verdict(); d.pop("release_accepted"); d["digest"] = buildinfo.verdict_digest(d)
    _put(tmp_path, SHA, d)
    assert buildinfo.recorded_oaf(SHA, tmp_path)["invalid_why"] == "release_accepted_not_boolean"


def test_a_forged_digest_or_a_tampered_field_is_refused(tmp_path):
    d = _verdict(); d["contract_id"] = "FORGED"                                  # edited after sealing
    _put(tmp_path, SHA, d)
    assert buildinfo.recorded_oaf(SHA, tmp_path)["invalid_why"] == "digest_mismatch"
    tmp2 = tmp_path / "t2"; d2 = _verdict(); d2["digest"] = "sha256:" + "0" * 64
    _put(tmp2, SHA, d2)
    assert buildinfo.recorded_oaf(SHA, tmp2)["invalid_why"] == "digest_mismatch"
    tmp3 = tmp_path / "t3"; d3 = _verdict(); d3["contract_digest"] = "sha256:XYZ"; d3["digest"] = buildinfo.verdict_digest(d3)
    _put(tmp3, SHA, d3)
    assert buildinfo.recorded_oaf(SHA, tmp3)["invalid_why"] == "digest_format"


def test_a_record_for_another_commit_is_refused(tmp_path):
    _put(tmp_path, SHA, _verdict(OTHER))
    assert buildinfo.recorded_oaf(SHA, tmp_path) == {"recorded": False, "reason": "invalid_record", "invalid_why": "wrong_commit"}


@pytest.mark.parametrize("extra", [{"verdict": "ACCEPTED"}, {"verdict": "contract_pass"}, {"verdict": 3}, {"contract_revision": True}, {"contract_revision": "1"}, {"contract_id": ""},
                                   {"contract_id": "../x"}, {"owner_authority": {"enforced": "yes", "source": "x"}}, {"owner_authority": "enforced"}, {"schema": "other/1"}])
def test_invalid_states_and_types_are_refused(tmp_path, extra):
    _put(tmp_path, SHA, _verdict(**extra))
    assert buildinfo.recorded_oaf(SHA, tmp_path)["recorded"] is False


def test_an_acceptance_flag_without_a_pass_under_an_enforced_boundary_is_self_contradictory(tmp_path, monkeypatch):
    _put(tmp_path, SHA, _verdict(release_accepted=True))                                                  # accepted, but verdict is OWNER_DECISION_REQUIRED
    assert buildinfo.recorded_oaf(SHA, tmp_path)["invalid_why"] in ("acceptance_without_pass_and_enforced_authority", "acceptance_not_derived")      # the shape check (no FINAL_ACCEPTANCE decision) and the authority check both refuse it
    t2 = tmp_path / "t2"; _put(t2, SHA, _passing(owner_authority={"enforced": False, "source": "none"}))        # PASS but authority not enforced
    assert buildinfo.recorded_oaf(SHA, t2)["recorded"] is False
    t3 = tmp_path / "t3"; _put(t3, SHA, _passing(release_accepted=False, owner_authority={"enforced": False, "source": "none"}))
    assert buildinfo.recorded_oaf(SHA, t3)["recorded"] is False
    # defence in depth (cycle 29): the shape check now refuses these records first; with it stubbed out the independent authority check must still refuse them
    monkeypatch.setattr(buildinfo, "strict_shape", lambda d: "")
    t4 = tmp_path / "t4"; _put(t4, SHA, _verdict(release_accepted=True))
    assert buildinfo.recorded_oaf(SHA, t4)["invalid_why"] == "acceptance_without_pass_and_enforced_authority"
    t5 = tmp_path / "t5"; _put(t5, SHA, _passing(release_accepted=True, owner_authority={"enforced": False, "source": "none"}))
    assert buildinfo.recorded_oaf(SHA, t5)["recorded"] is False


def test_a_synthetic_accepted_record_is_displayed_only_as_recorded_never_as_verified_authority(tmp_path):
    _put(tmp_path, SHA, _passing())
    r = buildinfo.recorded_oaf(SHA, tmp_path)
    assert r["recorded"] is True and r["release_accepted_claimed"] is True and r["acceptance"] == "claimed_accepted" and r["authority_verified_by_app"] is False


@pytest.mark.parametrize("raw", ["{not json", "[]", "null", '{"schema":"oaf.verdict/1","schema":"oaf.verdict/1"}', '{"release_accepted": NaN}', '{"a": 1.5}'])
def test_malformed_records_are_refused(tmp_path, raw):
    _put(tmp_path, SHA, None, raw=raw)
    assert buildinfo.recorded_oaf(SHA, tmp_path)["recorded"] is False


def test_a_record_that_is_not_valid_utf8_is_refused(tmp_path):
    p = tmp_path / "logs" / "oaf_vertical01" / "l" / "records" / SHA; p.mkdir(parents=True)
    (p / "VERDICT.json").write_bytes(b"\xff\xfe{")
    assert buildinfo.recorded_oaf(SHA, tmp_path)["recorded"] is False


def test_a_symlinked_or_oversized_record_is_not_read(tmp_path):
    f = _put(tmp_path, SHA, _verdict())
    real = tmp_path / "outside.json"; real.write_text(f.read_text()); f.unlink(); f.symlink_to(real)
    assert buildinfo.recorded_oaf(SHA, tmp_path)["recorded"] is False and buildinfo.recorded_oaf(SHA, tmp_path)["invalid_why"] == "symlink_or_unreadable_in_path"
    t2 = tmp_path / "t2"; g = _put(t2, SHA, None, raw=" " * (buildinfo.MAX_RECORD_BYTES + 10))
    assert buildinfo.recorded_oaf(SHA, t2)["invalid_why"] == "not_a_regular_file"


def test_unknown_or_malformed_commit_is_never_looked_up(tmp_path):
    assert buildinfo.recorded_oaf(None, tmp_path) == {"recorded": False, "reason": "commit_unknown"}
    assert buildinfo.recorded_oaf("../..", tmp_path) == {"recorded": False, "reason": "commit_unknown"}


def test_the_build_endpoint_reports_a_real_sha_or_unknown_and_nothing_secret(mk):
    env = mk([])
    d = env.c.get("/api/build").json()
    assert set(d) >= {"commit", "commit_known", "tree_clean", "measured_utc", "source", "oaf"}
    assert (d["commit"] is None) == (not d["commit_known"])
    assert d["commit"] is None or (len(d["commit"]) == 40 and set(d["commit"]) <= set("0123456789abcdef"))
    blob = json.dumps(d).lower()
    assert "token" not in blob and "/users/" not in blob and "password" not in blob
    assert d["oaf"]["recorded"] in (True, False) and (d["oaf"]["recorded"] is False or d["oaf"]["bound_to_commit"] == d["commit"])


# ---------------------------------------------------------------- MG-OAF-027: the COMPLETE verdict contract and a pinned, descriptor-based read
REQUIRED = ["advisories", "conditions", "evaluator", "inputs", "owner_decisions_relied_on", "reasons", "requirements"]


@pytest.mark.parametrize("member", REQUIRED)
def test_every_required_verdict_member_must_be_present(tmp_path, member):
    d = _verdict(); d.pop(member); d["digest"] = buildinfo.verdict_digest(d)               # re-sealed: the digest alone proves nothing
    _put(tmp_path, SHA, d)
    r = buildinfo.recorded_oaf(SHA, tmp_path)
    assert r["recorded"] is False and r["invalid_why"] == "members" and "verdict" not in r


def test_an_unknown_member_is_refused_even_when_the_digest_is_valid(tmp_path):
    d = _verdict(extra_member="x")
    _put(tmp_path, SHA, d)
    assert buildinfo.recorded_oaf(SHA, tmp_path)["invalid_why"] == "members"


@pytest.mark.parametrize("member,value", [("inputs", "x"), ("inputs", [1]), ("inputs", [{"kind": "contract"}]), ("inputs", [{"kind": "other", "digest": "sha256:" + "3" * 64}]),
                                          ("reasons", {}), ("reasons", [{"rank": 2, "code": "lower", "ref": "R"}]), ("reasons", [{"rank": "2", "code": "REQUIREMENT_NOT_ADMITTED", "ref": "R"}]),
                                          ("reasons", [{"rank": 9, "code": "REQUIREMENT_NOT_ADMITTED", "ref": "R"}]), ("advisories", [{"code": "A"}]), ("advisories", None),
                                          ("conditions", {"C1": "MET"}), ("conditions", {**{f"C{i}": "NOT_MET" for i in range(1, 11)}, "C1": "maybe"}),
                                          ("evaluator", {"name": "x"}), ("evaluator", []), ("requirements", [{"id": "R"}]), ("requirements", [{"id": "R", "status": "OK", "gate_results": [], "open_findings": []}]),
                                          ("owner_decisions_relied_on", [{"decision_id": "d"}])])
def test_wrong_shaped_verdict_members_are_refused(tmp_path, member, value):
    _put(tmp_path, SHA, _verdict(**{member: value}))
    r = buildinfo.recorded_oaf(SHA, tmp_path)
    assert r["recorded"] is False and r["reason"] == "invalid_record"


def test_the_verdict_must_follow_from_its_own_reasons(tmp_path):
    _put(tmp_path, SHA, _verdict(verdict="RESOURCE_BLOCKED"))                              # reasons say rank 2 (OWNER_DECISION_REQUIRED)
    assert buildinfo.recorded_oaf(SHA, tmp_path)["invalid_why"] == "verdict_not_derived_from_reasons"
    t2 = tmp_path / "t2"; _put(t2, SHA, _passing(conditions={**{f"C{i}": "MET" for i in range(1, 11)}, "C4": "NOT_MET"}))
    assert buildinfo.recorded_oaf(SHA, t2)["invalid_why"] == "pass_with_unmet_condition"
    t3 = tmp_path / "t3"; _put(t3, SHA, _verdict(verdict="CONTRACT_PASS", release_accepted=False))     # PASS with reasons listed
    assert buildinfo.recorded_oaf(SHA, t3)["recorded"] is False


def test_the_complete_synthetic_passing_record_is_the_only_accepted_shape(tmp_path):
    _put(tmp_path, SHA, _passing())
    r = buildinfo.recorded_oaf(SHA, tmp_path)
    assert r["recorded"] is True and r["acceptance"] == "claimed_accepted"


@pytest.mark.parametrize("link_at", ["logs", "oaf_vertical01", "ledger", "records", "commit"])
def test_a_symlink_anywhere_in_the_hierarchy_is_never_followed(tmp_path, link_at):
    real = tmp_path / "elsewhere"; _put(real, SHA, _passing())                                # a perfectly valid record, outside the owned hierarchy
    src_dir = real / "logs" / "oaf_vertical01" / "ledger" / "records" / SHA
    root = tmp_path / "root"; root.mkdir()
    chain = {"logs": root / "logs", "oaf_vertical01": root / "logs" / "oaf_vertical01", "ledger": root / "logs" / "oaf_vertical01" / "ledger",
             "records": root / "logs" / "oaf_vertical01" / "ledger" / "records", "commit": root / "logs" / "oaf_vertical01" / "ledger" / "records" / SHA}
    targets = {"logs": real / "logs", "oaf_vertical01": real / "logs" / "oaf_vertical01", "ledger": real / "logs" / "oaf_vertical01" / "ledger",
               "records": real / "logs" / "oaf_vertical01" / "ledger" / "records", "commit": src_dir}
    order = ["logs", "oaf_vertical01", "ledger", "records", "commit"]
    for name in order[: order.index(link_at)]:
        chain[name].mkdir(exist_ok=True)
    chain[link_at].symlink_to(targets[link_at])
    r = buildinfo.recorded_oaf(SHA, root)
    assert r["recorded"] is False and "verdict" not in r


def test_the_record_is_read_from_the_opened_descriptor_not_from_the_path_again(tmp_path):
    f = _put(tmp_path, SHA, _verdict())
    forged = tmp_path / "forged.json"; forged.write_text(json.dumps(_passing()))
    def swap():                                                                          # the path is replaced AFTER the descriptor was opened
        f.unlink(); f.symlink_to(forged)
    buildinfo._AFTER_OPEN = swap
    try:
        r = buildinfo.recorded_oaf(SHA, tmp_path)
    finally:
        buildinfo._AFTER_OPEN = None
    assert r["recorded"] is True and r["release_accepted_claimed"] is False and r["acceptance"] == "not_accepted"      # the original (non-accepted) bytes, never the forged accepted ones
