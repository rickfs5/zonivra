"""MG-OAF-028 (cycle-28 review): a hand-authored document must not be promoted to a recorded evaluator verdict. The validator checks the evaluator's CLOSED vocabularies, the rank of
every code, canonical ordering, evaluator identity, coherence of conditions / requirements / reasons, and NFC. All records here are SYNTHETIC; none is an owner record, and a
valid evaluator record is still shown only as «registrado» (the application does not verify owner authority)."""
import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mineguard import buildinfo  # noqa: E402
from oaf import VERSION as OAF_VERSION  # noqa: E402
from oaf import catalogue  # noqa: E402
from oaf.evaluate import evaluate  # noqa: E402
from oaf.tests.fixtures import C1, World, decision, item  # noqa: E402

from test_vertical04_state import SHA, _passing, _put, _verdict  # noqa: E402
from test_vs02_webcam_risk import mk  # noqa: E402,F401


def sealed(d):
    d = copy.deepcopy(d)
    d.pop("digest", None)
    d["digest"] = buildinfo.verdict_digest(d)
    return d


def why(tmp_path, doc, commit=SHA):
    _put(tmp_path, commit, doc)
    r = buildinfo.recorded_oaf(commit, tmp_path)
    return r.get("invalid_why") if r["recorded"] is False else "ACCEPTED_AS_RECORDED"


# ---------------------------------------------------------------- the review's counterexample, and the closed vocabularies
def test_the_original_forged_rank_six_reason_no_longer_produces_a_recorded_pass(tmp_path):
    forged = _passing(reasons=[{"rank": 6, "code": "MADE_UP", "ref": "x"}], advisories=[{"code": "FABRICATED", "ref": "y"}])      # all conditions MET, digest recomputed
    r = why(tmp_path, forged)
    assert r == "reasons"
    out = buildinfo.recorded_oaf(SHA, tmp_path)
    assert out == {"recorded": False, "reason": "invalid_record", "invalid_why": "reasons"} and "release_accepted" not in out and "verdict" not in out


@pytest.mark.parametrize("reason", [
    {"rank": 6, "code": "MADE_UP", "ref": "x"}, {"rank": 6, "code": "CONTRACT_PASS", "ref": "x"}, {"rank": 0, "code": "OPEN_MAJOR", "ref": "x"}, {"rank": 7, "code": "OPEN_MAJOR", "ref": "x"},
    {"rank": 2, "code": "MADE_UP", "ref": "x"}, {"rank": 5, "code": "OPEN_MAJOR", "ref": "x"},                                # a real code under the wrong rank
    {"rank": True, "code": "OPEN_MAJOR", "ref": "x"}, {"rank": "1", "code": "OPEN_MAJOR", "ref": "x"}, {"rank": 1, "code": "open_major", "ref": "x"},
    {"rank": 1, "code": "OPEN_MAJOR", "ref": ""}, {"rank": 1, "code": "OPEN_MAJOR"}, {"rank": 1, "code": "OPEN_MAJOR", "ref": "x", "detail": "y"},
])
def test_every_reason_must_be_a_catalogue_code_under_its_own_rank(tmp_path, reason):
    assert why(tmp_path, _verdict(reasons=[reason], verdict="CONTRACT_FAIL")) == "reasons"


@pytest.mark.parametrize("adv", [{"code": "MADE_UP", "ref": "x"}, {"code": "OPEN_MAJOR", "ref": "x"}, {"code": "HYPOTHESIS_ADVISORY", "ref": ""}, {"code": "HYPOTHESIS_ADVISORY"}])
def test_every_advisory_must_be_a_catalogue_advisory(tmp_path, adv):
    assert why(tmp_path, _verdict(advisories=[adv])) == "advisories"


def test_a_pass_cannot_carry_any_reason_and_a_fail_cannot_claim_pass(tmp_path):
    real = {"rank": 1, "code": "OPEN_MAJOR", "ref": "finding:F-1"}
    assert why(tmp_path, _passing(reasons=[real])) == "verdict_not_derived_from_reasons"
    assert why(tmp_path / "b", _verdict(reasons=[real], verdict="CONTRACT_PASS")) in ("verdict_not_derived_from_reasons", "pass_with_unmet_condition")


