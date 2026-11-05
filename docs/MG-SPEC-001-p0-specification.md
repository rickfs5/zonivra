# MG-SPEC-001 — P0 Specification (product, scope, requirements, acceptance, architecture) — v0.2, MG-02R

Status: PROPOSED. Date: 2026-10-20. Evidence labels are defined in README.
Implementation status: **NONE**. Every requirement below describes intended functionality; nothing has been demonstrated.
**Scope update (MG-03a, 2026-10-21, owner-approved):** real MP4 inference and real browser-webcam inference are now **mandatory P0** (target 2026-10-24; 2026-10-25 reserved for stabilization and acceptance). The replay adapter is retained as a testing mechanism and is **not sufficient** for final acceptance. Audit findings H-1 to H-6 remain **UNRESOLVED**: implementing the risk engine against MG-RISK-001 v0.2 is NO-GO until they are corrected and reviewed. No earlier acceptance requirement has been removed (see MG-DECISIONS-001 §G).
Resolves audit findings F-07, F-11, F-13, F-14, F-18, F-20 (partially: ADR text), and the P0 parts of F-05/F-19 (see MG-DECISIONS-001 §E).

## 1. Product definition (PROPOSED)
A local-first video review tool for mining-safety **decision support**. It converts *observations* (synthetic, human-annotated, or — experimentally — model-produced) into *reproducible, explainable, evidence-backed evaluations and events*
under *explicit, versioned, deterministic policies*, for human review. The initial UI language is **Spanish** (owner-approved), with structured message identifiers for future languages.
Differentiation hypotheses (all UNVERIFIED, none proven; competitors reportedly offer evidence and rules too):
1. Evidence-first traceability: each alert traces to source, observations, timestamps, rule/engine versions, and evidence.
2. AI observations never authorize or determine safety actions; explicit rules evaluate risk.
3. Reproducible scenario review: same inputs, rule versions and engine version ⇒ identical evaluation.
A future Risk Policy Studio and policy-version comparison are post-MVP (D-14, recommended); the pure evaluation function keeps the door open.

## 2. Claims policy (PROPOSED; binding on UI copy and documents, in every language)
Forbidden claims: prevents accidents; real-time protection; collision prediction; physical distances/speeds without calibration; "AI-detected" for synthetic or annotated data; certified/compliant; "system active/safe" style status; GREEN described as "safe" or "empty".
Required: persistent origin banner (`mg.banner.synthetic_replay` | `mg.banner.human_annotated` | `mg.banner.model_inference_experimental`); limitations panel (`mg.lim.decision_support_only`, `mg.lim.image_space_only`).

## 3. Scope tiers and cut-lines (PROPOSED; owner-directed structure)
Real inference (formerly tier X1) is now part of P0. Priority after P0 passes milestone MS-3: remaining **P1 → P2**. Nothing in P1/P2 may compromise P0.
| Tier | Scope | Start condition | Minimum viable | Cut order (first cut first) |
|---|---|---|---|---|
| **P0 (mandatory)** | Local web application with (a) recorded MP4 and (b) browser webcam, each analyzed with **real local inference**; shared observation contract, bounding-box overlay, configurable zones, rule engine (4 states), events, evidence, JSON export, determinism, origin banner; the replay adapter stays as the mandatory testing path | MG-03b authorization(s); engine work gated on H-1..H-6 (AP-28) | Full slice, both inference modes | The 17 non-essential P0 scenarios (MG-RISK-001 §14: the 37 essential are never cut) → evidence clips (stills stay) → UI polish. **Never cut:** real MP4 inference, real webcam inference (any reduction needs an explicit owner decision), source-digest binding, determinism test, essential negative/UNKNOWN/refusal scenarios, origin banner, provenance register, artifact separation |
| **X1 → promoted into P0 (MG-03a)** | Real local detector (and tracker stage) behind the adapter contract, labeled `model_inference` (EXPERIMENTAL banner until the owner lifts it) | License evidence complete AND owner approves each install/download (EXP-001 first) AND adapter contract tests (A-P0-19) pass | Time-boxed feasibility experiments EXP-001 (MP4) and EXP-002 (webcam) on 2–3 owner-recorded clips + written result; per-track dwell needs a tracker stage (unselected) or yields `identity_unavailable` | Detector fallback (YOLOX) only; "not run" is no longer acceptable |
| **P1 (live feed)** | `VideoSource` interface + state machine in the architecture; implementation after P0 completion and resource availability | MS-3 passed; time remains after the X1 decision | Simulated stream (P0 file paced by wall clock), state machine, drop counter, one stall→reconnect test | Webcam → fault-injection variants. RTSP stays design-only |
| **P2 (multi-feed)** | Interface only in the architecture; implementation after minimal P1 and spare resources | Minimal P1 done | 2–4 display-only tiles, per-source state, analysis budget of 1 | Skip and document as deferred |
Displaying multiple feeds does **not** imply simultaneous AI inference on all of them. `analysis_stride` is fixed at 1 in P0.

