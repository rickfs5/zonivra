# MG-PRODUCT-VERTICAL-06 — Operational overview and incident investigation

Status: IMPLEMENTED as a local-first, experimental increment; NOT independently accepted, NOT production-ready, NOT a safety system.
Date: 2026-10-28 (baseline `f429c82`).

MineGuard remains a decision-support review tool. Nothing here measures physical distance, predicts collisions or certifies safety. Evidence labels follow `docs/README.md`.

## 1. What was added

| Area | CONFIRMED behaviour (tests and browser journey) |
|---|---|
| Operator tile (Resumen) | Per source: stable source ID, source type, detector id, last observation (source time) and freshness, counts of the LAST fresh sample (absent, never zero, when stale/stopped), zones, open incidents (grounded in the alert store), and one of five duplicate states. |
| Incident record | `GET /api/incidents/{alert_id}` returns `mg.incident/1`, a pure projection of stored facts (alert + event, the source feed if still present, the stored run behind a simulation). |
| Investigation panel | In the incident drawer: why the risk state (deterministic rule, semantics/engine version, causes; AI never decides severity), observed facts apart from derived conclusions, two-track timeline, UNKNOWN intervals, evidence/trust, provider and source provenance, limitations, JSON export. |
| Timeline | Observation time (source clock, µs) and processing time (UTC of this application) are separate tracks. Entries: risk entry, UNKNOWN interval, episode closed, alert created, provenance, delivery outcomes, acknowledged, resolved. SYNTHETIC / RESTORED flags per entry. |
| Export | `GET /api/incidents/{id}/export` → `mg.incident.export/1`; the UI downloads that. It is a FRESH projection of the same incident (own `generated_utc` and current source context), not a byte copy of the panel; no image, footage or base64. |
| Containment | MG-DELETE-035, MG-RISK-035, MG-PROV-035 (section 4). |

### Timeline kinds NOT recorded by this version (declared, never invented)
`source_started`, `source_stopped`, `observation_received`. The record states them in `timeline.unsupported_kinds`; the screen shows "No registrado por esta versión".

### Duplicate-source states (MG-PROV-035)
`overlap.duplicate_status`: `inactive` (feed not playing/paused) · `active_overlap` (another feed of the same file is playing/paused now) · `historical_repeated` (overlapped earlier in this run or launched as a repeat, not now) · `unknown` (overlap record truncated or launch relation never recorded) · `none`. The run-level evidentiary label stays conservative and sticky (MG-PROV-027).

## 2. Incident record: honesty rules
- `classification`: `synthetic` (scripted drill only; never real evidence) · `repeated_source_simulation` (REAL detector inference over a file another feed also played) · `restored` (read back from disk; history, not a live alert; accepts no delivery report) · `live_feed_metadata`.
- `evidence.availability` is always `metadata_only`; `images_included` is always `false`.
- `evidence.local_consistency`: `pass` only when the stored run was read from one immutable snapshot whose components agree; `fail` when it is missing/unsafe/contradictory (with a conflict message id); `not_checked` when there is no stored run (live feed alerts, restored alerts).
- `evidence.authenticity` is always `not_independently_verified`. A local SHA-256 digest is a consistency aid, not proof of authenticity.
- `unknown_intervals.available=false` when there is no coherent stored evaluation to read them from; the panel then says so and does NOT assume GREEN.
- `delivery` is reported per channel exactly as recorded; acknowledge and resolve are operator actions and never delivery.
- `risk.semantics_version`/`engine_version` are those of the RUNNING build at projection time (`versions_basis`), `null` for a restored record; the event's producing version is not stored.
- Provider: `webcam_model_inference` (browser-declared camera; origin client-declared), `scripted_simulation` (no model, no camera), `model_inference` (detector over a replayed MP4; model id from the weights manifest), `repeated_source_simulation`. Detector accuracy is not claimed.