def test_reasons_advisories_and_decisions_must_be_unique_and_canonically_sorted(tmp_path):
    a, b = {"rank": 2, "code": "OWNER_AUTHORITY_NOT_ENFORCED", "ref": "owner-authority"}, {"rank": 2, "code": "REQUIREMENT_NOT_ADMITTED", "ref": "R-1"}
    assert why(tmp_path, _verdict(reasons=[b, a])) == "reasons_order"
    assert why(tmp_path / "b", _verdict(reasons=[a, a, b])) == "reasons_order"
    adv = [{"code": "IMPROVEMENT_RECORDED", "ref": "a"}, {"code": "HYPOTHESIS_ADVISORY", "ref": "a"}]
    assert why(tmp_path / "c", _verdict(advisories=adv)) == "advisories_order"
    assert why(tmp_path / "d", _verdict(advisories=[adv[1], adv[1]])) == "advisories_order"
    d1, d2 = {"decision_id": "D-1", "kind": "APPROVE_SCOPE", "source_ref": "s"}, {"decision_id": "D-2", "kind": "APPROVE_SCOPE", "source_ref": "s"}
    ins = sorted(_verdict()["inputs"] + [{"kind": "owner-decision", "digest": "sha256:" + "5" * 64}, {"kind": "owner-decision", "digest": "sha256:" + "6" * 64}], key=lambda i: (i["kind"], i["digest"]))
    fin = {"decision_id": "D-3", "kind": "FINAL_ACCEPTANCE", "source_ref": "s"}
    assert why(tmp_path / "e", _passing(owner_decisions_relied_on=[d2, d1, fin], inputs=ins)) == "owner_decisions_order"
    assert why(tmp_path / "f", _passing(owner_decisions_relied_on=[d1, d2, fin], inputs=sorted(ins + [{"kind": "owner-decision", "digest": "sha256:" + "7" * 64}], key=lambda i: (i["kind"], i["digest"])))) == "ACCEPTED_AS_RECORDED"


@pytest.mark.parametrize("ev", [{"name": "oaf-eval", "version": "9.9.9"}, {"name": "other", "version": "0.1.0-candidate"}, {"name": "oaf-eval"}, {"name": "oaf-eval", "version": ""}, "oaf-eval", None])
def test_only_the_known_evaluator_identity_is_accepted(tmp_path, ev):
    assert why(tmp_path, _verdict(evaluator=ev)) == "evaluator"


def test_inputs_must_be_sorted_bound_to_the_contract_and_policy_and_have_one_candidate(tmp_path):
    d = _verdict()
    assert why(tmp_path, _verdict(inputs=list(reversed(d["inputs"])))) == "inputs"
    assert why(tmp_path / "a", _verdict(inputs=[i for i in d["inputs"] if i["kind"] != "candidate"])) == "inputs"
    assert why(tmp_path / "b", _verdict(inputs=d["inputs"] + [d["inputs"][0]])) == "inputs"
    assert why(tmp_path / "c", _verdict(contract_digest="sha256:" + "9" * 64)) == "inputs_not_bound_to_contract_or_policy"
    assert why(tmp_path / "d", _verdict(policy_digest="sha256:" + "9" * 64)) == "inputs_not_bound_to_contract_or_policy"


def test_invalid_gate_states_and_requirement_statuses_are_refused(tmp_path):
    req = lambda **kw: [{"id": "R-1", "status": "NOT_ADMITTED", "gate_results": [{"gate": "G-X", "state": "PASS"}], "open_findings": [], **kw}]
    assert why(tmp_path, _verdict(requirements=req(gate_results=[{"gate": "G-X", "state": "GREENISH"}]))) == "requirements"
    assert why(tmp_path / "a", _verdict(requirements=req(status="DONE"))) == "requirements"
    assert why(tmp_path / "b", _verdict(requirements=req(gate_results=[{"gate": "G-Y", "state": "PASS"}, {"gate": "G-X", "state": "PASS"}]))) == "requirements"
    assert why(tmp_path / "c", _verdict(requirements=req(status="SATISFIED", gate_results=[{"gate": "G-X", "state": "FAILED"}]))) == "requirements"      # SATISFIED over a failed gate
    assert why(tmp_path / "d", _verdict(requirements=req(status="VIOLATED"))) == "requirements"                                                           # no REQUIREMENT_VIOLATED reason
    assert why(tmp_path / "e", _verdict(requirements=[req()[0], {**req()[0], "id": "A-0"}])) == "requirements_order"