## 4. Non-goals (PROPOSED)
Safety-critical or certified operation; replacing collision-avoidance systems; machinery intervention; calibrated world-space reasoning (distance, speed, time-to-collision); multi-user/cloud sync; model training/fine-tuning;
generative-AI explanations in MVP; RTSP implementation in MVP; Docker/Kubernetes/queues/microservices; historical analytics/report dashboards; Risk Policy Studio UI; custom rule-expression language; SQLite persistence (deferred); any Meta API; any employer asset.

## 5. Requirements (PROPOSED)
| ID | Requirement | Tier | Priority | Acceptance |
|---|---|---|---|---|
| FR-01 | Load a local MP4; record `source_digest`, media facts and the frame table (via a to-be-selected provider); refuse unsupported or non-monotonic input | P0 | Must | A-P0-01 |
| FR-02 | Play/pause/seek/step with annotation overlay; the displayed frame index is declared, and overlay data matches it | P0 | Must | A-P0-02 |
| FR-03 | Polygon zone editor; reject invalid polygons; edits create immutable canonical revisions | P0 | Must | A-P0-03 |
| FR-04 | Deterministic replay adapter; observation set bound to source digest, frame table and dimensions; refuse mismatch | P0 | Must | A-P0-04 |
| FR-05 | Persistent origin banner in UI, exports, evidence | P0 | Must | A-P0-05 |
| FR-06 | Rule engine: pure deterministic evaluation with named built-in versioned rules producing GREEN/YELLOW/RED/UNKNOWN per MG-RISK-001 | P0 | Must | A-P0-06, A-P0-09 |
| FR-07 | Evaluation carries timestamps, rule and engine versions, structured explanations, bounded input references | P0 | Must | A-P0-06, A-P0-10 |
| FR-08 | Evidence generated from deterministic evidence requests; stored in a separate content-addressed manifest; verify step | P0 | Must | A-P0-08, A-P0-20 |
| FR-09 | Event list synchronized with video (click-to-seek, active highlight) | P0 | Must | A-P0-11 |
| FR-10 | Export bundle: evaluation, evidence manifest, review log, run manifest; schema-valid | P0 | Must | A-P0-10 |
| FR-11 | UNKNOWN is explicit everywhere: UI, export, statistics; four buckets and coverage; never GREEN or silent | P0 | Must | A-P0-09, A-P0-18 |
| FR-12 | Reproducibility contract: identical inputs and engine/rule versions ⇒ identical `determinism_hash` and identifiers | P0 | Must | A-P0-07, A-P0-22 |
| FR-13 | Vision adapter contract admits a real detector without engine changes | P0 (was X1) | Must | A-X1-06, A-P0-19 |
| FR-14 | Detector output labeled `model_inference` (EXPERIMENTAL banner) everywhere | P0 (was X1) | Must | A-X1-04 |
| FR-15 | Common `VideoSource` interface with state machine, epochs, gap handling | P1 | Should | A-P1-01..04 |
| FR-16 | Bounded queues; latest-wins for live with every drop recorded; cancellation | P1 | Should | A-P1-05, A-P1-06 |
| FR-17 | Live runs reproducible by replaying the recorded observation log | P1 | Should | A-P1-07 |
| FR-18 | Per-source state, isolation, analysis budget, ANALYZED/DEGRADED/DISPLAY-ONLY | P2 | Could | A-P2-01..07 |
| FR-19 | Spanish UI via a message catalog keyed by `msg_id`; no user-facing literals in evaluation output | P0 | Must | A-P0-21 |
| FR-20 | Mutable human review records stored separately from the deterministic evaluation; UNKNOWN intervals reviewable | P0 | Should | A-P0-20 |
| FR-21 | Real-inference governance: license evidence sheet before any download; recorded owner approval; pinned, hash-verified environment; preliminary-result report | P0 (was X1) | Must | A-X1-01, A-X1-02, A-X1-03, A-X1-05 |
| FR-22 | Recorded MP4 analyzed with **real local inference** (not replay) end to end: frames → detections → tracks → ObservationSet (`model_inference`) → evaluation → events, evidence, export | P0 | Must | A-RI-01, A-RI-04, A-RI-05 |
| FR-23 | Browser webcam analyzed with **real local inference** end to end under the same contract, with bounded live evaluation and privacy-minimal evidence | P0 | Must | A-RI-02, A-RI-03, A-RI-04, A-RI-05 |
| NFR-01 | Operates offline; no CDN/telemetry/external requests at run time | P0 | Must | A-P0-12 |
| NFR-02 | Provenance + license register complete for everything shipped | P0 | Must | A-P0-13 |
| NFR-03 | Limitations documented; claims policy respected | P0 | Must | A-P0-14 |
| NFR-04 | Performance measured and reported (no targets until baseline) | P0 | Must | A-P0-15 |
| NFR-05 | No Meta API or paid-service dependency | P0 | Must | A-P0-16 |
| NFR-06 | Bounded resource use in live/multi modes | P1 | Should | A-P1-06, A-P1-08 |
| SEC-01 | Bind 127.0.0.1 only; Host/Origin validation; per-session token | P0 | Must | A-P0-12 |
| SEC-02 | Credentials never persisted/logged in plaintext (RTSP implementation out of MVP; the rule is tested with a canary) | P1 | Should | A-P1-09 |
| SEC-03 | Data directory permissions, retention class per evidence item, purge command | P0 | Should | A-P0-17 |
| SEC-04 | Network boundary: internet access prohibited at run time; future camera-LAN access only to explicitly authorized, allowlisted hosts | P0 / P1 | Must | A-P0-12, A-P1-10 |