## 3. Safe demonstration (test-owned server and storage)
```
cd <project root>
MG_E2E_PORT=8771 bash scripts/run_mvp_e2e.sh e2e/product06_acceptance.mjs
```
This starts an ISOLATED server with its own storage (never the owner's live server), drives the real UI in headless Chrome, writes screenshots and `results.json` to `logs/product_vertical06/`, and removes the storage. Fixture: the registered local clip replayed as feeds (a concurrency simulation, not cameras) plus the existing scripted YELLOW→RED drill (SYNTHETIC).

Manual use: start MineGuard as usual, open the console → Resumen (source tiles) → Simulaciones → "Simulación ROJO" → open the alert → "Investigación del incidente" → reconocer / resolver → "Exportar JSON del incidente".

## 4. Critical containment (bounded, not a redesign)
- **MG-DELETE-035 (data loss)** — OPEN as a defect, CONTAINED in the product. `POST /api/sessions/delete` and `POST /api/sessions/{id}/media/delete` answer `503 mg.refusal.delete_temporarily_unavailable` before the request body is read, parsed or validated (an oversized body is refused earlier by the application's global size limit with 413) and touch nothing; the Sessions view says deletion is temporarily unavailable and disables every delete control. The removal logic is dormant (`SESSION_DELETE_ENABLED=False`); its older tests switch it on for their own isolated server only. Re-enabling requires removal through pinned directory descriptors (`unlinkat`) and a new review.
- **MG-RISK-035 (contradictory evidence)** — CONTAINED at the single entry point for risk derivation: `export.json` must equal its component documents and the observation digest must equal the manifest's, otherwise the source is refused (`409 mg.refusal.run_inconsistent` / omitted from `/api/events`) and an incident built on it reports `local_consistency=fail`. This is a local consistency check, not authenticity; an attacker who rewrites every file consistently is outside the claim. A `provenance.json` component is not part of the snapshot (residual).
- **MG-WCAM-035** — UNCHANGED, preserved as a finding: producer equations (`submitted = accepted + rejected + invalid`, closure mapping, tail basis) are not all enforced, and `session_format` is a self-declared marker. Historical unverifiable sessions remain UNVERIFIED. No server-signed identity was built; an attacker with arbitrary write access to storage is outside the authenticity guarantee.
- **MG-PROV-035** — FIXED (small): an idle/stopped duplicate no longer marks a single active playback as repeated or overlapping.

## 5. Optional proposed extensions for a future Meta integration (UNVERIFIED, not implemented)
The incident record is source-agnostic. A future, separately authorized adapter could add OPTIONAL members without changing existing ones: `provenance.provider.kind="external_model"` with `model_id`/`version`; `evidence.regions[]` (mask/box references, image-space only); `explanations[]` (text from a language model, always labelled model output and never used for severity). Integration points: the canonical observation set (`observations.json`) feeds the same deterministic risk kernel, so alerts and this incident view need no change. No Meta API, credential or call exists in this repository state.

## 6. Residual limitations
Timeline lacks source start/stop and per-observation entries; counts on tiles are only those of the last fresh sample; `provenance.json` is outside the snapshot; per-source verify and listing routes still read by path (docs/MG-CONSOLE-001 §15.2); deletion is unavailable; real-detector precision and RF-DETR accuracy are NOT validated; full acceptance gate BLOCKED_RESOURCE (memory/swap) and manual A1–A12 pending.

## 7. Independent review result (cycle 35, attempt A36) and post-review state
Reviewed commit `004b9ff` (220 files as sent, tree SHA-256 `3688337d…b5a2`): VERDICT FAIL, 0 blocker, 5 major, 1 minor; verbatim report in `logs/codex/cycle36.last_message.md`. No further review was run.
- **OPEN, not corrected here:** MG-RISK-036 (risk input validation is a coherence check, not a full schema/digest/manifest-identity validation; `provenance.json` is outside the snapshot; coordinated edits of source and bundle pass) and MG-WCAM-035 (unchanged).
- **Corrected after the review — UNREVIEWED:** MG-INCIDENT-036 (provider/classification per source kind; version basis stated), MG-INCIDENT-037 (nested event identity enforced on load; consistency `pass` requires the same episode in the stored evaluation), MG-DELETE-036 (disabled routes answer before the body is read), MG-EXPORT-036 (documented and UI-worded as a fresh projection).
- MG-PROV-035 was judged resolved by the reviewer; MG-DELETE-035's data-loss race is contained (unreachable removal code).
