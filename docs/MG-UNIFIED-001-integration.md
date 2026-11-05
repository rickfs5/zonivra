# MG-UNIFIED-001 — Zonivra unified: Local-first MineGuard + selected Meta Lab components

Status: EXPERIMENTAL, PROPOSED. Not accepted by the owner. Everything added in this branch is UNREVIEWED unless section 8 states otherwise. Not a safety system.
Date: 2026-11-01.
Branch: `integration/meta-local-first`, created from the verified authoritative HEAD `7b02057df30e532dd2ded477a97093744b1d9b09` (tree `4c9e6f25b32f93e551e4d7feb7b0535d122834b1`). Meta Lab source commit read: `be32c1d` (reviewed `404447d`, `b7acabf` evidence-only).

## 1. What this branch is
One application: the authoritative MineGuard console and backend (RF-DETR local inference, deterministic risk kernel, feeds, alerts, incidents) plus four small, selected additions from the Meta Lab. The Meta Lab was NOT merged and stays a separate experimental worktree.

| Addition | Where | Nature |
|---|---|---|
| Provider status, read-only | `backend/mineguard/providers.py`, `GET /api/providers`, Configuración → "Proveedores de percepción y explicación" | states fixed by construction; no client, no credential read, no control |
| Cost Governor (pattern) | `backend/mineguard/costgov.py` + `rawjson.py` (ported from the lab `governor.py`; `rawjson.py` identical; `costgov.py` differs ONLY by the ledger head-marker remediation made in this sprint) | present and tested; its only transport is `NullRealTransport`; not wired to any route that could spend |
| Simulated explanation | `backend/mineguard/explain.py`, `GET /api/incidents/{id}/explanation`, incident drawer → "Explicación asistida (simulada)" | the lab validator, bound to `mg.incident/1`; fake transport; cannot change the incident |
| Tests | `backend/tests/test_costgov_*.py`, `test_unified_providers_explain.py`, `frontend` build, `e2e/unified_acceptance.mjs` | |

## 2. Reconciliation matrix (CONFIRMED against both source trees)
| Capability | MAIN implementation | META implementation | Authoritative owner | Contract | Decision | Evidence |
|---|---|---|---|---|---|---|
| Video ingestion | server-side registered MP4 only (`test-assets/*.mp4`, SHA-pinned, AVFoundation decode); no browser upload | browser-only `<video>` import (MP4/WebM), metadata JSON to server | MAIN | — | REUSE_MAIN; browser import DEFER (privacy/retention decision) | `app.py` `_State.scan`, `decode.py` |
| WebM | not supported (AVFoundation, single MP4 route) | accepted by the lab UI; duration fix in the lab | MAIN (MP4) | — | KEEP_EXPERIMENTAL (lab); not ported | G5 record in the lab evidence |
| Webcam | `webcam.py` sessions, privacy ON default | none | MAIN | `mg.obsset.vs01` | REUSE_MAIN | — |
| Inference | RF-DETR Nano, weights hash-checked | fixtures + documented-format strings (no inference) | MAIN | V-09/V-21/V-23 | REUSE_MAIN; fixtures never enter production paths | `detector.py` |
| Observation schema | `mg.obsset.vs01` | `mg.obsset.metalab.x1` (origin `simulated_provider_response`) | MAIN | refused by the canonical validator by design | DEFER; edge projection stays provisional, no canonical change | `MG-META-002` §5 |
| Taxonomy / tracking | `taxonomy.py`, `risk/tracker.py` | concept map, ids never tracks | MAIN | `mg.cls.v0` | REUSE_MAIN | — |
| Risk kernel / zones / events | `risk/kernel.py` `mg-risk-kernel` | consumed MAIN's kernel unmodified | MAIN | — | REUSE_MAIN; no second kernel | — |
| Alerts / incidents / evidence / export | `alerts.py`, `incident.py`, `mg.incident/1` | `metalab.incident.v0` | MAIN | `mg.incident/1` | REUSE_MAIN; the lab shape is only a validator input view (`explain.muse_view`) | `incident.py` |
| Provenance | `provenance.py`, classification `synthetic/restored/…` | provenance state vocabulary | MAIN | — | ADAPT: provider states exposed read-only | `providers.py` |
| Operations console | React (`frontend/src`) | static page | MAIN | — | REUSE_MAIN; lab UI NOT ported | — |
| Explanation | none | `muse.py` fake transport + strict validator | MAIN for the record, META for the validator | `mineguard.explanation.v0` | PORT_META (validator) + ADAPT (input view); no real call | `explain.py` |
| SAM 3.1 adapter / wire parser | none | `sam31.py`, `sam31_wire.py`, `provider_contracts.py` | META | lab extension only | KEEP_EXPERIMENTAL (no consumer in MAIN; would feed simulated data into a real path) | — |
| Cost Governor | `netguard.py` (egress guard) | `governor.py` (ledger, budgets, kill switch) | META (pattern), MAIN (egress) | — | PORT_META (logic unchanged except the ledger head-marker check; see review 3) | `costgov.py` |
| Comparison / benchmark | none | `compare.py`, SYNTHETIC_COMPARISON_ONLY | META | — | REJECT for now (`detection_metrics` computes accuracy metrics; open finding in R5b) | R5b |
| Evidence export | `mg.incident.export/1` | `metalab.evidence.v1` | MAIN | — | REUSE_MAIN | — |
| Configuration | env + loopback app | `MG_META_*` env at start | MAIN | — | `costgov` keeps `MG_META_*` names; the provider route deliberately ignores the environment | `providers.py` |
| Dependencies | pinned in `requirements.freeze.txt` | none new | — | — | no new dependency | — |
| Security / privacy | local session token, netguard, body limits | CSP, loopback | MAIN | — | REUSE_MAIN | — |