## 6. Main use case (PROPOSED)
"Person and vehicle in a restricted zone." The analyst loads a synthetic MP4, draws zone Z1, loads a replay observation set bound to that video, and runs policy `mg-policy@0.1.0`. Expected: YELLOW when a tracked person dwells in Z1 without a vehicle;
RED when a person and a vehicle occupy Z1 simultaneously; UNKNOWN when inputs are insufficient (with the cause); GREEN only on analyzed frames with no active episode and no UNKNOWN instance.
The analyst clicks an event, seeks to its start frame, inspects the rule trace, incomplete inputs and evidence, verifies evidence, optionally records a review, and exports. Image-space only. The result is a review aid, not a safety determination.
The same flow runs on a recorded MP4 and on a browser webcam session, both with real inference (MG-03a). Replay inputs remain the testing mechanism and never count toward real-inference acceptance.

## 7. Acceptance matrix (PROPOSED; "observable" = a test result or an inspectable artifact; implementation status NONE for all)
### P0
| ID | Criterion | Observable check |
|---|---|---|
| A-P0-01 | MP4 loads; digest, media facts and frame table recorded; unsupported, corrupt or non-monotonic input refused with a message id. The supported container/codec set is fixed by FS-07 and listed in the UI | E2E + refusal fixtures |
| A-P0-02 | (a) Overlay data shown equals the observation for the frame index the UI declares as displayed (0 error). (b) The declared index equals the frame actually rendered: measured by FS-03/FS-04 (burned-in frame counter). Frame-accuracy is claimed **only** for a display path that passes; otherwise the UI steps and overlays using server-extracted frames and labels native playback `mg.ui.approximate_playback` | Logic test; measurement report |
| A-P0-03 | Zones create/edit/delete; invalid polygons refused (MG-DATA-001 §5.6); revisions canonical and hashed | Unit tests (S-52) |
| A-P0-04 | Replay refuses source-digest, frame-table, dimension or frame-count mismatch; adapter reads no pixels | Negative tests (S-32, S-33) |
| A-P0-05 | Origin banner in UI, export and evidence images | Snapshot tests |
| A-P0-06 | All **37 essential** P0 scenarios of MG-RISK-001 §14 pass exactly; the other 17 P0 scenarios are attempted and any unimplemented are listed as deferred | Golden tests |
| A-P0-07 | Two runs and a run after restart yield identical `determinism_hash` and identical manifest digests (wall-clock, evidence and reviews excluded by contract) | Hash comparison |
| A-P0-08 | Every evidence request of every YELLOW/RED event is fulfilled in the evidence manifest; `verify` recomputes `file_sha256`, `pixel_sha256` (re-decoded with the recorded decoder) and the observation dump | Test |
| A-P0-09 | UNKNOWN never becomes GREEN or a silent closure; mutations (UNKNOWN→GREEN, F-bridging, dropped incomplete-input recording) each fail a test | Mutation tests |
| A-P0-10 | Export bundle validates against the schemas (MG-03); contains evaluation (timeline, events, suppressed, dropped tracks, coverage), manifests and origin; contains message ids, no user-facing literals | Validator |
| A-P0-11 | Click-to-seek lands on the event's `start` frame; active events highlighted during playback | E2E |
| A-P0-12 | Works with the network disabled; CSP `'self'`; loopback binding; Host/Origin enforced; no outbound connection attempted | Offline run + header tests |
| A-P0-13 | Provenance + license register complete for all shipped items; no AGPL/GPL item without owner review | Register audit |
| A-P0-14 | Limitations panel and claims policy present; no forbidden phrases in UI catalog or docs | Text scan |
| A-P0-15 | Benchmark report on the M4 Pro baseline (throughput, latency, RSS, CPU) — measurement, not pass/fail | Report |
| A-P0-16 | No Meta/paid-service dependency | Dependency audit |
| A-P0-17 | Data directory created with mode 0700; each evidence item has a retention class; a purge command removes evidence and leaves a verifiable record | Permission + purge test |
| A-P0-18 | UI shows GREEN/YELLOW/RED/UNKNOWN distinctly (UNKNOWN hatched grey); four buckets and coverage displayed and exported; UNKNOWN never counted as an alert or as GREEN | UI snapshot tests |
| A-P0-19 | Adapter contract test suite with a fake adapter; static check that the engine imports no adapter code | Test + static check |
| A-P0-20 | Regenerating evidence or editing reviews changes neither `determinism_hash` nor any event id | Test |
| A-P0-21 | Every `msg_id` used by any artifact has a Spanish catalog entry; no literal user-facing text in the evaluation | Catalog scan |
| A-P0-22 | Identity vectors: `source_id`, `obsset_digest`, `zone_ref`, `event_id`, `evaluation_id` equal the documented vectors across restarts | Vector test |
### X1 criteria (promoted to mandatory P0 by MG-03a; IDs unchanged)
| ID | Criterion |
|---|---|
| A-X1-01 | License evidence sheet (code, weights, training data, acceptable use, runtime) complete before any download (inspectable document) |
| A-X1-02 | Owner approval to download recorded in MG-DECISIONS-001 |
| A-X1-03 | Experiment isolated from the baseline tree; excluded from any freeze unless the gate passed. **MG-03a reinterpretation (not removal):** the detector is now part of the baseline, so "isolated" means a pinned, hash-verified environment directory, and real-inference outputs are kept separate from replay regression fixtures (see A-RI-04, A-RI-05) |
| A-X1-04 | All output labeled `model_inference` EXPERIMENTAL; no accuracy claims |
| A-X1-05 | Result report: ~2 owner-recorded clips, ~100 hand-labeled frames, precision/recall/latency/fps, stated as preliminary small-sample |
| A-X1-06 | Contract test: swapping Replay↔Inference adapter requires zero engine changes |
### Real inference (A-RI; PROPOSED; mandatory P0)
| ID | Criterion |
|---|---|
| A-RI-01 | A recorded MP4 is processed with the selected detector (weights hash verified at run time) and tracker, producing a valid `model_inference` ObservationSet and a full evaluation with events, evidence and export; replay inputs are rejected in this acceptance mode |
| A-RI-02 | A browser webcam session of at least 5 minutes runs through the same detector with live box overlay, a valid recorded observation log, evaluation, events, evidence and export, and meets the EXP-002 latency criteria |
| A-RI-03 | Live evaluation is bounded: each update's cost stays within budget at the session cap (p95 ≤ 100 ms per update), and the final live evaluation equals the batch evaluation of the recorded log |
| A-RI-04 | The manifest records an inference attestation (model id, weights SHA-256, library versions, device); a modified weights file refuses the run; the origin banner shows MODEL INFERENCE |
| A-RI-05 | With checkpoint and backbone pre-cached the run completes with network access denied and telemetry disabled; the dependency closure contains no GPL/AGPL component and matches the BOM and register (A-P0-13) |
### P1 (live feed)
| ID | Criterion |
|---|---|
| A-P1-01 | `VideoSource` conformance tests pass for the simulated source |
| A-P1-02 | States observable: IDLE, CONNECTING, STREAMING, STALLED, RECONNECTING, FAILED, CLOSED, CANCELLED |
| A-P1-03 | 5 s stall ⇒ STALLED ⇒ RECONNECTING with backoff ⇒ new epoch; tracks do not span epochs |
| A-P1-04 | Open episodes at a gap close per MG-RISK-001 §8 (`unbridged_gap`/`epoch_boundary`); the gap is UNKNOWN, never GREEN |
| A-P1-05 | Cancellation completes ≤2 s with no orphan threads/handles |
| A-P1-06 | Latest-wins drops are counted and recorded as `not_analyzed_dropped`; queues bounded |
| A-P1-07 | Live-record → replay produces the identical final evaluation |
| A-P1-08 | 30-minute soak shows no unbounded memory growth (threshold set after baseline) |
| A-P1-09 | A canary credential injected into a simulated source config appears in no config file, log, event, evidence item or export |
| A-P1-10 | Connection attempts occur only to explicitly allowlisted camera hosts/ports; any other destination is refused and logged |
### P2 (multi-feed)
| ID | Criterion |
|---|---|
| A-P2-01 | Each source shows independent state |
| A-P2-02 | Fault in one source does not alter others (fault-injection test) |
| A-P2-03 | Each tile shows ANALYZED / DEGRADED / DISPLAY-ONLY |
| A-P2-04 | DISPLAY-ONLY source emits zero evaluations/events |
| A-P2-05 | Per-source ids cannot collide; global feed shows source ids |
| A-P2-06 | Per-source budget {max analysis fps, queue depth, max resolution} and aggregate cap enforced |
| A-P2-07 | Cancelling one source frees its resources |
Traceability: all 33 requirements map to ≥1 acceptance item; all 50 acceptance items (22 P0 + 6 X1 + 5 A-RI + 10 P1 + 7 P2) map to ≥1 requirement; process-style items (A-X1-01/02/03/05) are mapped to FR-21; A-RI-01..05 are mapped to FR-22/FR-23. For the webcam mode, A-P1-04, A-P1-05, A-P1-06 and A-P1-07 are required in addition (recommended; owner to confirm, MG-DECISIONS-001 §G).

