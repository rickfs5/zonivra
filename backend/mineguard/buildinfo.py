"""Build identity and recorded OAF state for the developer panel (MG-PRODUCT-VERTICAL-04).

Nothing here is invented: the commit is what `git` reports for the checkout this process was STARTED from (captured once), the working-tree flag is re-measured per request,
and the OAF verdict is shown only when a recorded verdict file names exactly that commit. Anything else is reported as unknown / not recorded."""
import hashlib
import json
import os
import re
import stat
import subprocess
import time
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_STARTED: dict | None = None


def _git(*args: str) -> str | None:
    try:
        r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def _is_sha(s: str | None) -> bool:
    return bool(s) and len(s) == 40 and all(c in "0123456789abcdef" for c in s)


def started_commit() -> str | None:
    global _STARTED
    if _STARTED is None:
        sha = _git("rev-parse", "HEAD")
        _STARTED = {"commit": sha if _is_sha(sha) else None}
    return _STARTED["commit"]


VERDICTS = ("CONTRACT_FAIL", "OWNER_DECISION_REQUIRED", "RESOURCE_BLOCKED", "INCOMPLETE_EVIDENCE", "OWNER_ACCEPTANCE_PENDING", "CONTRACT_PASS")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
MAX_RECORD_BYTES = 262_144


def _no_dupes(pairs):
    d = {}
    for k, v in pairs:
        if k in d:
            raise ValueError("duplicate member")
        d[k] = v
    return d


def _no_const(name):
    raise ValueError("non-finite constant")