@pytest.mark.parametrize("cond", ["C4", "C5", "C6", "C7"])
def test_conditions_must_follow_from_the_reasons(tmp_path, cond):
    d = _verdict()
    bad = {**d["conditions"], cond: "NOT_MET"}                                  # claims an open blocker/major/hold/minor that no reason lists
    assert why(tmp_path, _verdict(conditions=bad)) == "conditions_incoherent"
    code = {"C4": "OPEN_BLOCKER", "C5": "OPEN_MAJOR", "C6": "SECURITY_HOLD_OPEN", "C7": "MINOR_NOT_ACCEPTED"}[cond]
    rank = {"C4": 1, "C5": 1, "C6": 2, "C7": 5}[cond]
    reasons = sorted(d["reasons"] + [{"rank": rank, "code": code, "ref": "x"}], key=lambda r: (r["rank"], r["code"], r["ref"]))
    assert why(tmp_path / "b", _verdict(reasons=reasons, verdict="CONTRACT_FAIL" if rank == 1 else "OWNER_DECISION_REQUIRED" if rank == 2 else "OWNER_DECISION_REQUIRED")) == "conditions_incoherent"   # reason present but condition MET


def test_the_authority_state_and_its_reason_must_agree(tmp_path):
    d = _verdict()
    assert why(tmp_path, _verdict(reasons=[r for r in d["reasons"] if r["code"] != "OWNER_AUTHORITY_NOT_ENFORCED"])) == "authority_reason_incoherent"
    assert why(tmp_path / "b", _verdict(owner_authority={"enforced": True, "source": "x"})) == "authority_reason_incoherent"
    assert why(tmp_path / "c", _verdict(owner_decisions_relied_on=[{"decision_id": "D-1", "kind": "APPROVE_SCOPE", "source_ref": "s"}])) == "decisions_relied_on_without_authority"


def test_a_string_that_is_not_nfc_is_refused(tmp_path):
    d = _verdict()
    d["reasons"][1]["ref"] = "R-e\u0301"
    d["digest"] = "sha256:" + "0" * 64                                              # cannot even be sealed: the canonical form refuses a non-NFC string
    assert why(tmp_path, d) == "malformed"                                          # 'e' + combining accent


def test_a_recomputed_digest_is_not_proof_and_the_unknown_member_stays_refused(tmp_path):
    assert why(tmp_path, sealed({**_verdict(), "extra": 1})) == "members"


# ---------------------------------------------------------------- the validator accepts what the REAL evaluator emits (no false rejection) and agrees with the OAF module
def test_the_duplicated_vocabularies_equal_the_oaf_catalogue():
    assert buildinfo.REASONS == catalogue.REASONS and buildinfo.ADVISORIES == catalogue.ADVISORIES and buildinfo.VERDICTS == catalogue.VERDICTS
    assert buildinfo.EVALUATOR == {"name": "oaf-eval", "versions": (OAF_VERSION,)}


def _real(w):
    recs, auth, _ = w.build()
    return evaluate(recs, auth)