## 8. Architecture (PROPOSED; exploratory images are inputs, not approved design)
```
VideoSource -> FrameIngest -> Orchestrator -> VisionAdapter -> [TrackerStage*] -> ObservationLog (append-only)
 (file|sim|webcam|rtsp*)  (frame table,   (per-source        (Replay | Inference*)  (unselected)              |
                           run_config)     budgets)                                                          v
                                                                         ZoneEngine + RuleEngine (pure, event-time)
                                                                                   |
                                                                      Evaluation  (deterministic artifact)
                                                                       |                       |
                                                  EvidenceEngine (reads source pixels)   Review records (mutable)
                                                       -> Evidence manifest                    |
                                                                  Local Store (canonical JSON + files)
                                                                                   |
                                                              Local API (127.0.0.1) -> Web UI (Review | Policy & Evidence | Sources)
```
`*` = future/experimental. Single local process group; no microservices. Language-neutral interface sketches:
- `VideoSource`: `open()`, `read(timeout) -> FramePacket | Gap | EndOfStream`, `info()`, `close()`; optional `seek(frame_index)` (files); `capabilities {seekable, finite, has_wallclock}`; state machine per A-P1-02.
- `VisionAdapter`: `describe() -> AdapterManifest {adapter_id, version, origin, model_id?, weights_sha256?, params_hash, label_map_digest?}`; `analyze(FramePacket) -> FrameObservation`. The adapter maps detector coordinates and labels into `image_px_v1` / `mg.cls.v0`. `ReplayAdapter` emits only `synthetic_scripted` / `human_annotated` and never touches pixels.
- `TrackerStage` (optional, unselected): turns detections into tracks with `track_ref`, `identity_breaks`, `terminated`; its `kind` is `tracker` and its provenance is registered. Without it, real detections have `track_ref = null` and per-track rules report `identity_unavailable`.
- `RiskEngine.evaluate(policy, zones, observation_stream, run_config) -> Evaluation`: deterministic function of the whole input set using event time only (MG-RISK-001 §1–2).
- `EvidenceEngine.fulfill(evaluation, source) -> EvidenceManifest`; `ReviewStore.append(entry)`; `Exporter.export(...) -> bundle`.
### 8.1 Pipeline policies (PROPOSED; detailed design deferred to pre-P1)
- Display path is separate from the analysis path. Files: native browser `<video>` only if FS-03/FS-04 pass, otherwise server-extracted frames; live/multi: server-rendered frames over WebSocket/MJPEG (UNVERIFIED design).
- Recorded: blocking, lossless analysis (stride 1). Live: latest-wins; every drop recorded. Multi: per-source budgets + scheduler + aggregate cap. Isolation: one worker per analyzed source, preferably a process (validate in a P2 spike).
- Cancellation: cooperative token + deadline + drain; explicit CANCELLED state. Reconnection: exponential backoff + jitter + max attempts; each reconnect starts a new `epoch`; tracks never span epochs.
- Temporal consistency: decisions use only `pts_us` and `frame_index`, never wall clock. P0 refuses non-monotonic frame tables; live timestamp anomalies are a pre-P1 design item.