def _canon(v) -> bytes:
    """Canonical bytes exactly as oaf.canon.canonical_bytes (sorted keys, compact separators, UTF-8, no floats, no NaN); equivalence is checked by a test against the OAF module."""
    def chk(x):
        if isinstance(x, dict):
            for k, y in x.items():
                if not isinstance(k, str):
                    raise ValueError("key")
                chk(y)
        elif isinstance(x, list):
            for y in x:
                chk(y)
        elif isinstance(x, str):
            if unicodedata.normalize("NFC", x) != x:
                raise ValueError("not NFC")
        elif isinstance(x, float) or not (x is None or isinstance(x, (int, bool))):
            raise ValueError("type")
    chk(v)
    return json.dumps(v, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def verdict_digest(doc: dict) -> str:
    return "sha256:" + hashlib.sha256(_canon({k: x for k, x in doc.items() if k != "digest"})).hexdigest()


def _isint(x) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


# The evaluator's closed vocabularies (oaf.catalogue, OAF-EVALUATOR-001 section 6), duplicated so the application does not import the OAF package. A test compares every
# constant with the OAF module, so a drift in either direction fails (MG-OAF-028).
REASONS = {
    1: ("OPEN_BLOCKER", "OPEN_MAJOR", "GATE_FAILED", "GATE_ERROR", "REQUIREMENT_VIOLATED", "INTEGRITY_ARTIFACT_DIGEST", "INTEGRITY_SELF_CONTRADICTION", "INTEGRITY_THRESHOLD_MISMATCH"),
    2: ("OWNER_AUTHORITY_NOT_ENFORCED", "CONTRACT_NOT_FROZEN", "REQUIREMENT_NOT_ADMITTED", "SECURITY_HOLD_OPEN", "CONTESTED_MATERIAL_FINDING", "UNADJUDICATED_MATERIAL_FINDING"),
    3: ("GATE_RESOURCE_BLOCKED",),
    4: ("GATE_NOT_RUN", "GATE_EVIDENCE_MISSING", "EVIDENCE_WRONG_COMMIT", "EVIDENCE_WRONG_CONTRACT", "REQUIREMENT_UNCOVERED", "ARTIFACT_UNVERIFIED", "CANDIDATE_DIRTY"),
    5: ("MINOR_NOT_ACCEPTED", "MANUAL_GATE_PENDING", "LIMITATION_NOT_ACCEPTED"),
}
ADVISORIES = ("HYPOTHESIS_ADVISORY", "IMPROVEMENT_RECORDED", "OUT_OF_SCOPE_RECORDED", "PROPOSAL_NOT_ADMITTED", "STALE_FINDING_HISTORY", "REVISION_AFTER_FINDINGS",
              "FINAL_ACCEPTANCE_WITHOUT_ELIGIBILITY", "LIMITATION_RELIED_ON", "ADJUDICATION_IGNORED", "OWNER_DECISION_NOT_BOUND", "OWNER_RECORD_UNLISTED", "WAIVER_NOT_PERMITTED",
              "LIMITATION_NOT_PERMITTED", "SEVERITY_LOWERED_BY_OWNER", "HYPOTHESIS_DISMISSED_BY_OWNER", "MINOR_ACCEPTED_BY_OWNER")
EVALUATOR = {"name": "oaf-eval", "versions": ("0.1.0-candidate",)}
_GATE_STATES = {"PASS", "WAIVED", "PENDING", "MISSING", "NOT_RUN", "FAILED", "ERROR", "BLOCKED"}
_RANK_OF = {c: r for r, cs in REASONS.items() for c in cs}
_TOP = {"advisories", "candidate_commit", "conditions", "contract_digest", "contract_id", "contract_revision", "digest", "evaluator", "inputs", "owner_authority", "owner_decisions_relied_on",
        "policy_digest", "reasons", "release_accepted", "requirements", "schema", "verdict"}
_KINDS = {"contract", "candidate", "evidence", "finding", "adjudication", "owner-decision", "policy"}
_REQ_STATUS = {"NOT_EVALUATED", "UNCOVERED", "VIOLATED", "NOT_ADMITTED", "SATISFIED", "PENDING"}
_CODE = re.compile(r"[A-Z][A-Z0-9_]{1,59}")
MAX_ITEMS = 5000


def _s(x, n=200) -> bool:
    return isinstance(x, str) and 0 < len(x) <= n


def _lst(x) -> bool:
    return isinstance(x, list) and len(x) <= MAX_ITEMS


def _exact(x, keys) -> bool:
    return isinstance(x, dict) and set(x) == set(keys)


def strict_shape(d: dict) -> str:
    """Exact, recursive shape of the evaluator's verdict output (oaf.evaluate): every member must be present, none may be added, and each has its exact type. Returns "" or a reason code."""
    if set(d) != _TOP:
        return "members"
    if not (_exact(d["evaluator"], ("name", "version")) and d["evaluator"]["name"] == EVALUATOR["name"] and d["evaluator"]["version"] in EVALUATOR["versions"]):
        return "evaluator"
    if not _lst(d["inputs"]) or not all(_exact(i, ("kind", "digest")) and i["kind"] in _KINDS and isinstance(i["digest"], str) and _DIGEST.fullmatch(i["digest"]) for i in d["inputs"]):
        return "inputs"
    ik = [(i["kind"], i["digest"]) for i in d["inputs"]]
    kinds = [k for k, _ in ik]
    if ik != sorted(set(ik)) or kinds.count("candidate") != 1 or kinds.count("policy") != 1 or "contract" not in kinds:
        return "inputs"                                                    # the evaluator requires exactly one candidate and one policy, at least one contract; sorted by (kind, digest)
    if ("contract", d["contract_digest"]) not in ik or ("policy", d["policy_digest"]) not in ik:
        return "inputs_not_bound_to_contract_or_policy"
    cond = d["conditions"]
    if not (isinstance(cond, dict) and set(cond) == {f"C{i}" for i in range(1, 11)} and all(v in ("MET", "NOT_MET") for v in cond.values())):
        return "conditions"
    if not _lst(d["requirements"]):
        return "requirements"
    for r in d["requirements"]:
        if not (_exact(r, ("id", "status", "gate_results", "open_findings")) and _s(r["id"], 80) and r["status"] in _REQ_STATUS and _lst(r["gate_results"])
                and all(_exact(g, ("gate", "state")) and _s(g["gate"], 80) and isinstance(g["state"], str) and _CODE.fullmatch(g["state"]) for g in r["gate_results"])
                and _lst(r["open_findings"]) and all(_s(f, 120) for f in r["open_findings"])):
            return "requirements"
    rs = d["reasons"]
    if not _lst(rs) or not all(_exact(r, ("rank", "code", "ref")) and _isint(r["rank"]) and r["rank"] in REASONS and isinstance(r["code"], str) and r["code"] in REASONS[r["rank"]]
                               and _s(r["ref"], 200) for r in rs):
        return "reasons"                                                   # an unknown code, a code under another rank, or a rank no evaluator emits (6 = CONTRACT_PASS) is refused
    keys = [(r["rank"], r["code"], r["ref"]) for r in rs]
    if keys != sorted(set(keys)):
        return "reasons_order"                                             # the evaluator emits unique reasons sorted by (rank, code, ref)
    if not _lst(d["advisories"]) or not all(_exact(a, ("code", "ref")) and isinstance(a["code"], str) and a["code"] in ADVISORIES and _s(a["ref"], 200) for a in d["advisories"]):
        return "advisories"
    akeys = [(a["code"], a["ref"]) for a in d["advisories"]]
    if akeys != sorted(set(akeys)):
        return "advisories_order"
    if not _lst(d["owner_decisions_relied_on"]) or not all(_exact(o, ("decision_id", "kind", "source_ref")) and _s(o["decision_id"], 120) and _s(o["kind"], 60) and _s(o["source_ref"], 200) for o in d["owner_decisions_relied_on"]):
        return "owner_decisions"
    ids = [o["decision_id"] for o in d["owner_decisions_relied_on"]]
    if ids != sorted(set(ids)):
        return "owner_decisions_order"
    auth = d["owner_authority"]["enforced"]
    if not auth and ids:
        return "decisions_relied_on_without_authority"                     # no owner record counts unless the boundary is enforced
    expected = VERDICTS[min(r["rank"] for r in rs) - 1] if rs else "CONTRACT_PASS"
    if d["verdict"] != expected:
        return "verdict_not_derived_from_reasons"                 # the verdict must be exactly what the listed reasons imply
    if d["verdict"] == "CONTRACT_PASS" and any(v != "MET" for v in cond.values()):
        return "pass_with_unmet_condition"
    codes = {r["code"] for r in rs}
    refs = {(r["code"], r["ref"]) for r in rs}
    if (not auth) != ("OWNER_AUTHORITY_NOT_ENFORCED" in codes):
        return "authority_reason_incoherent"
    # requirements: ordering and the status each one can have given its gates, findings and reasons
    rids = [r["id"] for r in d["requirements"]]
    if rids != sorted(set(rids)):
        return "requirements_order"
    for r in d["requirements"]:
        gs = [g["gate"] for g in r["gate_results"]]
        if gs != sorted(set(gs)) or r["open_findings"] != sorted(set(r["open_findings"])) or not all(g["state"] in _GATE_STATES for g in r["gate_results"]):
            return "requirements"
        st = r["status"]
        if (st == "NOT_EVALUATED" and (gs or r["open_findings"])) or (st == "UNCOVERED" and gs):
            return "requirements"
        if st == "SATISFIED" and (r["open_findings"] or any(g["state"] not in ("PASS", "WAIVED") for g in r["gate_results"]) or not gs):
            return "requirements"
        if any(g["state"] in ("FAILED", "ERROR") for g in r["gate_results"]) and st != "VIOLATED":
            return "requirements"                                          # a failed or errored gate makes the requirement VIOLATED (oaf.evaluate)
        if (st == "VIOLATED") != (("REQUIREMENT_VIOLATED", r["id"]) in refs) or (st == "UNCOVERED") != (("REQUIREMENT_UNCOVERED", r["id"]) in refs):
            return "requirements"
    # conditions follow from the reasons exactly as the evaluator derives them
    want = {"C2": all(r["status"] in ("SATISFIED", "NOT_EVALUATED") for r in d["requirements"]) and "REQUIREMENT_NOT_ADMITTED" not in codes,
            "C3": not (codes & {"EVIDENCE_WRONG_COMMIT", "EVIDENCE_WRONG_CONTRACT", "CANDIDATE_DIRTY"}),
            "C4": "OPEN_BLOCKER" not in codes, "C5": "OPEN_MAJOR" not in codes, "C6": "SECURITY_HOLD_OPEN" not in codes, "C7": "MINOR_NOT_ACCEPTED" not in codes}
    for k, v in want.items():
        if (cond[k] == "MET") != v:
            return "conditions_incoherent"
    if (cond["C1"] == "MET") != (auth and "CONTRACT_NOT_FROZEN" not in codes):             # C1 = frozen with verified owner approval AND an enforced boundary (oaf.evaluate)
        return "conditions_incoherent"
    if cond["C10"] == "MET" and "MANUAL_GATE_PENDING" in codes:
        return "conditions_incoherent"
    # gates: C8 (review gates), C9 (every other non-manual gate) and C10 (manual gates) together cover every required gate, so they are MET together only if every listed gate state passes,
    # and a record whose listed gates all pass cannot claim C9/C10 unmet; C8 can then be unmet only through an unadjudicated or contested finding
    states = [g["state"] for r in d["requirements"] for g in r["gate_results"]]
    allpass = all(s in ("PASS", "WAIVED") for s in states)
    contested = bool(codes & {"UNADJUDICATED_MATERIAL_FINDING", "CONTESTED_MATERIAL_FINDING"})
    if cond["C8"] == "MET" and contested:
        return "conditions_incoherent"
    if all(cond[k] == "MET" for k in ("C8", "C9", "C10")) and not allpass:
        return "conditions_incoherent"
    if allpass and (cond["C9"] != "MET" or cond["C10"] != "MET" or (cond["C8"] != "MET" and not contested)):
        return "conditions_incoherent"
    # owner decisions: relied-on decisions come from owner-decision INPUTS, and the acceptance flag exists only through a relied-on FINAL_ACCEPTANCE decision on a CONTRACT_PASS (oaf.evaluate)
    if len(ids) > kinds.count("owner-decision"):
        return "decisions_without_inputs"
    final = any(o["kind"] == "FINAL_ACCEPTANCE" for o in d["owner_decisions_relied_on"])
    if d["release_accepted"] is not (final and d["verdict"] == "CONTRACT_PASS"):
        return "acceptance_not_derived"
    return ""


def validate_verdict(raw: bytes, commit: str) -> tuple[dict | None, str]:
    """Strict, fail-closed validation of a recorded OAF verdict (MG-OAF-026/027). Returns (view, "") or (None, reason). Nothing from an invalid record is projected.
    The record must have EXACTLY the members of the evaluator's output with exact types (`strict_shape`), a known verdict that follows from its own reasons, strict booleans, well formed digests,
    a RECOMPUTED canonical digest, exactly the running commit, and a coherent acceptance (an acceptance flag requires CONTRACT_PASS under an ENFORCED owner-authority boundary).
    The application cannot verify the owner's authority itself: a valid accepted record is shown as «registrado», never as the application's own approval."""
    try:
        d = json.loads(raw.decode("utf-8"), object_pairs_hook=_no_dupes, parse_constant=_no_const, parse_float=lambda x: (_ for _ in ()).throw(ValueError("float")))
    except (ValueError, UnicodeDecodeError, RecursionError):
        return None, "malformed"
    if not isinstance(d, dict) or d.get("schema") != "oaf.verdict/1":
        return None, "schema"
    if d.get("candidate_commit") != commit:
        return None, "wrong_commit"
    v = d.get("verdict")
    if not isinstance(v, str) or v not in VERDICTS:
        return None, "unknown_verdict"
    ra = d.get("release_accepted")
    if ra is not True and ra is not False:
        return None, "release_accepted_not_boolean"
    cid, rev = d.get("contract_id"), d.get("contract_revision")
    if not (isinstance(cid, str) and 1 <= len(cid) <= 80 and re.fullmatch(r"[A-Za-z0-9._-]+", cid) and _isint(rev) and 1 <= rev <= 10**6):
        return None, "contract_identity"
    for k in ("contract_digest", "policy_digest", "digest"):
        if not (isinstance(d.get(k), str) and _DIGEST.fullmatch(d[k])):
            return None, "digest_format"
    auth = d.get("owner_authority")
    if not (isinstance(auth, dict) and set(auth) == {"enforced", "source"} and isinstance(auth.get("enforced"), bool) and isinstance(auth.get("source"), str) and len(auth["source"]) <= 80):
        return None, "owner_authority_shape"
    why = strict_shape(d)
    if why:
        return None, why
    try:
        if verdict_digest(d) != d["digest"]:
            return None, "digest_mismatch"
    except ValueError:
        return None, "malformed"
    if ra and not (v == "CONTRACT_PASS" and auth["enforced"] is True):
        return None, "acceptance_without_pass_and_enforced_authority"
    if v == "CONTRACT_PASS" and auth["enforced"] is not True:
        return None, "pass_without_enforced_authority"
    return {"verdict": v, "contract_id": cid, "contract_revision": rev, "contract_digest": d["contract_digest"], "verdict_digest": d["digest"], "release_accepted_claimed": ra,
            "owner_authority_claimed_enforced": auth["enforced"]}, ""


_AFTER_OPEN = None      # test hook: called after the record's descriptor is opened and before it is read (to prove the read follows the descriptor, not the path)


def _open_dir(parent_fd: int, name: str) -> int:
    return os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)