## 3. Why only these four additions
The authoritative product already provides the complete real-local journey (registered MP4 → RF-DETR → risk kernel → zones/events → alerts → incident → timeline → evidence → JSON export). The value the Meta Lab adds without contradicting it is (a) an honest, inspectable statement of what can and cannot run, (b) a cost-containment pattern ready for a future real transport, and (c) a validated, subordinate, SIMULATED explanation. Porting its UI, its fixtures or its comparison would either duplicate stable components or place simulated data beside real inference.

## 4. Invariants (tested)
- No network client exists in `explain.py`, `providers.py`, `costgov.py`, `rawjson.py` (AST scan).
- Provider states do not depend on the environment: credentials and `MG_META_*` flags set in the environment change nothing.
- No HTTP route changes provider or spend configuration (POST/PUT/PATCH/DELETE on `/api/providers` → 404/405).
- The explanation cannot change the incident (record equal before/after), carries `decided_by: deterministic_policy`, is labelled SIMULATED_EXPLANATION, and `real_muse_call` is false.
- Every scripted fault of the fake transport (13 modes) is rejected, never repaired.
- A UNKNOWN interval is described as unknown; no "verde/seguro/sin riesgo" language is accepted.
- The governor is NOT shown as a guarantee: `zero_charge_guaranteed` is false and the UI says so.

## 5. Local inference (RF-DETR) — what is VERIFIED
- CONFIRMED: the weights file in `models/` matches `models/rf-detr-nano.pth.sha256` and `MANIFEST.json`; `GET /api/providers` reports `LOCAL_READY` only when the application's own weights check returns `verified=true`. This is a hash check; it does not instantiate the detector (accepted limitation of review 4).
- CONFIRMED (browser journey `e2e/unified_acceptance.mjs`, 17/17): the registered nonpersonal MP4 is analysed by RF-DETR Nano on this machine through the product's own multi-source path (counted samples, provenance `real_inference`, model named); a stored single-model analysis of the same file (742 frames, stride 30) completes and its export is labelled `model_inference`, never `synthetic_scripted`.
- CONFIRMED: no RED was faked. Under the real model the class histogram of the first samples was `{vehicle: 4, person: 1}` and no risk event was produced in a full-frame operator zone within 90 s; the step is recorded as NOT_EXERCISED. The RED/YELLOW incident in the journey is the scripted drill, labelled SINTÉTICO everywhere.
- NOT CLAIMED: RF-DETR accuracy (not validated), physical distance, collision prediction.

## 6. Meta status
- No Meta, Baseten, Modal or Muse client exists in this branch. `GET /api/providers` states `META_DISABLED`, `CREDENTIALS_NOT_CONFIGURED`, `BILLING_NOT_APPROVED`, `LIVE_NOT_VERIFIED` for Meta; `CREDENTIALS_NOT_CONFIGURED` and `BILLING_NOT_APPROVED` for Baseten and Modal; `SIMULATED` for Muse. These are fixed by construction and independent of the environment (tested with credentials and `MG_META_*` flags set).
- The cost block says 0 authorized external calls, US$ 0, transport `NullRealTransport`, and states that a local governor does NOT guarantee zero charges. The Meta documentation read in an earlier sprint documents no hard spend cap; the governor's limits bind only this process.
- External calls made: 0. Spend: US$ 0. The Meta Lab's SAM 3.1 adapter and wire parser stay in the experimental worktree.

