# Contribution timeline — pre-existing MineGuard vs event-period work

**Evidence base:** `git log` of the project's development repository (history to commit `21ed859`) (106 commits to `21ed859`, author dates, offset -05:00) plus the accepted-product repository (5 commits `adc4924…afe4b39`, a snapshot of `21ed859` followed by UI work). Git dates are evidence, **not proof**: they are local timestamps; they do not prove authorship, and commit messages are not proof that a feature works. Nothing was classified as "new" without a dated commit.

**Boundary used (conservative):** the public pages say the event *begins Oct 26* with workshops Oct 27–Nov 4; earlier docs used 2026-10-27 as the owner-relayed start. **Everything dated 2026-10-26 or earlier is classified PRE-HACKATHON.** The first commit after the boundary is `bff33ae` (2026-10-28 04:00). The private Official Rules text on pre-existing work was not available to the builder and is **not verified**; the exact kickoff time on Oct 26 is also not verified.

| Period / commits | What | Class | In the accepted product? |
|---|---|---|---|
| 2026-10-24 `7f9c916`…`02eec22` (VS-01, 11 checkpoints) | MP4 decode (AVFoundation), local RF-DETR Nano inference, FastAPI + React, **provenance verification and trust states** (`19acc1c` source/evidence-bound trust, `f209737` provenance verification and approved-model anchor, `8b67bc1` executing-environment provenance, `9b54116` origin labels), local API security | **PRE-HACKATHON** | yes |
| 2026-10-24 → 10-25 `0879321`…`147f139` (VS-02, MVP 1, Night sprint, Vertical-02) | Risk Kernel v2, tracker, zones, webcam sessions, Privacy-ON default, multi-feed monitoring, alert center, dashboards | **PRE-HACKATHON** | yes |
| 2026-10-25 `098f69e`, `d66bb1e`, `ea89281` | OAF evaluator experiments | PRE-HACKATHON | docs only, excluded from release |
| 2026-10-25 → 10-26 `acd237f`…`10f56c4` (Console 03/04/05) | Operations console shell, source/risk state separation, trusted operations & incident investigation | **PRE-HACKATHON** | yes |
| 2026-10-28 `bff33ae`…`f429c82` (V05 closeouts 028–033) | Correctness/integrity corrections of the pre-existing V05 code | IN-WINDOW **corrections of pre-existing code**, not new capability | yes |
| 2026-10-28 `d8d2419`, `004b9ff`, `be205c8` (Vertical-06) | Incident investigation workbench, incident timeline, an evidence/trust *panel* in the UI and JSON export — surfaces built **on top of** the pre-existing trust states | **HACKATHON-WINDOW** (built on pre-existing core) | yes |
| 2026-10-29 `d146f72`, `7b02057` (Vertical-07) | Source lifecycle, complete incident timeline, local demo launcher | **HACKATHON-WINDOW** | yes |
| 2026-11-01 `80bf0c2`, `53cba9a`, `eab991b`, `47344c5`…`ef933b4` (Unified-01) | Cost Governor port (deny-by-default, `NullRealTransport`), read-only provider status, SIMULATED explanation | **HACKATHON-WINDOW** | yes |
| 2026-11-01 `af50df0`…`00bf298` (Recovery-02) | Explanation corrections, SAM 3.1 first-call *plan* (not authorized) | HACKATHON-WINDOW | code yes; plan docs excluded |
| 2026-11-01 `7f81be5`…`2b6d282` (Release-prep, Meta-Live-04, Recovery-05, Final-06, Live-Gate-07) | Inert governed Meta transport/clients, projection boundary, offline SAM/Muse request builders | HACKATHON-WINDOW, **independent reviews FAILED** (2 of 2 each, open defects) | **source files are present but nothing imports the transport (static invariant test); not a claimed capability** |
| 2026-11-02 `0443134`, `fb0b9e6` (EN-locale) | English UI (default) + Spanish, Zonivra brand | **HACKATHON-WINDOW** | yes |
| 2026-11-02 `28606b6`, `bd693c7` (Evidence-10 + R1) | Offline import/review of an external SAM 3.1 result: provenance manifest, hash verification, "authenticity not proven" semantics, closed SSE allowlist (new provenance *classes* for imported results; the general provenance concept pre-existed) | **HACKATHON-WINDOW** | yes |
| 2026-11-02 `de8a08a` (Real-SSE-12) | Strict observed profile `META_SAM31_OBSERVED_1`, isolated import of the first owner-executed SAM response | **HACKATHON-WINDOW** | yes (independently reviewed) |
| 2026-11-03 `21ed859` (Final-15-R1) | Webcam 503 (D1) — *rejected*; camera state UX, deterministic synthetic industrial scenarios | HACKATHON-WINDOW; **webcam backend reverted to `de8a08a`**; scenarios/UX via the accepted UI commit | scenarios yes; D1 no |
| 2026-11-03 `95d40a5`, `e05844a` (NS20 / UI-REVAMP-22) | Light/Dark/System themes, risk glyph badges, evidence passport, scenario cards | **HACKATHON-WINDOW** | yes (independently passed) |
| 2026-11-04 `d4bbb57`, `afe4b39` | Release integration (webcam baseline retained), top-bar chip fix (3 UI files + 1 test) | HACKATHON-WINDOW | yes (`FV29_DELTA_PASS`) |

## Non-repository, owner-reported items
- **One genuine SAM 3.1 API request** executed by the owner outside the application on a synthetic truck image (attempt `f892c336…`, finished 2026-11-03T04:24:22Z, HTTP 200, 165,441 bytes). **No repository artifact establishes it other than the private response and its outcome record.** It is not Zonivra-native inference.
- **Owner acceptance 2026-11-04:** the owner imported it manually into the accepted UI and reported PASS for the review workflow (response `698b0b63…3036`, image `721c3589…2e1d`, manifest `c064e40a…1016`; one box, UNCLASSIFIED, no confidence, strict profile passed, `HASHES_VERIFIED_AUTHENTICITY_NOT_PROVEN`). This is the owner's own visual acceptance, not an independent review.

## Third-party (not contributions)
RF-DETR/PyTorch/FastAPI/React and other dependencies; RF-DETR weights and COCO data; stock footage (Pexels #6565442); Higgsfield presenter clip; Meta's SAM 3.1 model.

## What must not be claimed
Pre-Oct-26 work as event work — including the provenance/trust foundation, RF-DETR inference, tracking, zones and the Risk Kernel · native/live Meta inference in the app · SAM-derived risk · industrial accuracy · genuine incidents (the only RED is a labelled synthetic rule demonstration) · the failed Meta transport or the rejected webcam D1/D1-R2 as capabilities.