def _read_record(root: Path, commit: str) -> list[tuple[float, bytes | None, str]]:
    """Walks logs/oaf_vertical01/<ledger>/records/<commit>/VERDICT.json with DESCRIPTORS: every component is opened with O_NOFOLLOW relative to its parent's descriptor (a symlink anywhere in the
    hierarchy is refused, never followed), the file's size is checked on the OPENED descriptor, and the bytes are read from that descriptor (bounded). Returns (mtime, bytes|None, problem)."""
    out = []
    try:
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    except OSError:
        return out
    chain = [fd]
    try:
        for comp in ("logs", "oaf_vertical01"):
            try:
                chain.append(_open_dir(chain[-1], comp))
            except FileNotFoundError:
                return out
            except OSError:
                return [(0.0, None, "symlink_or_unreadable_in_path")]
        base = chain[-1]
        for ledger in sorted(os.listdir(base)):
            sub = []
            try:
                for comp in (ledger, "records", commit):
                    sub.append(_open_dir(sub[-1] if sub else base, comp))
                f = os.open("VERDICT.json", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=sub[-1])
            except FileNotFoundError:
                continue
            except NotADirectoryError:
                continue
            except OSError:
                out.append((0.0, None, "symlink_or_unreadable_in_path"))
                continue
            finally:
                for x in sub:
                    os.close(x)
            try:
                st = os.fstat(f)
                if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_RECORD_BYTES:
                    out.append((st.st_mtime, None, "not_a_regular_file"))
                    continue
                if _AFTER_OPEN:
                    _AFTER_OPEN()
                data = b""
                while len(data) <= MAX_RECORD_BYTES:
                    chunk = os.read(f, 65536)
                    if not chunk:
                        break
                    data += chunk
                out.append((st.st_mtime, data if len(data) <= MAX_RECORD_BYTES else None, "" if len(data) <= MAX_RECORD_BYTES else "not_a_regular_file"))
            finally:
                os.close(f)
    except OSError:
        return out or [(0.0, None, "unreadable")]
    finally:
        for x in chain:
            os.close(x)
    return out