## 7. WebM (G5)
- Root cause CONFIRMED (details in the lab evidence `docs/meta-lab-evidence/night02/REVIEW.md` of the Meta Lab worktree): the clip written by `scripts/make_demo_clip.mjs` is a MediaRecorder WebM with no Duration and no Cues; Chrome reports `Infinity`, other browsers `NaN`, and the lab loader handled only `Infinity`.
- Fix and regression tests live in the experimental lab (commit `404447d`): the loader resolves NaN and Infinity or rejects with the exact reason without inventing a duration; a pure-JS patcher inserts the missing Duration (`ffprobe`: N/A → 3.65 s on the owner's file). Review R5b found the patcher unsafe in the general case (it extrapolates the last frame and does not adjust SeekHead/Cues offsets) and an initial-run race in the lab page: both are OPEN in the lab and NOT ported.
- This branch's backend decodes MP4 only (AVFoundation); a WebM cannot be analysed here. Optional local conversion, if the owner already has `ffmpeg`: `ffmpeg -i in.webm -c:v libx264 -pix_fmt yuv420p out.mp4`. Nothing was installed.

## 8. Evidence (all exit 0 unless stated; files in `docs/unified-evidence/`)
| Check | Result |
|---|---|
| Authoritative + new backend suite (`backend/tests`, 47 modules) | 1276 passed, 1 skipped (a conditional skip, not counted as a pass), 0 failed |
| Frontend typecheck / vitest / build | clean / 87 passed / built |
| Unified mutation harness (`scripts/mutation_check_unified.py`) | baseline 100 passed; 28/28 mutants killed |
| Browser journey `e2e/unified_acceptance.mjs` (isolated server, real RF-DETR) | 17/17; 2 NOTEs (NOT_EXERCISED real-model incident explanation; no RED from the real MP4) |
| Authoritative browser suites (`product07_acceptance`, `mvp_acceptance`, …) | NOT_RUN in this sprint (host load 7–28); the backend suite includes the product-07 backend tests |
| Meta Lab (separate worktree) | pytest 299, V02 E2E 27/27, product E2E 51/51, mutation 102/102 — see its own evidence |
Screenshots (real, headless Chrome): `screenshots/u1_providers.png`, `u2_real_analysis.png`, `u4_explanation.png`. Hashes: `SHA256SUMS.txt`.

## 9. Independent reviews (4 of 4 used) — see `docs/unified-evidence/REVIEW.md`
Meta Lab R5 FAIL (4), R5 confirmation FAIL (4, open in the lab), integration review 1 FAIL (6, remediated), integration final review FAIL (5: 3 blocking OPEN, 1 test, 1 documentation fixed). **There is NO independent approval of this branch.**

## 10. Known open defects of this branch (CONFIRMED, not remediated: review budget exhausted)
1. Explanation of incident A can be drawn for one render under incident B (`ExplanationPanel` state is cleared in an effect, not keyed by incident).
2. The video-inspection check is a regex: `Se evaluó el video completo.` is accepted by the validator (the fake transport never produces it).
3. `Ledger.replay` accepts a `0 <non-zero hash>` head marker when the ledger file is absent (the governor is connected to no route).
4. The "truthful origin for every class" unit test is weak for restored / repeated-source records.

## 11. Owner decisions
1. Authorize one more review cycle (and the three small fixes above) before any acceptance; or accept the limitations.
2. Decide whether the Meta Lab's open findings (initial-run race, WebM patcher, `detection_metrics`, export URL leak, ledger head) are fixed in the lab or the lab stays frozen.
3. Decide on a browser video import into the authoritative app (privacy/retention: it would write media to disk).
4. G-1: canonical schema change for provider observations (recommendation: keep the edge projection).
5. Before any real call: confirm a provider-side hard spend cap or prepaid mode; then a separate, separately authorized vertical (one capped single-image discovery call).
6. Merge and acceptance are reserved to the owner; nothing was pushed or merged.
