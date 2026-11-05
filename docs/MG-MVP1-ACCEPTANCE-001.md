# MG-MVP1-ACCEPTANCE-001 — MVP 1 acceptance record and open risks

Status: **MVP 1 NOT ACCEPTED** (candidate; see §3). The later increment MG-VERTICAL-02 (multi-feed, alerts, dashboards, corrections of the cycle-20 findings) is a separate candidate described in `MG-VERTICAL2-001`; it is NOT accepted either. Date: 2026-10-25 (night sprint). Evidence labels: CONFIRMED / UNVERIFIED / BLOCKED.

## 1. Gate checklist
| Gate | Status | Evidence |
|---|---|---|
| Privacy ON default in backend and UI | CONFIRMED | `test_privacy.py` (default, strict values, per-session), E2E «Privacy ON is the DEFAULT», selector resets after each capture |
| No persistent visual data under Privacy ON | CONFIRMED (synthetic + real-model clip frames) | scans for image magic bytes / base64 over the whole test storage; real-model test |
| Privacy OFF persists frames, indicator, delete images | CONFIRMED | `test_privacy.py`, E2E (banner, «RETENIDOS», delete images) |
| Delete one / selected / all; personal sessions preserved | CONFIRMED (isolated fixtures) | `test_session_deletion.py`, `test_night_review1.py`; decoys survive; UI E2E (counts: §2) |
| Verification/trust consistency | CONFIRMED | metadata-only never `verified`; any failed verification revokes; provenance hash bound to identity |
| RED simulation through the UI, regression | CONFIRMED | MVP browser suite (counts: §2), `test_vs02_replay_mp4.py` |
| MP4 regression | CONFIRMED | MP4 browser suite and `test_integration_real.py` (counts: §2) |
| Webcam synthetic-device E2E | CONFIRMED as a TEST SOURCE only | Chrome fake device fed from a Y4M; physical-camera acceptance PENDING |
| UI navigation (the nine areas of MVP 1 plus Multi-fuente = ten) | CONFIRMED | MVP E2E (counts: §2) |
| Docs match code | PROPOSED | this document set; reviewed in review 2 |
| Independent Codex review without unresolved blocking/major | **NOT MET** | nine reviews (cycles 15-23), all FAIL: majors 5, 8, 7, 5, 4, 3, 6, 8, 4 (minors 2, 1, 0, 0, 2, 1, 1, 3, 1); every finding reproduced, fixed and regression-tested afterwards; the review budget of MG-VERTICAL-02 (three reviews) is EXHAUSTED and everything corrected after the last one is UNREVIEWED (verbatim texts `logs/night_review/cn1`..`cn6`, `cv2a`..`cv2c`) |
| Multi-feed monitoring (three feeds, genuine model), per-feed isolation | MEASURED + browser suite | `MG-VERTICAL2-001` §3, §13; five feeds RESOURCE_BLOCKED; physical multi-camera PENDING |
| Alert engine, in-app / browser / sound, dashboards | IMPLEMENTED (candidate) | `MG-VERTICAL2-001` §7–§11; browser notification only exercised with permission overrides, not by a human |
| Cycle-20 findings (WCAM-019/020/021, EVIDENCE-019) | CORRECTED and confirmed as resolved by review A (cycle 21) | `test_vertical2_wcam`, `MG-VERTICAL2-001` §4 |
| Full acceptance gate under the watchdog | **BLOCKED** | host memory pressure level 2 (~47 % free) at every attempt; thresholds not relaxed |

## 2. Test inventory and executed results (GENERATED; no figure here is typed by hand)
Regenerate with `scripts/acceptance_counts.py generate --evidence <TEST_EVIDENCE.txt> --e2e label=<transcript>`; `scripts/acceptance_counts.py check` (also run by `test_acceptance_counts.py`) fails when this block disagrees with the current test inventory, so an obsolete count cannot survive.

<!-- GENERATED:TEST_INVENTORY BEGIN (scripts/acceptance_counts.py; do not edit by hand) -->

Module inventory = `pytest --collect-only` of the tree this block was generated from. Executed results = the named evidence transcript (git-ignored, identified by hash).

