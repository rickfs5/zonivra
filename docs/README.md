# Orbit MineGuard / Zonivra — Documentation Index

Status: PROPOSED (MG-02 documentation foundation, corrected by MG-02R). Date: 2026-10-20. Scope updated 2026-10-21 (MG-03a): real MP4 and webcam inference are mandatory P0; replay is the testing path; risk-engine implementation stays NO-GO until audit findings H-1..H-6 are corrected and reviewed.
**Current implementation status (authoritative, 2026-10-25): see `MG-PRODUCT-001-operating-guide.md`, `MG-VERTICAL2-001-multi-source-monitoring.md` and `MG-MVP1-ACCEPTANCE-001.md`.** IMPLEMENTED (candidate, not accepted): MP4 real inference (VS-01), webcam sessions with Privacy ON/OFF (`MG-PRIVACY-001`) and acknowledged, idempotent lifecycle messages with prefix/gap/tail coverage accounting, the original IoU tracker 0.3.0, user-drawn zones, the deterministic risk kernel `mg-risk-kernel/2.0.3` (`MG-RISK-002`), the scripted RED simulation, safe session deletion, **multi-feed video monitoring (one shared model, three feeds validated, four measured, five RESOURCE_BLOCKED), the alert engine (in-app, browser notification and local sound with consent), the operator and developer dashboards**, and the ten-area Spanish UI. NOT implemented: RTSP, physical multi-camera, SMS/WhatsApp/e-mail/webhook delivery (the webhook is a documented, disabled contract), heat maps, calibration, a better tracker. MVP 1 is NOT accepted (the full gate has not run: host resource conditions; independent reviews are listed in `MG-VERTICAL2-001` §16). Older sections of the other documents that say a feature is "not implemented" describe the state when they were written (historical) and are superseded by this paragraph.

## Legends (defined once, here)
### Evidence labels (used for findings and facts)
| Label | Meaning |
|---|---|
| CONFIRMED | Directly observed on this machine, or stated by the owner as a binding directive |
| PROPOSED | A draft design/decision awaiting owner acceptance |
| UNVERIFIED | A claim from owner-supplied material or recollection, not independently checked |
| BLOCKED | Cannot proceed until external evidence or authorization exists |
### Decision statuses (used in MG-DECISIONS-001 and gates)
| Status | Meaning |
|---|---|
| APPROVED | Formally approved or directed by the owner (the source is recorded) |
| RECOMMENDED | A proposal; not approved |
| OPEN | No proposal yet |
| BLOCKED | Waiting on external evidence |
| RETIRED | Superseded or resolved by another row (the replacement is named) |
Gates: legal/eligibility/authorization gates are `G-n` (MG-GATES-001, statuses OPEN / BLOCKED / NOT AUTHORIZED). Roadmap milestone gates are `MS-n` (MG-SPEC-001 §13).

## Documents (12 files; the inventory was ratified by the owner in MG-02R, AP-12)
| Document | Purpose | Status |
|---|---|---|
| `../CLAUDE.md` | Session rules and prohibitions | PROPOSED |
| `MG-SPEC-001-p0-specification.md` | Product, scope (P0/X1/P1/P2), requirements, acceptance, architecture, network boundary, UX, feasibility assumptions, roadmap | PROPOSED (v0.2) |
| `MG-DATA-001-canonical-contracts.md` | Canonical JSON profile, hash boundaries, identity derivation, time and geometry arithmetic, observation/zone/policy/evaluation/evidence/review contracts, validation catalog | PROPOSED (v0.2) |
| `MG-RISK-001-risk-policy-spec.md` | Four evaluation states, bridging, built-in rules, tracks, episode machine and handoffs, 59-scenario catalog | PROPOSED (v0.2) |
| `MG-GATES-001-open-gates.md` | Employer-IP, trademark, patent, model licensing, eligibility, codec, authorization gates | OPEN / BLOCKED |
| `MG-PROVENANCE-001-register-and-checklist.md` | Provenance register and dependency/license review checklist | PROPOSED (register seeded, mostly empty) |
| `MG-DECISIONS-001-decision-register.md` | Approved vs recommended vs open decisions; MG-02R findings disposition and change log | LIVE |
| `adr/0001-local-first-operation.md` | ADR (amended in MG-02R) | **PROPOSED** |
| `adr/0002-deterministic-risk-evaluation.md` | ADR (amended in MG-02R) | **PROPOSED** |
| `adr/0003-interchangeable-vision-adapters.md` | ADR (aligned in MG-02R) | **PROPOSED** |
| `adr/0004-provenance-requirements.md` | ADR (aligned in MG-02R) | **PROPOSED** |

## Deliberately not written yet (deferred, with triggers)
| Item | Written when |
|---|---|
| Video-source / multi-feed detailed design; backpressure ADR; live `timestamp_anomaly` handling | Before any P1 implementation |
| Vision model evaluation and license evidence sheet | First step of X1 (any external text access needs owner approval) |
| Performance budget | After baseline measurements (benchmark report) |
| Full UX reconciliation | After P0; must-haves are in SPEC §11 |
| Competitive landscape, differentiation strategy | After P0; source material is owner-supplied and UNVERIFIED |
| JSON Schema files (`*.schema.json`) | MG-03 (contracts exist here as prose and examples; creating files is not authorized in MG-02R) |
| Detailed patent / trademark backlog | Short list lives in MG-GATES-001 |
| Verbatim owner authorization texts | Would require a new file; deferred (the register cites authorizations by title) |

## MVP 1 stabilization and MG-VERTICAL-02 documents (added 2026-10-25)
| Document | Purpose |
|---|---|
| `MG-PRODUCT-001-operating-guide.md` | Operating guide (authoritative implementation status) |
| `MG-PRIVACY-001-privacy-and-retention.md` | Privacy ON/OFF, retention, deletion |
| `MG-MVP1-ACCEPTANCE-001.md` / `MG-ACCEPTANCE-STATE.json` | Acceptance record; its test inventory is GENERATED (`scripts/acceptance_counts.py`) |
| `MG-MVP1-STABILIZATION-001.md` | Stabilization and privacy acceptance record (historical) |
| `MG-VERTICAL2-001-multi-source-monitoring.md` | Cycle-20 corrections, multi-feed, alerts, dashboards, capacity, manual acceptance (PROPOSED, not accepted) |
| `MG-CONSOLE-001-operations-console.md` | MG-PRODUCT-VERTICAL-03: the Operations Console (ten areas, component boundaries, honesty rules, how to start and validate; PROPOSED, not accepted) |
| `MG-ROADMAP-001-mvp2-mvp3.md` | Original MVP 2/3 plan; now partly implemented (see its status line) |
| `adr/0005-shared-inference-worker.md`, `adr/0006-late-declaration-sidecar.md` | Architectural decisions of MG-VERTICAL-02 (PROPOSED) |

## VS-01 documents (added 2026-10-24)
| Document | Purpose |
|---|---|
| `MG-VS01-runbook-and-acceptance.md` | Launch/test commands, timestamp mapping, acceptance matrix, observed results |
| `MG-VS01-bom-and-provenance.md` | Dependency BOM, licence-gate findings, model/weights/video provenance |

## Reading order
1. SPEC-001 → 2. DATA-001 → 3. RISK-001 → 4. GATES-001 → 5. PROVENANCE-001 → 6. DECISIONS-001 → 7. ADRs.