def test_real_evaluator_outputs_of_every_kind_validate(tmp_path):
    outs = []
    w = World()
    outs.append(_real(w))                                                          # an ENFORCED PASS
    w = World(); w.items = [x for x in w.items if x["gate_id"] != "G1"]; outs.append(_real(w))                       # missing evidence
    w = World(); w.authority_enforced = False; outs.append(_real(w))               # unenforced authority
    w = World(); w.skip_freeze = True; outs.append(_real(w))                       # contract not frozen
    w = World(); w.complete_g4 = False; outs.append(_real(w))                      # manual gate pending
    w = World(); w.items = [x if x["gate_id"] != "G1" else item("G1", "FAIL") for x in w.items]; outs.append(_real(w))     # a failed gate
    w = World(); w.bundle_commit = "f" * 40; outs.append(_real(w))                 # evidence for another commit
    w = World(); w.extra_decisions.append(decision("D-FA", "FINAL_ACCEPTANCE", ["CAND1"])); outs.append(_real(w))     # a REAL accepted output (synthetic owner decision under a synthetic enforced boundary)
    assert outs[-1]["release_accepted"] is True and outs[-1]["owner_decisions_relied_on"]
    w = World(); w.extra_decisions.append(decision("D-FA", "FINAL_ACCEPTANCE", ["CAND1"])); w.complete_g4 = False; outs.append(_real(w))      # final acceptance without eligibility
    assert outs[-1]["release_accepted"] is False
    assert {o["verdict"] for o in outs} >= {"CONTRACT_PASS", "CONTRACT_FAIL", "OWNER_DECISION_REQUIRED", "INCOMPLETE_EVIDENCE"}
    for i, o in enumerate(outs):
        view, problem = buildinfo.validate_verdict(json.dumps(o).encode(), C1)
        assert problem == "" and view and view["verdict"] == o["verdict"], (i, problem, o["reasons"])


def test_every_recorded_ledger_verdict_in_this_checkout_validates():
    real = Path(__file__).resolve().parents[2] / "logs" / "oaf_vertical01"
    docs = sorted(real.glob("*/records/*/VERDICT.json"))
    if not docs:
        pytest.skip("no recorded ledger verdicts in this checkout")
    for p in docs:
        doc = json.loads(p.read_text())
        view, problem = buildinfo.validate_verdict(p.read_bytes(), doc["candidate_commit"])
        assert problem == "", (str(p.relative_to(real)), problem)


# ---------------------------------------------------------------- API level
def test_the_build_endpoint_never_reports_the_forged_record_as_accepted(mk, tmp_path, monkeypatch):
    forged = _passing(reasons=[{"rank": 6, "code": "MADE_UP", "ref": "x"}])
    _put(tmp_path, SHA, forged)
    orig = buildinfo.recorded_oaf
    monkeypatch.setattr(buildinfo, "started_commit", lambda: SHA)
    monkeypatch.setattr(buildinfo, "recorded_oaf", lambda commit, root=None: orig(commit, tmp_path))
    env = mk([])
    d = env.c.get("/api/build").json()["oaf"]
    assert d == {"recorded": False, "reason": "invalid_record", "invalid_why": "reasons"}
    assert "release_accepted" not in d and "acceptance" not in d


# ---------------------------------------------------------------- MG-OAF-029: the acceptance flag exists only through a relied-on FINAL_ACCEPTANCE decision; gate conditions follow the listed gates
def test_review_counterexample_an_accepted_record_without_a_final_acceptance_decision_is_refused(tmp_path):
    bare = _passing()
    bare["owner_decisions_relied_on"] = []
    bare["inputs"] = [i for i in bare["inputs"] if i["kind"] != "owner-decision"]
    assert why(tmp_path, bare) == "acceptance_not_derived"                                                       # release_accepted=true, every condition MET, digest recomputed: no evaluator emits this
    out = buildinfo.recorded_oaf(SHA, tmp_path)
    assert out["recorded"] is False and "release_accepted" not in out


def test_the_complete_evaluator_shaped_acceptance_is_still_only_registered_never_verified(tmp_path):
    _put(tmp_path, SHA, _passing())
    r = buildinfo.recorded_oaf(SHA, tmp_path)
    assert r["recorded"] is True and r["acceptance"] == "claimed_accepted" and r["authority_verified_by_app"] is False


def test_a_final_acceptance_on_a_pass_must_carry_the_flag_and_a_decision_needs_an_input(tmp_path):
    ok = _passing()
    assert why(tmp_path, {**ok, "release_accepted": False}) == "acceptance_not_derived"                         # FINAL_ACCEPTANCE relied on a PASS, flag false
    no_input = _passing()
    no_input["inputs"] = [i for i in no_input["inputs"] if i["kind"] != "owner-decision"]
    assert why(tmp_path / "b", no_input) == "decisions_without_inputs"