## 9. Candidate technologies — NOT APPROVED (UNVERIFIED licenses; adoption requires MG-PROVENANCE-001 review and separate install authorization)
| Component | Candidate | Note |
|---|---|---|
| Frontend | React + TypeScript + Vite | Owner-reported MIT; verify versions and transitive deps |
| API | FastAPI (Python) | Owner-reported MIT; verify |
| Video decode / frame table provider | **None selected** | FS-01 compares candidates: OpenCV alone (per-frame PTS exposure UNVERIFIED), FFmpeg-family tools or bindings (each needs license, patent and codec review), others |
| Images | Pillow | Owner-reported MIT-CMU; verify |
| Persistence | Canonical JSON files | SQLite deferred (owner-approved) |
| Tests | Pytest + Vitest | Verify |
| Vision | None selected | Excluded pending owner review: Ultralytics models/code (owner-reported AGPL-3.0); trackers with unverified copyleft lineage |
| Codecs | H.264/H.265 decoding | Patent-pool exposure is an open legal item (G-6) |

## 10. Security, privacy and network boundary (PROPOSED)
- **NB-1 Internet:** prohibited for the application at run time. No CDN, telemetry, update check, or external API.
- **NB-2 Loopback:** the server binds 127.0.0.1 only; Host/Origin validation (DNS-rebinding defense); per-session token (delivery mechanism decided in MG-03); CSP `'self'`; no CORS wildcard. Other local users/processes can reach loopback, so the token is mandatory.
- **NB-3 Camera LAN (future, not in MVP):** outbound LAN connections to cameras are permitted only after separate explicit owner authorization, per source, to allowlisted host/port pairs configured by the user; off by default; every other destination is refused and logged (A-P1-10). Plain RTSP is unencrypted: use trusted networks only; prefer RTSPS where supported.
- **NB-4 Install/fetch time:** any package, model or asset download is a separate, explicit, owner-approved action with pinned versions and recorded hashes; it is never performed by the application.
- Credentials: store only a `credential_ref`, resolved from the macOS Keychain or process environment at runtime; redact URLs in logs; never persist plaintext (SEC-02).
- Privacy: footage of identifiable workers is personal data; minimize; no automatic export; retention class per evidence item; purge command; data directory mode 0700; FileVault is relied on for at-rest encryption (UNVERIFIED that it is enabled); logs never contain frames or credentials.
- Webcam: requires macOS camera consent and a visible camera-on state; off by default.