| Module | Collected | Executed passed | Exit |
|---|---:|---:|---:|
| acceptance_counts | 3 | not executed | n/a |
| adapter_policy | 8 | not executed | n/a |
| alert_delivery029 | 6 | not executed | n/a |
| costgov_base | 30 | not executed | n/a |
| costgov_hardening | 34 | not executed | n/a |
| cycle24_alerts | 10 | not executed | n/a |
| cycle24_feed | 25 | not executed | n/a |
| cycle24_store | 30 | not executed | n/a |
| cycle24_wcam | 10 | not executed | n/a |
| cycle25_findings | 9 | not executed | n/a |
| cycle7_findings | 36 | not executed | n/a |
| cycle8_findings | 53 | not executed | n/a |
| doc_consistency | 3 | not executed | n/a |
| external_import | 216 | not executed | n/a |
| final_remediation031 | 109 | not executed | n/a |
| gate_preconditions | 5 | not executed | n/a |
| integration_real | 21 | not executed | n/a |
| meta_containment | 97 | not executed | n/a |
| meta_invariants | 10 | not executed | n/a |
| meta_pipeline | 14 | not executed | n/a |
| meta_provenance | 195 | not executed | n/a |
| meta_sam31 | 53 | not executed | n/a |
| meta_transport | 38 | not executed | n/a |
| muse_open | 49 | not executed | n/a |
| night_review1 | 9 | not executed | n/a |
| night_review2 | 12 | not executed | n/a |
| night_review3 | 9 | not executed | n/a |
| night_review4 | 6 | not executed | n/a |
| night_review5 | 6 | not executed | n/a |
| oaf028_verdict | 48 | not executed | n/a |
| overlap_history | 34 | not executed | n/a |
| privacy | 14 | not executed | n/a |
| product_vertical06 | 17 | not executed | n/a |
| product_vertical07 | 9 | not executed | n/a |
| rc1_closeout | 26 | not executed | n/a |
| rc1s_closeout | 113 | not executed | n/a |
| risk_gap_closure | 16 | not executed | n/a |
| risk_kernel | 73 | not executed | n/a |
| risk_kernel_mutations | 29 | not executed | n/a |
| schema_validation | 32 | not executed | n/a |
| session_deletion | 23 | not executed | n/a |
| timebase_canon | 6 | not executed | n/a |
| unified_explain_unit | 65 | not executed | n/a |
| unified_providers_explain | 4 | not executed | n/a |
| vertical04_state | 80 | not executed | n/a |
| vertical05_provenance | 11 | not executed | n/a |
| vertical2_alerts | 60 | not executed | n/a |
| vertical2_dashboards | 15 | not executed | n/a |
| vertical2_feeds | 32 | not executed | n/a |
| vertical2_security | 21 | not executed | n/a |
| vertical2_track | 4 | not executed | n/a |
| vertical2_wcam | 38 | not executed | n/a |
| video_feeds15 | 27 | not executed | n/a |
| vs02_integrity | 31 | not executed | n/a |
| vs02_replay_mp4 | 9 | not executed | n/a |
| vs02_webcam_risk | 62 | not executed | n/a |
| **Total (56 modules)** | **2005** | **0** | |

Evidence: none supplied for this generation (inventory only; nothing is claimed as executed).

Full acceptance gate (`scripts/acceptance_gate.sh`): **NOT RUN in this evidence**. Targeted module runs are never a gate result.

<!-- GENERATED:TEST_INVENTORY END -->

The intermittent browser timeout at the step «analysis started» is analysed in `MG-MVP1-STABILIZATION-001` §2 (cause UNVERIFIED; the test now records the HTTP outcome of the session-start request). Mutation checks: each safeguard added in a review round has at least one test that fails when the safeguard is removed.

## 3. Why MVP 1 is not accepted yet
Two mandatory conditions are open: (a) the full gate has not completed because of resource pressure (BLOCKED, not failed), and (b) no independent review has passed: each review so far found new major defects in the previous round's fixes, and the latest fixes are unreviewed. Per the sprint rules a resource-blocked mandatory acceptance prevents claiming MVP 1 and prevents heavy multi-feed execution.

## 4. Open risks (all remain)
1. Disclosure incident from an earlier raw review snapshot: OPEN (owner decision). 2. Physical-camera acceptance: PENDING. 3. Legal/licence/eligibility gates G-1..G-9: OPEN. 4. Tracker is the simple IoU v0 (no ByteTrack: no approved dependency). 5. Metadata (and the per-frame pixel hash) is not anonymous. 6. The runtime environment recorded in provenance cannot be re-executed for image-less sessions. 7. Fixes after the last review are UNREVIEWED by definition. 8. Existing personal and older synthetic sessions/zones in the owner's live storage were NOT touched; the owner decides their cleanup (Sesiones area).

## 5. Fixes after the fifth (last) authorized review: UNREVIEWED
Review 5 (cycle 19) returned FAIL (4 major, 2 minor): MG-WCAM-019 (heartbeat watermark), MG-WCAM-020 (staleness re-checked after inference), MG-ZONE-019 (simulation default zone under the artifact lock), MG-ZONE-020 (NFC zone names; validation errors no longer echo input), MG-VERSION-019, MG-EVIDENCE-019 (the table in §2 is generated mechanically from the execution transcript). All were reproduced and corrected afterwards with regression vectors in `test_night_review5.py`; **no independent review of these corrections exists**. The verbatim review texts are in `logs/night_review/cn1`..`cn5`.
