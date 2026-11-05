# MG-PROVENANCE-001 — Provenance Register and Dependency/License Review Checklist

Status: PROPOSED. Date: 2026-10-20 (updated in MG-02R). No review below constitutes legal clearance. Register status of every third-party item: **NOT REVIEWED**.

## 1. Register entry fields (required before any asset is used)
`prov_id`; kind (code-dependency | model | weights | dataset | video | image | icon | font | codec | script | observation-set | evidence-derivative | document); name; exact version; source URL (if any);
retrieval date; sha256; SPDX license + license text location; copyright holder; redistribution terms; attribution/notice duties; patent notes; trademark notes;
how it is used (shipped | dev-only | experiment-only); review status (NOT REVIEWED | APPROVED | APPROVED-WITH-CONDITIONS | REJECTED | NEEDS-OWNER); reviewer; date.
**Rule:** no register entry, no use. Cross-project reuse (any other Orbit/Compass/FinScope/employer material) is a third-party import requiring explicit owner approval.
Hand-authored or generated observation sets are registered (kind `observation-set`) with their authoring method; the `meta.provenance_id` of an ObservationSet points to this entry (MG-DATA-001 §7). Evidence stills derived from a source video inherit that source's entry and retention class.

## 2. Synthetic media rule
Synthetic media is not "original" by assertion. A synthetic video or observation set is usable only when the register records: the generation script (own file, hash),
its dependencies and their licenses, the seed and exact command, every source asset (fonts, textures, images) with its own license, and the sha256 of each output.
Without that entry the media is UNVERIFIED and unusable. No such media exists yet, and no generator exists (creating one is MG-03 work needing separate authorization).

## 3. Seeded entries
| prov_id | Item | Kind | Status | Notes |
|---|---|---|---|---|
| prov_001 | Host baseline: Mac mini Mac16,11, Apple M4 Pro, 24 GB, macOS 27.0.1 | observation | CONFIRMED 2026-10-20 (read-only `sw_vers`, `sysctl`, `system_profiler`) | Serial/UUID not recorded. The version triple (27.0.1 / build 26A434 / Darwin 27.0.0) was recorded as observed; the 2026-10-21 re-run returned identical values (the oddity remains unexplained but is reproducible; finding F-26 deferred) |
| prov_002 | MG-02 / MG-02R documents (this document set) | document | PROPOSED | Drafted by an AI assistant (Claude Code) under owner authorization, from owner-supplied material; copyright status of AI-assisted text is an open question (G-7) |
| prov_003 | Reference image 1: "Arquitectura, Tecnologías, Mockup y Plan de Trabajo" | image | NOT REVIEWED | Owner reports AI generation; rights unknown; contains photographic-style mining imagery; internal exploratory reference only; not reused in product; not stored in this repo |
| prov_004 | Reference image 2: "Arquitectura de software – MVP" | image | NOT REVIEWED | Same as prov_003 |
| prov_005 | News photograph (ITMG/voi.id) referenced in owner's text | image | **REJECTED for use** | Rights unreviewed; must not be used |
| prov_006 | Owner-supplied plan, competitive-analysis text, and design claims | document | UNVERIFIED | Source of dates, license and competitor claims; none independently checked |

## 4. Candidate technologies (none approved; none installed; none downloaded)
| Item | Owner-reported license (UNVERIFIED) | Status | Verification still needed |
|---|---|---|---|
| React, TypeScript, Vite | MIT (React/Vite per owner) | NOT REVIEWED | Versions, transitive deps, license text |
| FastAPI (+ Python deps) | MIT | NOT REVIEWED | Transitive deps (e.g., Starlette, pydantic, uvicorn) |
| Frame-table / PTS provider | — | **NO PROVIDER SELECTED** | FS-01 compares candidates: OpenCV alone (per-frame PTS exposure UNVERIFIED), FFmpeg-family tools or bindings, others. Each candidate needs license, bundled-binary, codec/patent (G-6) review before adoption |
| OpenCV (≥4.5; prefer headless) | Apache-2.0 | NOT REVIEWED | Wheel contents (bundled FFmpeg/Qt), codec exposure |
| Pillow | MIT-CMU | NOT REVIEWED | License text |
| Pytest, Vitest | — | NOT REVIEWED | Licenses |
| Canonical JSON (RFC 8785) implementation | — | NOT SELECTED | FS-08 (library or own implementation; either is registered) |
| Vision model/weights | — | **NO MODEL SELECTED** | Entire G-4 process |
| Ultralytics models/code | AGPL-3.0 or Enterprise (owner-reported) | **EXCLUDED pending owner review** | — |
| Trackers (ByteTrack, DeepSORT, forks) | Mixed/unknown | NOT REVIEWED; no tracker stage selected | Lineage of each implementation |
| Video.js, hls.js, Chart.js, Docker | Various | NOT NEEDED for MVP | — |
| Meta models ("SAM 3.1", "Muse Spark") | Unknown | UNVERIFIED names; BLOCKED by G-5 | Out of scope pre-event |

## 4a. Environment facts not yet verified
CONFIRMED 2026-10-21 by read-only checks (MG-03a): Python 3.14.6 (mise) plus Homebrew python@3.13/3.14/3.15; Node 24.18.0; npm 11.16.0; pnpm 11.15.1; uv 0.13.1; Homebrew 7.0.9; git 2.54.0; 96 GiB free disk; FileVault On; `ffmpeg`/`ffprobe` absent; only camera listed is "Insta360 Virtual Camera". Still unverified: camera permission state and a physical camera feed. Package and model candidates remain NOT REVIEWED in this register until the owner approves a BOM entry step (MG-03a produced a proposed BOM in the session report only).

## 5. Dependency / license review checklist (per item)
1. Identify name, exact version, hash, source URL; include transitive dependencies from a lockfile scan.
2. Record the SPDX license and read the actual LICENSE/NOTICE text (not a README badge).
3. Classify: permissive (MIT, BSD, Apache-2.0, ISC, MIT-CMU) = candidate; weak copyleft (LGPL, MPL) = owner decision; strong copyleft (GPL, AGPL, SSPL) = **not approved without owner review**;
   custom/non-commercial/research-only = blocked from shipped artifacts, and from experiments unless the owner decides otherwise (D-11, recommended: excluded).
4. Weights are separate from code: review weights license, training-data terms, acceptable-use policy, redistribution conditions.
5. Datasets: images + annotations licenses, privacy/consent terms.
6. Bundled binaries (e.g., FFmpeg, Qt in wheels): verify the actual build's license set.
7. Codec/patent-pool exposure (H.264/H.265): record as open legal item (G-6); prefer OS decoder where feasible.
8. Attribution/notice obligations; generate a third-party notice file.
9. Trademark/branding rules of the dependency.
10. Redistribution mode (local only vs shipped vs hosted); note AGPL network-use triggers.
11. Maintenance/security status (CVEs, abandonment).
12. Record reviewer, date, decision.
13. Re-review on every version bump.
14. Open-source license ≠ patent clearance. State this in every review outcome.
