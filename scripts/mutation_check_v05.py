#!/usr/bin/env python3
"""Mutation check for the review-26 corrections (MG-OAF/PROV/TRUST/DOC-026) and the review-28 corrections (MG-RISK/OAF/PROV/DOC-028) and the review-31/32/33 corrections (MG-ALERT/EVENT/WCAM/RISK/QUARANTINE-031..034) (stdlib). Each mutation is applied to a COPY of the sources (the working tree is never touched); the
selected tests must FAIL (pytest exit 1 / vitest failed tests, not a collection or import error) and a clean unmutated copy must PASS first.
Usage: scripts/mutation_check_v04.py <result.json>   (heavy tests are not used; every selected test uses the fake detector)"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = str(ROOT / ".venv" / "bin" / "python")
BE = [
    ("MG-OAF-026 a string «false» is accepted as the acceptance flag", "backend/mineguard/buildinfo.py", "if ra is not True and ra is not False:", "if False:", ["backend/tests/test_vertical04_state.py", "-k", "strict_boolean"]),
    ("MG-OAF-026 the digest is not recomputed", "backend/mineguard/buildinfo.py", 'if verdict_digest(d) != d["digest"]:', "if False:", ["backend/tests/test_vertical04_state.py", "-k", "forged_digest"]),
    ("MG-OAF-026 a record for another commit is accepted", "backend/mineguard/buildinfo.py", 'if d.get("candidate_commit") != commit:', "if False:", ["backend/tests/test_vertical04_state.py", "-k", "another_commit"]),
    ("MG-OAF-026 acceptance without PASS under an enforced boundary is accepted", "backend/mineguard/buildinfo.py", 'if ra and not (v == "CONTRACT_PASS" and auth["enforced"] is True):', "if False:", ["backend/tests/test_vertical04_state.py", "-k", "self_contradictory"]),
    ("MG-OAF-026 a symlinked record file is followed", "backend/mineguard/buildinfo.py", 'f = os.open("VERDICT.json", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=sub[-1])', 'f = os.open("VERDICT.json", os.O_RDONLY, dir_fd=sub[-1])', ["backend/tests/test_vertical04_state.py", "-k", "symlinked_or_oversized"]),
    ("MG-PROV-026 the running feed is labelled by its launch only", "backend/mineguard/feeds.py", "return bool(self._repeated_frozen or self._shared_run)", "return bool(self._repeated_frozen)", ["backend/tests/test_vertical05_provenance.py", "-k", "second_feed_of_a_file"]),
    ("MG-PROV-026 earlier alerts of the run are not re-labelled", "backend/mineguard/alerts.py", 'if src.get("kind") != "feed" or src.get("provenance") != "repeated_source_simulation":', "if True:", ["backend/tests/test_vertical05_provenance.py", "-k", "second_feed_of_a_file"]),
    ("MG-PROV-027 the evidentiary label follows the present relationship again", "backend/mineguard/feeds.py", "return bool(self._repeated_frozen or self._shared_run)", "return bool(self._repeated_frozen or self.shared_now())", ["backend/tests/test_vertical05_provenance.py", "-k", "stays_a_repeated or ends_alone or evidentiary"]),
    ("MG-OAF-027 members are not checked exactly", "backend/mineguard/buildinfo.py", "if set(d) != _TOP:", "if False:", ["backend/tests/test_vertical04_state.py", "-k", "required_verdict_member or unknown_member_is_refused"]),
    ("MG-OAF-027 the verdict need not follow from its reasons", "backend/mineguard/buildinfo.py", 'if d["verdict"] != expected:', "if False:", ["backend/tests/test_vertical04_state.py", "-k", "follow_from_its_own_reasons"]),
    ("MG-OAF-027 ancestors may be symlinks", "backend/mineguard/buildinfo.py", "os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd", "os.O_RDONLY | os.O_DIRECTORY, dir_fd=parent_fd", ["backend/tests/test_vertical04_state.py", "-k", "symlink_anywhere"]),
    ("MG-OAF-027 the file is re-read by path (race)", "backend/mineguard/buildinfo.py", "data = b\"\"\n                while len(data) <= MAX_RECORD_BYTES:\n                    chunk = os.read(f, 65536)", "data = b\"\"\n                while len(data) <= MAX_RECORD_BYTES:\n                    chunk = open(os.path.join(str(root), 'logs', 'oaf_vertical01', ledger, 'records', commit, 'VERDICT.json'), 'rb').read(65536) if not data else b''", ["backend/tests/test_vertical04_state.py", "-k", "opened_descriptor"]),
    ("MG-TRUST-026 the timeline row drops the restored flag", "backend/mineguard/monitor.py", '"simulation", "delivery_state", "loaded_from_store")}', '"simulation", "delivery_state")}', ["backend/tests/test_vertical05_provenance.py", "-k", "genuine_looking"]),
    ("MG-RISK-028 the closing hold is tested only at the returning sample", "backend/mineguard/risk/kernel.py", "self._expire_unobserved(s.t_us, inclusive=False)", "pass", ["backend/tests/test_risk_gap_closure.py"]),
    ("MG-RISK-028 a stream that ends in silence keeps its episode open", "backend/mineguard/risk/kernel.py", "self._expire_unobserved(end_us, inclusive=True)", "pass", ["backend/tests/test_risk_gap_closure.py"]),
    ("MG-RISK-028 the episode closes at the returning sample instead of the boundary", "backend/mineguard/risk/kernel.py", "self._close(m, tb, self._prev.index, reason,", "self._close(m, upto, self._prev.index, reason,", ["backend/tests/test_risk_gap_closure.py"]),
    ("MG-OAF-028 rank 6 and unknown codes are accepted as reasons", "backend/mineguard/buildinfo.py", 'r["rank"] in REASONS and isinstance(r["code"], str) and r["code"] in REASONS[r["rank"]]', '1 <= r["rank"] <= 6 and isinstance(r["code"], str) and _CODE.fullmatch(r["code"])', ["backend/tests/test_oaf028_verdict.py"]),
    ("MG-OAF-028 advisory codes are not checked against the catalogue", "backend/mineguard/buildinfo.py", 'isinstance(a["code"], str) and a["code"] in ADVISORIES and _s(a["ref"], 200)', 'isinstance(a["code"], str) and _CODE.fullmatch(a["code"]) and _s(a["ref"], 200)', ["backend/tests/test_oaf028_verdict.py"]),
    ("MG-OAF-028 reasons need not be unique and sorted", "backend/mineguard/buildinfo.py", "if keys != sorted(set(keys)):", "if False:", ["backend/tests/test_oaf028_verdict.py"]),
    ("MG-OAF-028 any evaluator identity is accepted", "backend/mineguard/buildinfo.py", 'd["evaluator"]["name"] == EVALUATOR["name"] and d["evaluator"]["version"] in EVALUATOR["versions"]', "True", ["backend/tests/test_oaf028_verdict.py"]),
    ("MG-OAF-028 conditions need not follow from the reasons", "backend/mineguard/buildinfo.py", 'if (cond[k] == "MET") != v:', "if False:", ["backend/tests/test_oaf028_verdict.py"]),
    ("MG-PROV-028 the phase of earlier alerts is not kept up to date", "backend/mineguard/alerts.py", 'if ph != "unknown" and a.get("overlap_phase") not in (ph, "mixed"):', "if False:", ["backend/tests/test_overlap_history.py"]),
    ("MG-PROV-028 every episode counts as during the overlap", "backend/mineguard/overlap.py", "if start_us <= a:", "if False:", ["backend/tests/test_overlap_history.py"]),
    ("MG-PROV-028 a record without interval evidence is read as alone", "backend/mineguard/overlap.py", 'return "unknown" if repeated_source else "alone"', 'return "alone"', ["backend/tests/test_overlap_history.py"]),
    ("MG-PROV-028 a merged alert does not say mixed", "backend/mineguard/alerts.py", 'a["overlap_phase"] = "mixed"', "pass", ["backend/tests/test_overlap_history.py"]),
    ("MG-PROV-028 the end of an overlap is never recorded", "backend/mineguard/feeds.py", 'self._overlaps[-1]["until_before_us"] = t_us', "pass", ["backend/tests/test_overlap_history.py"]),
    ("MG-RISK-029 a trailing silence with no open episode stays GREEN", "backend/mineguard/risk/kernel.py", "if end_us - self._prev.t_us > self.p.max_sample_gap_us:", "if False:", ["backend/tests/test_risk_gap_closure.py"]),
    ("MG-OAF-029 the acceptance flag need not follow from a FINAL_ACCEPTANCE decision", "backend/mineguard/buildinfo.py", 'if d["release_accepted"] is not (final and d["verdict"] == "CONTRACT_PASS"):', "if False:", ["backend/tests/test_oaf028_verdict.py"]),
    ("MG-OAF-029 relied-on decisions need no owner-decision input", "backend/mineguard/buildinfo.py", 'if len(ids) > kinds.count("owner-decision"):', "if False:", ["backend/tests/test_oaf028_verdict.py"]),
    ("MG-OAF-029 gate conditions may be falsely unmet when every listed gate passes", "backend/mineguard/buildinfo.py", "if allpass and (", "if False and (", ["backend/tests/test_oaf028_verdict.py"]),
    ("MG-OAF-029 C1 need not follow the freeze and the authority", "backend/mineguard/buildinfo.py", 'if (cond["C1"] == "MET") != (auth and "CONTRACT_NOT_FROZEN" not in codes):', "if False:", ["backend/tests/test_oaf028_verdict.py"]),
    ("MG-ALERT-029 delivery is assumed when every client channel is disabled", "backend/mineguard/alerts.py", 'return "in_app_only" if d["in_app"]["status"] == "displayed" else "pending"', 'return "delivered"', ["backend/tests/test_alert_delivery029.py"]),
    ("MG-ALERT-029 displaying an alert does not update its delivery state", "backend/mineguard/alerts.py", 'a["delivery"]["in_app"] = {"status": "displayed", "at": self._utc(self.wall())}\n            a["delivery_state"] = self.delivery_state(a["delivery"])', 'a["delivery"]["in_app"] = {"status": "displayed", "at": self._utc(self.wall())}', ["backend/tests/test_alert_delivery029.py"]),
    ("MG-PROV-029 the overlap bound is reached silently", "backend/mineguard/feeds.py", "self._overlaps_truncated = True", "pass", ["backend/tests/test_overlap_history.py"]),
    ("MG-PROV-029 a truncated history still claims a phase after the last interval", "backend/mineguard/overlap.py", 'if doc["truncated"]:', "if False:", ["backend/tests/test_overlap_history.py"]),
    ("MG-OAF-030 a recorded claim is projected as recorded acceptance again", "backend/mineguard/buildinfo.py", '"claimed_accepted" if view["release_accepted_claimed"] else "not_accepted"', '"recorded_accepted" if view["release_accepted_claimed"] else "not_accepted"', ["backend/tests/test_oaf028_verdict.py"]),
    ("MG-OAF-030 the claimed authority is projected under the enforced name again", "backend/mineguard/buildinfo.py", '"owner_authority_claimed_enforced": auth["enforced"]}, ""', '"owner_authority_enforced": auth["enforced"]}, ""', ["backend/tests/test_oaf028_verdict.py"]),
    ("MG-ALERT-030 a denied channel hides a real client delivery", "backend/mineguard/alerts.py", 'if "delivered" in cl:', 'if "delivered" in cl and "denied" not in cl and "unsupported" not in cl:', ["backend/tests/test_alert_delivery029.py"]),
    ("MG-ALERT-030 a denied channel claims in-app delivery before display", "backend/mineguard/alerts.py", 'return "in_app_only" if d["in_app"]["status"] == "displayed" else "pending"', 'return "in_app_only"', ["backend/tests/test_alert_delivery029.py"]),
    ("MG-ALERT-031 a stored aggregate is trusted without the channel outcomes (coherence check off)", "backend/mineguard/alerts.py", "if coherence and not _delivery_coherent(a):", "if False:", ["backend/tests/test_final_remediation031.py"]),
    ("MG-ALERT-031 the aggregate is not compared with the one derived from the channels", "backend/mineguard/alerts.py", 'return a["delivery_state"] == AlertEngine.delivery_state(d)', "return True", ["backend/tests/test_final_remediation031.py"]),
    ("MG-ALERT-031 a reported outcome need not carry its timestamp", "backend/mineguard/alerts.py", 'if reported != (ch["at"] is not None) or (ch["at"] is not None and not _UTC.match(ch["at"])):', "if False:", ["backend/tests/test_final_remediation031.py"]),
    ("MG-ALERT-031 a restored alert can receive a client delivery", "backend/mineguard/alerts.py", 'if a.get("loaded_from_store"):\n                raise ValueError("restored_alert_not_deliverable")', 'if False:\n                raise ValueError("restored_alert_not_deliverable")', ["backend/tests/test_final_remediation031.py"]),
    ('MG-EVENT-030 the events cache ignores the observations (dependency list)', 'backend/mineguard/snapshot.py', 'DEPENDENCIES = ("export.json", "observations.json", "source.json", "manifest.json", "risk/zones.json", "media_deleted.json")', 'DEPENDENCIES = ("export.json", "source.json", "manifest.json", "risk/zones.json", "media_deleted.json")', ["backend/tests/test_final_remediation031.py"]),
    ('MG-EVENT-031 the cache identity is file size, not content', 'backend/mineguard/snapshot.py', '(hashlib.sha256(v).hexdigest() if isinstance(v, bytes) else v)', '(len(v) if isinstance(v, bytes) else v)', ["backend/tests/test_final_remediation031.py"]),
    ('MG-EVENT-031 a symlinked dependency is followed', 'backend/mineguard/snapshot.py', 'os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK', 'os.O_RDONLY | os.O_NONBLOCK', ["backend/tests/test_final_remediation031.py"]),
    ('MG-EVENT-032 a result is cached even though the inputs moved while it was derived', 'backend/mineguard/api_vs02.py', '        if not snapshot.matches_disk(snap, d):\n            raise ValueError("source_changed_while_read")\n        ev_cache.put', '        if False:\n            raise ValueError("source_changed_while_read")\n        ev_cache.put', ["backend/tests/test_final_remediation031.py"]),
    ('MG-EVENT-032 the derivation re-reads the files instead of using the snapshot bytes', 'backend/mineguard/api_vs02.py', 'obs, source, manifest = snap.doc("observations.json"), snap.doc("source.json"), snap.doc("manifest.json")', 'obs, source, manifest = read_json_strict(d / "observations.json"), read_json_strict(d / "source.json"), read_json_strict(d / "manifest.json")', ["backend/tests/test_final_remediation031.py"]),
    ('MG-EVENT-032 the cache hands out its stored object', 'backend/mineguard/snapshot.py', 'return copy.deepcopy(hit[1]) if hit and hit[0] == key else None', 'return hit[1] if hit and hit[0] == key else None', ["backend/tests/test_final_remediation031.py"]),
    ("MG-EVENT-032 the cache stores the caller's object", 'backend/mineguard/snapshot.py', 'self._d[name] = (key, copy.deepcopy(value))', 'self._d[name] = (key, value)', ["backend/tests/test_final_remediation031.py"]),
    ('MG-ALERT-032 an identical terminal report is applied again', 'backend/mineguard/alerts.py', '            if slot["status"] == status:\n                return a ', '            if False:\n                return a ', ["backend/tests/test_final_remediation031.py"]),
    ('MG-QUARANTINE-032 a rejected store is not preserved', 'backend/mineguard/alerts.py', 'if self.rejected_on_load or not isinstance(doc.get("alerts"), list):', 'if False:', ["backend/tests/test_final_remediation031.py"]),
    ('MG-QUARANTINE-032 an unparseable store is not preserved before the product starts empty', 'backend/mineguard/alerts.py', 'self._preserve_rejected_store(fd, raw, 0)\n                        raise', 'raise', ["backend/tests/test_final_remediation031.py"]),
    ('MG-QUARANTINE-032 an earlier preserved record can be replaced', 'backend/mineguard/safedir.py', 'os.link(tmp, name, src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)', 'os.rename(tmp, name, src_dir_fd=fd, dst_dir_fd=fd)', ["backend/tests/test_final_remediation031.py"]),
    ('MG-QUARANTINE-032 preservation is claimed complete without reading it back', 'backend/mineguard/alerts.py', 'info["complete"] = hashlib.sha256(safedir.read_regular_bytes(fd, name)).hexdigest() == h', 'info["complete"] = True', ["backend/tests/test_final_remediation031.py"]),
    ('MG-WCAM-032 the accepted count may exceed the recorded outcomes', 'backend/mineguard/session_docs.py', 'a["accepted"] == a["analyzed"] + a["dropped"] + a["adapter_error"]', 'a["accepted"] >= a["analyzed"] + a["dropped"] + a["adapter_error"]', ["backend/tests/test_final_remediation031.py"]),
    ('MG-WCAM-032 the sample log shape is not checked by the accounting', 'backend/mineguard/session_docs.py', '    if not samples_valid(samples, retention):\n        return False  ', '    if False:\n        return False  ', ["backend/tests/test_final_remediation031.py"]),
    ('MG-WCAM-032 an unknown drop reason is accepted', 'backend/mineguard/session_docs.py', 'DROP_REASONS = ("queue_overflow", "stale", "session_stopped")', 'DROP_REASONS = ("queue_overflow", "stale", "session_stopped", "mystery")', ["backend/tests/test_final_remediation031.py"]),
    ('MG-WCAM-032 duplicate or reordered sequences are accepted', 'backend/mineguard/session_docs.py', 'or x["seq"] <= last_seq or', 'or x["seq"] < -1 or', ["backend/tests/test_final_remediation031.py"]),
    ('MG-WCAM-032 stored-session verification omits the shape check', 'backend/mineguard/session_verify.py', 'add("sample_log_shape_is_exact", sd.samples_valid(samples, mode),', 'add("sample_log_shape_is_exact", True,', ["backend/tests/test_final_remediation031.py"]),
    ('MG-WCAM-032 finalization stores an unverifiable sample log', 'backend/mineguard/webcam.py', 'if not sd.samples_valid(sample_log, "frames" if self.retain else "metadata_only"):', 'if False:', ["backend/tests/test_final_remediation031.py"]),
    ('MG-RISK-033 /risk re-reads the files instead of using the snapshot', 'backend/mineguard/api_vs02.py', '"evaluation": evaluate_dir(d, zdoc, snap), "trust": trust_for(kind, d), "zones": zdoc}', '"evaluation": evaluate_dir(d, zdoc), "trust": trust_for(kind, d), "zones": zdoc}', ["backend/tests/test_final_remediation031.py"]),
    ('MG-RISK-033 /risk/export re-reads the files instead of using the snapshot', 'backend/mineguard/api_vs02.py', '                    doc = evaluate_dir(d, zdoc, snap)\n', '                    doc = evaluate_dir(d, zdoc)\n', ["backend/tests/test_final_remediation031.py"]),
    ('MG-RISK-033 the export bundle comes from the disk, not the snapshot', 'backend/mineguard/api_vs02.py', 'bundle = copy.deepcopy(snap.doc("export.json"))          #', 'bundle = read_json_strict(d / "export.json")          #', ["backend/tests/test_final_remediation031.py"]),
    ('MG-RISK-033 a response whose inputs moved is published', 'backend/mineguard/api_vs02.py', '        if not snapshot.matches_disk(snap, d):\n            raise HTTPException(409, {"msg_id": "mg.refusal.source_changed_while_read"})', '        if False:\n            raise HTTPException(409, {"msg_id": "mg.refusal.source_changed_while_read"})', ["backend/tests/test_final_remediation031.py"]),
    ('MG-RISK-033 the zone reader uses the disk', 'backend/mineguard/api_vs02.py', 'return consistent(d, lambda snap: load_zones(d, snap=snap))', 'return load_zones(d)', ["backend/tests/test_final_remediation031.py"]),
    ('MG-EVENT-033 a cache hit skips the publication check', 'backend/mineguard/api_vs02.py', '            if not snapshot.matches_disk(snap, d):\n                raise ValueError("source_changed_while_read")   # MG-EVENT-033', '            if False:\n                raise ValueError("source_changed_while_read")   # MG-EVENT-033', ["backend/tests/test_final_remediation031.py"]),
    ('MG-WCAM-033 the format marker is not part of the digest-bound identity', 'backend/mineguard/session_docs.py', '    if session_format is not None:\n        p["session_format"] = session_format', '    if False:\n        p["session_format"] = session_format', ["backend/tests/test_final_remediation031.py"]),
    ('MG-WCAM-033 a session without the current format marker verifies as current', 'backend/mineguard/session_verify.py', 'cur = source.get("session_format") == sd.SESSION_FORMAT', 'cur = True', ["backend/tests/test_final_remediation031.py"]),
    ('MG-WCAM-033 a current session may omit its accounting', 'backend/mineguard/session_docs.py', 'if current and (a is None or set(a) != set(ACCOUNTING_KEYS_V3) or set(ls) != set(STATS_KEYS_V2)):', 'if current and False:', ["backend/tests/test_final_remediation031.py"]),
    ('MG-WCAM-033 the producer does not write the format marker', 'backend/mineguard/webcam.py', '"retention": retention, "session_format": sd.SESSION_FORMAT}', '"retention": retention}', ["backend/tests/test_final_remediation031.py"]),
    ('MG-WCAM-034 an image hash is accepted for any status and retention mode', 'backend/mineguard/session_docs.py', 'if retention is not None and (x["jpeg_sha256"] is not None) != (retention == "frames" and st == "analyzed"):', 'if False:', ["backend/tests/test_final_remediation031.py"]),
    ('MG-WCAM-034 verification ignores the retention mode', 'backend/mineguard/session_verify.py', 'sd.samples_valid(samples, mode), "every sample', 'sd.samples_valid(samples), "every sample', ["backend/tests/test_final_remediation031.py"]),
    ("MG-RISK-029 the engine version does not change with the behaviour", "backend/mineguard/risk/kernel.py", 'ENGINE_VERSION = "mg-risk-kernel/2.0.3"', 'ENGINE_VERSION = "mg-risk-kernel/2.0.2"', ["backend/tests/test_night_review4.py"]),
    ("MG-DOC-026 the code tracker version drifts from the docs", "backend/mineguard/risk/tracker.py", 'TRACKER_VERSION = "0.3.0"', 'TRACKER_VERSION = "0.9.9"', ["backend/tests/test_doc_consistency.py"]),
]
FE = [
    ("MG-TRUST-026 restored alerts are classified as real on every surface", "frontend/src/lib.ts", "if (x.loaded_from_store) return \"unverified\";", "", ["src/lib.test.ts"]),
    ("MG-OAF-026 truthiness decides acceptance in the UI", "frontend/src/lib.ts", 'if (o.validated !== true || typeof o.release_accepted_claimed !== "boolean") return { kind: "invalid" };', "", ["src/lib.test.ts"]),
    ("MG-PROV-028 an unrecognised overlap phase is shown as alone", "frontend/src/lib.ts", '? phase : "unknown"}`;', '? phase : "alone"}`;', ["src/lib.test.ts"]),
]


def run(cmd, cwd, timeout=300):
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    except subprocess.TimeoutExpired:
        return 124, "TIMEOUT: a mutation that hangs is NOT a kill (no assertion failed)"      # reported as SURVIVED_OR_ERROR, never as KILLED, and never aborts the other mutations
    return p.returncode, (p.stdout + p.stderr)[-8000:]


def workdir():
    d = Path(tempfile.mkdtemp(prefix="mgmut-", dir=ROOT / "logs" / "v04"))
    shutil.copytree(ROOT / "backend", d / "backend", ignore=shutil.ignore_patterns("__pycache__"))
    for n in ("pytest.ini", "test-assets", "models", "docs", "logs", "oaf"):
        if (ROOT / n).exists():
            os.symlink(ROOT / n, d / n)
    shutil.copytree(ROOT / "frontend" / "src", d / "frontend" / "src")
    for n in ("package.json", "tsconfig.json", "vite.config.ts", "node_modules", "index.html"):
        if (ROOT / "frontend" / n).exists():
            os.symlink(ROOT / "frontend" / n, d / "frontend" / n)
    return d


def be_test(d, args):
    return run([PY, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", *args], d)


def fe_test(d, args):
    return run(["npx", "vitest", "run", *args], d / "frontend")


def main():
    out = {"controls": {}, "mutations": []}
    d = workdir()
    try:
        rc, tail = be_test(d, [a for a in ["backend/tests/test_vertical05_provenance.py", "backend/tests/test_vertical04_state.py", "backend/tests/test_doc_consistency.py", "backend/tests/test_risk_gap_closure.py", "backend/tests/test_oaf028_verdict.py", "backend/tests/test_overlap_history.py", "backend/tests/test_alert_delivery029.py", "backend/tests/test_night_review4.py", "backend/tests/test_night_review1.py", "backend/tests/test_final_remediation031.py"]])
        out["controls"]["backend"] = {"rc": rc, "tail": tail[-200:]}
        rc2, tail2 = fe_test(d, ["src/lib.test.ts"])
        out["controls"]["frontend"] = {"rc": rc2, "tail": tail2[-200:]}
        if rc != 0 or rc2 != 0:
            print("CONTROL FAILED", json.dumps(out["controls"], indent=1)); Path(sys.argv[1]).write_text(json.dumps(out, indent=1)); return 2
    finally:
        shutil.rmtree(d, ignore_errors=True)
    for name, rel, old, new, args in BE + FE:
        d = workdir()
        try:
            f = d / rel
            src = f.read_text()
            if src.count(old) != 1:
                out["mutations"].append({"name": name, "status": "INVALID_ANCHOR", "count": src.count(old)}); continue
            if old == new:
                out["mutations"].append({"name": name, "status": "SKIPPED_NOOP"}); continue
            f.write_text(src.replace(old, new))
            rc, tail = (be_test if rel.startswith("backend") else fe_test)(d, args)
            killed = rc == 1 and bool(re.search(r"\d+ failed", tail) or re.search(r"Tests\s+\d+ failed", tail))   # a real test failure; a collection / import error (rc 2) never counts
            out["mutations"].append({"name": name, "status": "KILLED" if killed else "SURVIVED_OR_ERROR", "rc": rc, "tail": tail[-300:]})
        finally:
            shutil.rmtree(d, ignore_errors=True)
        print(out["mutations"][-1]["status"], name)
    Path(sys.argv[1]).write_text(json.dumps(out, indent=1))
    bad = [m for m in out["mutations"] if m["status"] != "KILLED"]
    print(f"{len(out['mutations']) - len(bad)}/{len(out['mutations'])} killed; not killed: {[m['name'] for m in bad]}")
    return 1 if bad else 0


sys.exit(main())