## 11. UX must-haves and reconciliation with exploratory mockups (PROPOSED)
Views: **Review** (video + timeline + events), **Policy & Evidence** (policy and engine version, rule trace, incomplete inputs, evidence viewer, hash verify, review notes), **Sources** (health, budget, analysis state).
Every event shows: rule, rule version, policy and engine version, source, time range, origin, state, incomplete inputs, evidence, review state. UNKNOWN is visually distinct (hatched grey) and always shows its cause. UI language is Spanish; text comes from the catalog by `msg_id`.
Inconsistencies between the two exploratory images and the requirements (all PROPOSED fixes):
1. YOLOv8 listed as optional detector vs. owner's AGPL exclusion → remove.
2. Tracker licenses asserted ("MIT") without verification → verify.
3. Boxes/labels on photographic frames without synthetic/replay/model marker → mandatory origin banner, confidence/track display.
4. "EN VIVO" and "Sistema Activo" imply operational assurance → neutral wording.
5. Four tiles imply parallel inference; no per-tile analysis/drop/latency/reconnect state → ANALYZED/DEGRADED/DISPLAY-ONLY.
6. "Persona en zona de riesgo" shown RED but RED requires person AND vehicle; "Vehículo cerca de persona" implies proximity → image-space occupancy rules only.
7. Zone colors conflated with event severity → zone kind separate from state.
8. GREEN shown as events ("Operación normal", 48 green events) → GREEN is a state, not an event.
9. Event cards lack rule/engine versions, evidence, confidence/origin, incomplete inputs, review state; statistics undefined → add; define metrics or omit.
10. No Risk Policy & Evidence Review view; Reportes/Chart.js tabs exceed MVP → add view, defer reports.
11. Zones lack revisions/camera-move assumption → revisioned zones with `reference_frame_digest`.
12. SQLite+WebSockets (image 2) vs JSON-only (image 1) → JSON-only (owner-approved).
13. Video.js/hls.js/FFmpeg/Docker listed without need → display path decided by FS-03/FS-04; others deferred.
14. "MineGuard" in UI header despite name-conflict finding; mockup imagery provenance unknown → neutral working name; no reuse of imagery.
15. UNKNOWN state absent from mockups → add.