def test_an_unaccepted_pass_without_a_final_acceptance_decision_is_the_only_unflagged_pass(tmp_path):
    assert why(tmp_path, _passing(release_accepted=False, owner_decisions_relied_on=[])) == "ACCEPTED_AS_RECORDED"       # a PASS the owner has not finally accepted: recorded, flag false


@pytest.mark.parametrize("cond", ["C8", "C9", "C10"])
def test_gate_conditions_cannot_be_falsely_unmet_when_every_listed_gate_passes(tmp_path, cond):
    d = _verdict()
    assert why(tmp_path, _verdict(conditions={**d["conditions"], cond: "NOT_MET"})) == "conditions_incoherent"


def test_gate_conditions_can_be_unmet_through_a_failing_gate_or_a_contested_finding_only(tmp_path):
    d = _verdict()
    failing = [{"id": "R-1", "status": "NOT_ADMITTED", "gate_results": [{"gate": "G-X", "state": "FAILED"}], "open_findings": []}]
    unmet = {**d["conditions"], "C8": "NOT_MET", "C9": "NOT_MET", "C10": "NOT_MET"}
    assert why(tmp_path, _verdict(requirements=failing, conditions=unmet)) == "requirements"                   # a failed gate under a requirement that is not VIOLATED
    ok_failing = _verdict(requirements=[{**failing[0], "status": "VIOLATED"}], conditions={**unmet, "C2": "NOT_MET"},
                          reasons=sorted(d["reasons"] + [{"rank": 1, "code": "REQUIREMENT_VIOLATED", "ref": "R-1"}], key=lambda r: (r["rank"], r["code"], r["ref"])), verdict="CONTRACT_FAIL")
    assert why(tmp_path / "b", ok_failing) == "ACCEPTED_AS_RECORDED"
    contested = sorted(d["reasons"] + [{"rank": 2, "code": "UNADJUDICATED_MATERIAL_FINDING", "ref": "F-1"}], key=lambda r: (r["rank"], r["code"], r["ref"]))
    assert why(tmp_path / "c", _verdict(reasons=contested, conditions={**d["conditions"], "C8": "NOT_MET"})) == "ACCEPTED_AS_RECORDED"
    assert why(tmp_path / "d", _verdict(reasons=contested)) == "conditions_incoherent"                                    # contested finding listed but C8 MET


def test_c1_follows_the_freeze_and_the_authority_in_both_directions(tmp_path):
    d = _verdict()
    assert why(tmp_path, _verdict(conditions={**d["conditions"], "C1": "MET"})) == "conditions_incoherent"      # unenforced authority cannot meet C1
    p = _passing()
    assert why(tmp_path / "b", {**p, "conditions": {**p["conditions"], "C1": "NOT_MET"}}) in ("conditions_incoherent", "pass_with_unmet_condition")      # enforced, frozen, PASS: C1 cannot be unmet


# ---------------------------------------------------------------- MG-OAF-030 (cycle-30 review): output-only validation is never projected as acceptance or as enforced authority
def test_review_counterexample_a_fabricated_final_acceptance_is_only_a_claim_the_application_cannot_confirm(tmp_path):
    d = _passing()                                                      # three owner-decision inputs, none of them a FINAL_ACCEPTANCE: the output alone cannot show that
    d["inputs"] = sorted([i for i in d["inputs"] if i["kind"] != "owner-decision"] + [{"kind": "owner-decision", "digest": "sha256:" + c * 64} for c in "789"], key=lambda i: (i["kind"], i["digest"]))
    _put(tmp_path, SHA, sealed(d))
    r = buildinfo.recorded_oaf(SHA, tmp_path)
    assert r["recorded"] is True and r["acceptance"] == "claimed_accepted" and r["authority_verified_by_app"] is False
    assert "release_accepted" not in r and "owner_authority_enforced" not in r and r["release_accepted_claimed"] is True and r["owner_authority_claimed_enforced"] is True
    assert "CLAIMS" in r["note"] and "verifies neither" in r["note"]