def recorded_oaf(commit: str | None, root: Path = ROOT) -> dict:
    """The recorded verdict bound to exactly `commit` after STRICT validation, or an explicit 'not recorded / not valid'. Only validated fields are exposed."""
    if not commit or not _is_sha(commit):
        return {"recorded": False, "reason": "commit_unknown"}
    invalid = None
    for _, raw, problem in sorted(_read_record(Path(root), commit), key=lambda t: t[0], reverse=True):
        if raw is None:
            invalid = problem or "unreadable"
            continue
        view, why = validate_verdict(raw, commit)
        if view is None:
            invalid = why
            continue
        return {"recorded": True, "validated": True, **view, "acceptance": "claimed_accepted" if view["release_accepted_claimed"] else "not_accepted", "authority_verified_by_app": False,
                "bound_to_commit": commit, "note": "recorded evaluator OUTPUT only: acceptance and owner authority are what the record CLAIMS; the application has neither the digest-bound input records nor the owner's authority, so it verifies neither (MG-OAF-030)"}
    if invalid:
        return {"recorded": False, "reason": "invalid_record", "invalid_why": invalid}
    return {"recorded": False, "reason": "no_verdict_for_this_commit"}


def build_info() -> dict:
    sha = started_commit()
    now = _git("rev-parse", "HEAD")
    status = _git("status", "--porcelain")
    return {"commit": sha, "commit_known": sha is not None,
            "tree_clean": (status == "") if status is not None else None,
            "checkout_moved_since_start": (now != sha) if (sha and _is_sha(now)) else None,
            "measured_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "source": "git rev-parse at process start" if sha else "unavailable",
            "oaf": recorded_oaf(sha)}