## 12. Machine baseline, performance protocol, and feasibility assumptions MG-03 must test
CONFIRMED by read-only commands on 2026-10-20: Mac mini (Mac16,11), Apple M4 Pro, 12 cores (8P+4E), 24 GB RAM (25,769,803,776 bytes), macOS 27.0.1 (build 26A434), Darwin 27.0.0 arm64. (Serial number and hardware UUID not recorded.)
MG-03a read-only re-check, 2026-10-21 (CONFIRMED): same OS/hardware values as above; GPU 16 cores (Metal 4); free disk 96 GiB; **FileVault On**; Python 3.14.6 (mise; Homebrew python@3.13, 3.14 and 3.15 also present); Node 24.18.0; npm 11.16.0; pnpm 11.15.1; uv 0.13.1; Homebrew 7.0.9; git 2.54.0; `ffmpeg`/`ffprobe` not installed; browsers Safari 27.0.1, Chrome 155.0.8059.85, Firefox 158.0; **cameras listed: only "Insta360 Virtual Camera" (this Mac mini has no built-in camera)**.
UNVERIFIED: camera permission state, availability of a physical camera feed, GPU/MPS/ANE inference performance, memory headroom under load.
Performance protocol (PROPOSED; no targets until measured): fixed 1080p30 ~5-minute clip; metrics = replay throughput (× realtime), event-to-display latency, steady/peak RSS, CPU%, dropped frames; cases = 1, 2, 4 sources; thresholds PROPOSED until the owner ratifies.
**Feasibility assumptions (each must be tested by a spike in MG-03 *before* a decoder is selected or frame accuracy is promised; none is assumed true):**
| ID | Assumption to test | Decision it gates |
|---|---|---|
| FS-01 | A candidate provider can expose per-frame PTS ticks, time base, start offset and last-frame duration deterministically for H.264 MP4 (B-frames, edit lists, VFR). OpenCV's exposure of per-frame PTS is UNVERIFIED | Provider choice; FR-01; `frame_table_provider` |
| FS-02 | Decoded frame count equals the PTS table length; frame `i` decodes identically on repeated runs and across software/hardware decode | `pixel_sha256` evidence verification; A-P0-08 |
| FS-03 | In the target browser(s), the frame actually displayed can be identified exactly (for example via a burned-in frame counter plus the video-frame callback API's media time) | Native-playback accuracy claim; A-P0-02(b) |
| FS-04 | Frame stepping and seeking in native `<video>` land on the intended frame (no native frame-step API assumed) | Native path vs server-extracted frames |
| FS-05 | Start offsets, edit lists and negative PTS produce the documented `pts_us` | `pts_us` formula (MG-DATA-001 §4) |
| FS-06 | Rotation metadata and display matrices produce the display-oriented geometry assumed in `image_px_v1` | Coordinate contract |
| FS-07 | Supported container/codec set on this machine (H.264 profiles; H.265 optional) | A-P0-01 supported-media list |
| FS-08 | A canonical-JSON implementation reproduces RFC 8785 test vectors and rejects floats/duplicates/non-NFC | CJ-1 conformance |
| FS-09 | Evidence image encoding is stable for a pinned encoder version (file hashes) | Evidence file hashing scope |
| FS-10 | Overlay alignment methodology on the chosen display path (pixel-exact vs approximate) | UX claims |
| FS-11 | Baseline throughput of decode + engine on the M4 Pro | NFR-04 report |

## 13. Roadmap Oct 20–25 (PROPOSED; every sprint needs its own explicit owner authorization — none exists beyond MG-02R)
Milestone gates are named **MS-n** (legal/eligibility gates keep the **G-n** names in MG-GATES-001). Sprint labels follow the owner's plan.
| Day | Sprint | Content | Milestone gate |
|---|---|---|---|
| Oct 20 | MG-01 / MG-02 / MG-02R | Definition, documentation foundation, corrective documentation | MS-0: owner review of MG-02R outputs |
| Oct 21 | MG-03a (Phases 0–2) | Environment checks, documentation reconciliation, official-source research, BOM and experiment definitions (no installs, no downloads, no code) | MS-1: owner decision on the install/download request and on the H-1..H-6 correction pass |
| Oct 22 | MG-03b | EXP-001 (MP4) and EXP-002 (webcam) first; detector adapter; feasibility spikes FS-01..FS-11 as needed; documentation corrections H-1..H-6; kernel and golden vectors (data) | MS-2: detector go/no-go at 18:00 |
| Oct 23 | MG-04 | Tracker, observation writer, overlay UI and zones; engine only after H-1..H-6 are corrected and reviewed; evaluation, evidence, export, determinism | MS-3: A-P0-06/07/20/22 and A-RI-01 pass |
| Oct 24 | MG-05 | **Implementation target:** both modes end to end with real inference, export, acceptance dry run | MS-4: A-RI-02..05 |
| Oct 25 | MG-06 | Stabilization and acceptance only (no new features); BOM and NOTICE; benchmark; limitations; checkpoint form per owner decision (D-06/D-07) | MS-5: acceptance checklist |
Documentation bar for starting MG-03: this document set approved and the MG-03-blocking decisions resolved. The size of the P0 test catalog (54 scenarios, 37 essential) is a schedule risk; cut-lines in §3 apply.
