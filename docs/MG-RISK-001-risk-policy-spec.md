# MG-RISK-001 — Deterministic Risk Policy Specification (v0.2, MG-02R)

Status: PROPOSED. Date: 2026-10-20. Evaluation semantics label: `semantics_version = 1`. Implementation status: **IMPLEMENTED for `semantics_version = 2`** (`backend/mineguard/risk/`, reference `MG-RISK-002`); this document is the historical v0.2 specification and the `semantics_version = 1` text below is NOT what runs.
Supersedes the MG-02 draft. Resolves audit findings F-01, F-02, F-03, F-08, F-09, F-10, F-11, F-12, F-13, F-16, F-21 (see MG-DECISIONS-001 §E).
**MG-VS02 note (2026-10-25):** the evaluation mechanics of §5.4, §5.5, §7 and §8 are superseded for `semantics_version = 2` by `docs/MG-RISK-002-kernel-reference.md`, which records the builder's own H-1..H-6 reconciliation (the original audit text is not in the repository) and is pending independent review. The text below is kept for traceability.
**MG-03a status note (2026-10-21; SUPERSEDED on 2026-10-25 by MG-RISK-002, which records the builder's own H-1..H-6 reconciliation, reviewed by Codex but still not owner-confirmed):** audit findings H-1 to H-6 remained **UNRESOLVED** at that date. The dispositions above are the author's labels and have not been independently validated. Implementing the risk engine against this v0.2 text is **NO-GO** until H-1..H-6 are corrected and reviewed. Under the MG-03a scope update, scenario S-30 (formerly X1) is mandatory for acceptance; the catalog text is unchanged pending the correction pass.
Evidence labels (CONFIRMED / PROPOSED / UNVERIFIED / BLOCKED) are defined in README. Owner-approved inputs to this version: four states and priority
RED > YELLOW > UNKNOWN > GREEN with incomplete inputs always visible; per-track dwell; named built-in versioned rules (no expression language);
rule version AND engine version in the reproducibility contract. Numeric defaults and the mechanics below remain PROPOSED until accepted (MG-DECISIONS-001 D-21).

## 1. Principles
1. AI observations never determine or authorize safety actions. They are inputs; built-in, versioned rules evaluate risk.
2. `evaluate` is a deterministic function of the *whole* input set. It keeps internal timers (it is not memoryless) but uses only event time (frame timestamps), never a wall clock, randomness, network, or I/O.
3. Image-space only. No physical distance, speed, or collision prediction without calibration (none exists in the MVP).
4. **Unobserved is not GREEN.** GREEN can be assigned only to frames that were analyzed (§5.4).
5. Incomplete inputs are never hidden: they stay visible on every state, including confirmed RED and YELLOW (§3.2).

## 2. Reproducibility contract
Outputs are a function of: `engine_version` (and its `semantics_version`), the set of `(rule_id, rule_version)` and their validated `params`, the common parameters,
the zone set, the observation set, the source digest and frame table, and the run configuration (all digests are defined in MG-DATA-001 §3).
- A `(rule_id, rule_version)` pair is **frozen**: any behavior change requires a new `rule_version`. Any change to this document's evaluation mechanics requires a new `semantics_version`.
- The engine refuses a policy that names a rule version it does not implement, or whose `semantics_version` differs (V-14, V-19).
- Reproducibility claim (to be demonstrated in MG-03, not yet demonstrated): identical inputs and identical `engine_version` ⇒ byte-identical canonical evaluation output.
  Across engine versions with the same `semantics_version`, equality is expected only where the golden vectors (§12) pass.
- Evidence files and human reviews are **outside** this contract (MG-DATA-001 §2).

## 3. Evaluation states
| State | Meaning | Requires | Event? | UI | Never means |
|---|---|---|---|---|---|
| GREEN | Frame analyzed; no episode active; no rule instance UNKNOWN | Status `analyzed` | No (state only; pending candidates and dropped tracks are listed) | Green | "Safe"; "zone empty"; "no hazard" |
| YELLOW | At least one YELLOW episode active, no RED episode active | Episode confirmed (§8) | Yes | Amber | Proximity or distance claim |
| RED | At least one RED episode active | Episode confirmed (§8) | Yes | Red | Collision prediction; that an accident occurred |
| UNKNOWN | Not evaluable at this time, or an unobserved interval | At least one cause (§5.1) | **No severity, not an alert; flagged for human review** | Hatched grey | GREEN; "nothing happened" |

### 3.1 Priority (per source, epoch, zone, frame)
`RED > YELLOW > UNKNOWN > GREEN`:
1. any RED episode ACTIVE ⇒ RED;
2. else any YELLOW episode ACTIVE ⇒ YELLOW;
3. else any rule instance has `open_pred` value UNKNOWN at this frame, or the frame is not `analyzed` ⇒ UNKNOWN;
4. else GREEN.
Rule instances: R1 has one per evaluated zone; R2 has one per live person track item plus one pseudo-instance for untracked person items (§7.4).

### 3.2 Incomplete inputs stay visible
For every ACTIVE episode and every frame, the engine records `incomplete` ranges whenever any UNKNOWN item or instance exists in the zone (the episode's own absorbed/closing UNKNOWN frames,
other rule instances that are UNKNOWN, and item-level unknowns such as an ambiguous object that does not change the confirmed result).
They appear on the event (`incomplete`) and on the timeline interval (`causes`). A RED or YELLOW state is therefore never presented as "complete".

### 3.3 State transitions (aggregate view)
| From → To | Mechanism | Events |
|---|---|---|
| GREEN → YELLOW/RED | Candidate confirmed (§8.1) | Episode opens (`start` = confirmation frame) |
| YELLOW → RED | R1 confirms while R2 sustain fails; both conditions change at the same frame; R2's closing hold equals R1's open hold when both use `debounce_ms`, so `YELLOW.end == RED.start` | YELLOW closes `rule_cleared`; RED opens |
| RED → YELLOW | R1 closing run confirmed; R2 `open_pred` requires NV held ≥ `debounce_ms`; with equal `debounce_ms` values `RED.end == YELLOW.start` | RED closes `rule_cleared`; YELLOW opens |
| YELLOW/RED → UNKNOWN | Closing run containing unknowns confirmed (`inputs_unknown`), unbridged hole (`unbridged_gap`), track discontinuity, epoch boundary | Episode closes; interval UNKNOWN follows |
| UNKNOWN → GREEN | Unknown cause resolved and frame analyzed with no instance UNKNOWN | None |
| UNKNOWN → YELLOW/RED | Candidate confirmed; the confirmation hold counts only determinate TRUE frames | Episode opens |
| any → UNKNOWN (no episode) | Hole, faulty frame, or instance UNKNOWN | Interval only (reviewable); no event |
If the two `debounce_ms` values differ, a gap or overlap between the closing and opening episodes can occur; both are then visible in the timeline (priority rules still apply). No transition is silent: each closing carries `closed_reason`.

## 4. Three-valued logic (Kleene)
Values TRUE (T), FALSE (F), UNKNOWN (U): `¬T=F, ¬F=T, ¬U=U`; `F∧x=F; T∧x=x; U∧U=U`; `T∨x=T; F∨x=x; U∨U=U`. Unknown inputs never silently become FALSE.

## 5. Observations → effective values
### 5.1 Unknown causes (closed enum)
`frame_not_analyzed` (stride/dropped) · `source_gap` · `adapter_error` · `class_unknown_or_ambiguous` · `below_confidence_threshold` · `object_truncated_at_frame_edge` · `track_absent` ·
`identity_unavailable` · `epoch_boundary`. (`timestamp_anomaly` exists for live adapters; P0 refuses non-monotonic frame tables, MG-DATA-001 V-16; live handling is deferred to the pre-P1 design.)

### 5.2 Object occupancy `OCC_o(Z, f)`
For an object `o` in an analyzed frame and a zone `Z` (kinds per rule `zone_kinds`):
1. Reference point and polygon test: MG-DATA-001 §5 (doubled integer coordinates, boundary inclusive). If no relevant truncation flag: result T/F.
2. If `truncated.left|right|bottom` is set: uncertainty segment test (MG-DATA-001 §5.5): F if the segment does not touch the closed polygon, else U (`object_truncated_at_frame_edge`). A truncated object is **never** determinately inside.
3. Confidence gate (only if `min_confidence_permille` is set): a `null` confidence is exempt. Below threshold: `below_threshold_handling = "ignore"` removes the object; `"unknown"` makes the item U (`below_confidence_threshold`) when its occupancy is T or U, F when F.
4. Class state: `known` person/vehicle contribute to their class; `known other` is ignored; `ambiguous` contributes U to each candidate class (when its occupancy is T or U; F if F); `unknown_class` is ambiguous across all classes.

### 5.3 Items and class occupancy
Items are: present objects (tracked or untracked) and absent tracks still inside their hold window (§7.3).
`OCC_class(c, Z, f)` = Kleene-OR over the items contributing to class `c`: T if any is T; else U if any is U; else F (including "no items").
`TrackOcc(T, Z, f)` is the value of track T's item (class person for R2).
Holes and faulty frames: every value is U (causes per frame status).

### 5.4 Bridging (never F; never across faults)
Applied to each predicate trace (`OCC_class`, `TrackOcc`) **before** any rule logic.
A maximal run of U values is **bridged to T** (flag `bridged`) iff all hold: (a) it is bracketed on both sides by T values in the same epoch; (b) bracket-to-bracket duration
`pts(next_T) − pts(prev_T) ≤ tolerance`; (c) every cause in the run is bridgeable. Tolerance is `track_gap_tolerance_ms` if the run contains `track_absent`, else `gap_tolerance_ms`
(the smaller if several apply). Bridgeable causes: `frame_not_analyzed`, `source_gap`, `class_unknown_or_ambiguous`, `below_confidence_threshold`, `object_truncated_at_frame_edge`, `track_absent`.
**Not bridgeable:** `adapter_error`, `identity_unavailable`, `epoch_boundary`. **F is never bridged**: an F..U..F run stays U.
Consequence (principle 4): a frame that is not `analyzed` is labeled UNKNOWN in the timeline unless an ACTIVE episode covers it (then it stays RED/YELLOW with `bridged` and `incomplete` flags).
Bridging only sustains timers and episodes; it never turns an unobserved frame into GREEN.

### 5.5 Primitives
- `HOLD(p, d)`: T iff `p` has been T continuously for ≥ `d` (pts difference between the first frame of the current T-run and the current frame, integer microseconds, `≥`); F if `p` is F or the run is shorter than `d`; U if `p` is U.
- `DWELL_T(Z, f)` (per track): the current continuous T-run of `TrackOcc(T, Z)` (bridged T counts) started at `t_enter` (pts of its first frame). T iff `pts(f) − t_enter ≥ dwell_us`; F if the run is shorter or `TrackOcc` is F; U if `TrackOcc` is U.
  The run restarts at an unbridged return, an identity break (§7.2), or after F.
- All thresholds are converted exactly: `x_us = x_ms × 1000`.

## 6. Built-in rules (v0.1.0 policy; named, versioned, parameters validated)
Common parameters (all integers; MG-DATA-001 §9 holds the validation table): `gap_tolerance_ms=1000`, `track_gap_tolerance_ms=500`, `track_unknown_hold_ms=3000` (`track_gap_tolerance_ms ≤ track_unknown_hold_ms`),
`min_confidence_permille=null`, `below_threshold_handling="unknown"`. Fixed in the rule version (not parameters): reference point `bbox_bottom_center`, boundary inclusive.
| Rule | Version | Severity | Key | Parameters | `open_pred` / `open_hold` | `sustain` / `close_hold` |
|---|---|---|---|---|---|---|
| `R_PERSON_AND_VEHICLE_IN_ZONE` (R1) | 1.0.0 | RED | zone | `debounce_ms=500`, `zone_kinds=["restricted"]` | `OCC_class(person) ∧ OCC_class(vehicle)` / `debounce_us` | same predicate / `debounce_us` |
| `R_PERSON_DWELL_NO_VEHICLE` (R2) | 1.0.0 | YELLOW | zone + person track | `dwell_ms=1500`, `debounce_ms=500`, `zone_kinds=["restricted"]` | `DWELL_T ∧ HOLD(NV, debounce_us)` with `NV = ¬OCC_class(vehicle)` / 0 | `DWELL_T ∧ NV` / `debounce_us` |
Every enabled rule participates in the GREEN determination (there is no separate "required" list; removing a rule is a policy change). Zones whose `kind` is not in the rule's `zone_kinds` are not evaluated by that rule; a zone evaluated by no rule is listed in `zones_not_evaluated`.
YELLOW definition (per-track dwell, owner-approved) uses vehicle presence at class level: any determinate vehicle in the zone blocks YELLOW. Dwell values (1500 ms, 500 ms) are PROPOSED defaults (D-04b).

## 7. Tracks, identity, discontinuity, censoring
### 7.1 Identity model
Per-track dwell requires the producer's assertion that a `track_id` denotes one continuous identity within an epoch. Tracks come from the observation set (scripted, annotated, or a future tracker stage; no tracker is selected).
The engine does not infer identity and does not de-duplicate overlapping detections (a known limitation: duplicate detections of one person yield duplicate per-track events).
### 7.2 Definitions
- **Discontinuity**: the dwell run of a track is broken by (a) F occupancy, (b) a U-run that is not bridged, (c) termination, (d) an **identity break** declared by the producer (`identity_breaks` frames), (e) an epoch boundary.
  After a discontinuity the same `track_id` reappearing starts a *new* dwell run; the event/interval records `track_discontinuity` or `identity_loss` as appropriate. This can understate dwell; it never overstates it.
- **Identity loss**: a declared identity break, or a track whose absence exceeded `track_gap_tolerance_ms`.
- **Left-censoring**: a run (condition, dwell, NV hold) that begins at the first analyzed frame of an epoch. Durations are lower bounds; the event carries `left_censored=true`.
- **Right-censoring**: an episode ACTIVE (or CLOSING) at the end of an epoch/stream; carries `right_censored=true`, closes with `end_of_stream` (or `epoch_boundary`/`cancelled`).
### 7.3 Absent tracks (analyzed frames in which a non-terminated track has no object)
- Last determinate value F: the item contributes F (equivalent to a non-detection).
- Last value T or U: value U (`track_absent`) from the first absent frame. It can be bridged back to T (§5.4) if the track returns within `track_gap_tolerance_ms`. Otherwise it stays U until the track returns (new run), is terminated,
  or `track_unknown_hold_ms` elapses since its last object. At hold expiry the item is dropped (contributes nothing) and recorded in `dropped_tracks` (visible in the evaluation, never silent).
- Termination `exited_view` (producer asserts the object left the view): F from the termination frame. Termination `unresolved`: as an absent track (hold logic).
### 7.4 Untracked objects
A person object with `track_ref=null` cannot satisfy per-track dwell. If its occupancy is T or U, the R2 pseudo-instance is U (`identity_unavailable`, not bridgeable): the zone shows UNKNOWN (or a YELLOW/RED with this incomplete input visible). Untracked objects still count for R1 (class level).

## 8. Episodes (the only source of YELLOW/RED events)
Per rule key a machine runs on every frame of the epoch (for live gaps with no frames, one pseudo-frame at the gap start). Frame times are the source's `pts_us` (MG-DATA-001 §4).
### 8.1 Machine
| Mode | Condition | Action |
|---|---|---|
| INACTIVE | `open_pred = T` | → CAND; `condition_since` = this frame; `run_start` = this frame |
| CAND | `open_pred = T` and `pts(f) − run_start ≥ open_hold_us` | → ACTIVE; `start` = this frame (`confirmed`) |
| CAND | `open_pred ≠ T` | → INACTIVE; record a *suppressed candidate* (`below_open_hold`, from/to frames, terminal value). **Opening requires continuous T; no absorption before confirmation.** |
| ACTIVE | `sustain = T` | stay |
| ACTIVE | first frame of an **unbridged hole** (including dropped/source gap) | close immediately: `end` = that frame's time; `closed_reason = unbridged_gap` |
| ACTIVE | `sustain ≠ T` (F or value-level U) | → CLOSING; `closing_start` = this frame (`condition_until` = this frame) |
| CLOSING | `sustain = T` | → ACTIVE; record `absorbed {from, to, values, reasons}`; the frames stay inside the episode |
| CLOSING | `pts(f) − closing_start ≥ close_hold_us` | close: `end` = this frame; reason by §8.2 |
| any | end of stream / epoch end / cancellation | close; `end` = end of the last frame (`end_of_stream`, `epoch_boundary`, `cancelled`); `right_censored` as applicable; a CAND is recorded as suppressed (`stream_ended_before_open_hold`) |
`open_hold`/`close_hold` come from §6; R2's `open_hold` is 0 because its `open_pred` already contains DWELL and HOLD. `pts(f) − run_start ≥ hold` uses integer microseconds; at 30 fps, 15 frames = 500 000 µs exactly.
### 8.2 Closing reason (first match)
`unbridged_gap` (immediate close above) → `track_discontinuity` (closing run contains an identity break, termination, or unbridged track absence) → `inputs_unknown` (closing run contains any U; causes listed in `closed_unknown_reasons`) → `rule_cleared` (all F).
`epoch_boundary`, `end_of_stream`, `cancelled` apply to forced closes.
### 8.3 Times and intervals
Event times: `condition_since ≤ start`, `condition_until` (first non-T frame of the closing run, or null), `end` (≥ `condition_until`). All timeline intervals are half-open `[start_pts_us, end_pts_us)`; adjacent intervals share boundaries exactly.
`start`/`end` are frame-granular (no interpolation). Event IDs and evidence requests derive from these values (MG-DATA-001 §3, §10).
### 8.4 Event content (deterministic)
`trigger_refs` (objects at the start frame, bounded), `contributors.tracks` (sorted set over the episode), `facts_at_start` (three-valued strings), `incomplete`, `absorbed`, `bridged`, `origin_summary`, `confidence_summary`, structured `explanation {msg_id, args}`, `limitations` (message ids). No free text and no mutable human fields.

## 9. Timeline, coverage, and review targets
Per (epoch, evaluated zone): ordered, gap-free, half-open intervals covering `[0, stream_end_us)`; a new interval begins whenever `state` or the set of active `event_id`s changes. Each interval carries `bridged`, `pending` candidates, and `causes` (reason, frame range, bounded `refs`).
UNKNOWN intervals are reviewable targets (MG-DATA-001 §12) and carry `causes` with frame ranges and object/track refs; they need no evidence file but may request an `observation_dump`.
Coverage per zone: `total_us`, `analyzed_us`, per-state microseconds (partitioning `total_us`), `unknown_by_reason_us`; permille values use floor division.
`epoch_gaps`: for live recordings, the interval between epochs is UNKNOWN (`epoch_boundary`); zones are assumed valid after a reconnect only under the declared fixed-camera assumption and are flagged `zone_validity_unverified`.

## 10. Explanations
Structured only: `explanation = {msg_id, args}` with `args` limited to ids, frame references, class names and parameter values. The UI renders messages from a Spanish catalog (initial language) keyed by `msg_id`; new languages add catalogs without changing evaluation output (MG-DATA-001 §1.5).

## 11. Versioning and comparison
Semver for policy and rules; policy digest covers rule ids, versions and parameters. Policy comparison (future) = run `evaluate` twice on identical inputs and diff. Not in MVP.

## 12. Test requirements
Every rule version needs ≥1 positive, negative, boundary, UNKNOWN and handoff case (catalog §14). Golden outputs are hand-authored before engine code and become the rule's conformance vectors.
Mutation checks (each must fail a test): flip a threshold; map UNKNOWN→GREEN; allow F-bridging; drop a rule from the GREEN determination; remove the incomplete-input recording; change the open/close hold; replace per-track dwell by class-level dwell.

## 13. Known limitations (documented, not hidden)
Footpoint (`bbox_bottom_center`) approximates ground contact and is perspective-sensitive. Detector misses yield GREEN with no UNKNOWN (GREEN scope, §3). No de-duplication of overlapping detections.
Vehicle presence is class-level. Class taxonomy is `person | vehicle | other` (MG-DATA-001 §7). Sampled/strided analysis is not supported in P0 (`analysis_stride` must be 1); under strided or budgeted runs unanalyzed frames are UNKNOWN (P2 scenario S-49).
Frame-granular times; no interpolation between frames.

## 14. Scenario catalog (59 scenarios; tiers P0 / X1 / P1 / P2; E = essential, never cut; C = cuttable)
Fixture conventions: 30 fps constant frame rate, time base 1/30, N = 300 frames unless stated, one zone `Z` (kind `restricted`), defaults of §6 (15 frames = 500 ms; 30 = 1000 ms; 45 = 1500 ms; 90 = 3000 ms).
Frame k has `pts_us = round_half_up(k·10⁶/30)`; all durations used below are multiples of 3 frames and therefore exact. "enter f" = first frame whose reference point is inside Z. Intervals are `[a, b)` in frame indices.
Engine-level cases (marked ε) use explicit `pts_us` fixtures independent of any video. Expected states are the timeline; events listed with `start`/`end`.

### 14.1 Positive
| ID | Tier | Cut | Setup | Expected |
|---|---|---|---|---|
| S-01 | P0 | E | Person P1 (tracked) enters Z at 30, stays to 150, outside from 151; no vehicle | GREEN [0,75), YELLOW [75,166), GREEN [166,300). One YELLOW event: `condition_since`=75, `start`=75, `dwell_since`=30, `condition_until`=151, `end`=166, `rule_cleared` |
| S-02 | P0 | C | Vehicle alone in Z for 60–200 | GREEN throughout; no events; no suppressed candidates |
| S-03 | P0 | E | P1 enters at 30 and stays; vehicle V1 enters at 60 and stays | GREEN [0,75), RED [75,300). R2 never opens (NV broken before dwell). RED: `condition_since`=60, `start`=75, `end_of_stream`, `right_censored` |
| S-04 | P0 | C | V1 enters at 30, P1 enters at 60, both stay | GREEN [0,75), RED [75,300) |
| S-05 | P0 | E | P1 enters 30, V1 enters 60; P1 outside from 151; V1 stays | RED [75,166) closes `rule_cleared` (`condition_until`=151); GREEN [166,300) |
| S-06 | P0 | C | Both leave at 151 | Same boundaries as S-05; no residual events |
| S-07 | P0 | C | P1 enters 30, P2 enters 40, V1 enters 100 | Two YELLOW events (P1 start 75; P2 start 85) both close at 115 `rule_cleared`; **one** RED event start 115 listing tracks P1, P2, V1; YELLOW [75,115) then RED [115,300) |
| S-08 | P0 | C | Zones A and B; person + vehicle in A as S-03; B empty | A: as S-03. B: GREEN throughout. Independent timelines |
| S-35 | P0 | E | RED→YELLOW handoff: P1 enters 30, V1 enters 60, V1 outside from 151, P1 stays | RED [75,166); YELLOW start 166 (NV held 15 frames) ⇒ `RED.end == YELLOW.start == 166`; YELLOW to 300 `end_of_stream` |
| S-36 | P0 | E | YELLOW→RED handoff: P1 enters 30; V1 enters 150 | YELLOW [75,165) closes `rule_cleared`; RED start 165 ⇒ `YELLOW.end == RED.start`; no GREEN gap |

### 14.2 Negative
| ID | Tier | Cut | Setup | Expected |
|---|---|---|---|---|
| S-09 | P0 | E | Person outside Z; vehicle inside | GREEN; no events |
| S-10 | P0 | C | Both outside | GREEN |
| S-11 | P0 | C | P1 in Z for frames 30–73 (44 frames) then leaves | GREEN; no YELLOW; no suppressed (R2 never raw-T) |
| S-12 | P0 | C | P1 crosses Z in 20 frames | GREEN |
| S-13 | P0 | E | Empty analyzed scene | GREEN throughout (GREEN scope note: detector misses are indistinguishable) |
| S-14 | P0 | C | Person and vehicle boxes overlap in image but both reference points outside Z | GREEN |
| S-37 | P0 | E | P1 dwells in zone A; V1 in zone B | No RED in A or B. A: YELLOW per R2 (vehicle absent from A); B: GREEN |
| S-38 | P0 | E | P1 in Z from 30; V1 present only frames 100–109 (10 frames) | R1: suppressed candidate [100,110) `below_open_hold`; YELLOW from 75 *absorbs* the NV flicker (`absorbed` [100,110)); one YELLOW event to its natural end. No RED |
| S-45 | P0 | C | P1, P2, V1 in Z; P1 leaves; P2 and V1 stay | RED continues without interruption (class-level R1) |
| S-46 | P0 | C | Object `other` (known, non-target) inside Z with a person and no vehicle | `other` ignored: behaves as S-01 |
| S-47 | P0 | C | Person + vehicle in zone of kind `monitored` | Zone not evaluated (`zones_not_evaluated`); no timeline, no events |

### 14.3 Boundary
| ID | Tier | Cut | Setup | Expected |
|---|---|---|---|---|
| S-15 | P0 | E | ε Geometry vectors, square (100,500),(900,500),(900,1000),(100,1000): ref on edge (bbox x=480,y=900,w=40,h=100 ⇒ (500,1000)); ref on vertex; ref on right edge (x=880,w=40 ⇒ x=900) | All inside (inclusive) |
| S-16 | P0 | C | ε Same square: bbox y+h=1001 (1 unit below); x=881,w=40 (x=901); x=880,w=41 (x=900.5 half-integer) | All outside. Reference is half-integer: computed in doubled coordinates, no rounding |
| S-17 | P0 | E | (a) P1 enters 30 and stays through 75; (b) ε explicit `pts_us`: dwell exactly 1 500 000 µs | (a) YELLOW start 75. (b) YELLOW opens at that frame (`≥`) |
| S-18 | P0 | E | (a) P1 inside frames 30–74 only (max dwell 44 frames = 1 466 667 µs); (b) ε dwell 1 499 999 µs | GREEN; no YELLOW |
| S-19 | P0 | E | With P1 dwelling from 30: (a) V1 present frames 100–115 (confirmation at 115); (b) V1 present frames 100–114 | (a) RED start 115 and YELLOW end 115. (b) RED suppressed (`below_open_hold`, 14 frames); YELLOW absorbs |
| S-20 | P0 | E | Open strictness vs close absorption, with P1 in Z from 30: (a) V1 present 100–104, absent 105–109, present from 110; (b) in a confirmed RED, V1 disappears for frames 150–154 | (a) R1 candidate dropped at 105 (suppressed [100,105); no absorption before confirmation); new candidate at 110 ⇒ RED start 125; YELLOW (from 75) absorbs the 100–104 flicker and closes at 125 (`rule_cleared`). (b) Absorbed `[150,155)`; one RED event |

### 14.4 Unknown, incomplete, censoring
| ID | Tier | Cut | Setup | Expected |
|---|---|---|---|---|
| S-21 | P0 | C | P1 in Z from 30; frames 50–59 `not_analyzed_dropped` (Δ bracket 11 frames ≤ 30) | Dwell continues (bridged T); YELLOW start 75. Timeline: frames [50,60) are **UNKNOWN** (`frame_not_analyzed`, `bridged=true`), never GREEN |
| S-22 | P0 | E | YELLOW active from 75; frames 80–119 `source_gap` (bracket 41 frames > 30) | YELLOW [75,80) closes `unbridged_gap` at 80; UNKNOWN [80,120) (`source_gap`); P1 still in Z: dwell restarts at 120 ⇒ GREEN [120,165) (flag `dwell_restarted_after_unknown`), YELLOW start 165 |
| S-23 | P0 | E | Empty scene; frames 100–109 `not_analyzed_dropped` | UNKNOWN [100,110): F is never bridged; GREEN elsewhere |
| S-24 | P0 | E | (a) P1 from 30; ambiguous {person,vehicle} object inside Z frames 60–120; (b) YELLOW active (start 75), ambiguous object inside Z from frame 100 for 40 frames | (a) UNKNOWN [60,121) (`class_unknown_or_ambiguous`); no RED/YELLOW; after resolution NV held 15 frames ⇒ GREEN [121,136), YELLOW start 136. (b) YELLOW stays active with `incomplete` from 100 (closing run); the ambiguity lasts 40 frames (> 15) ⇒ closes at 115 `inputs_unknown` (`class_unknown_or_ambiguous`); UNKNOWN [115,140); after resolution NV is held 15 frames ⇒ GREEN [140,155), new YELLOW start 155 |
| S-25 | P0 | E | YELLOW active from 75; P1 last seen at 100, then absent; never returns; not terminated | YELLOW [75,116) closes `inputs_unknown` (`track_absent`) at 116 (15-frame closing run); UNKNOWN [116,190); at 190 (`track_unknown_hold_ms` since last seen 100) item dropped ⇒ GREEN from 190 with `dropped_tracks` entry (P1, last seen 100, `hold_expired`) |
| S-26 | P0 | E | Truncated bottom: (a) person truncated at bottom, observed ref point inside Z; (b) same, ref point far outside Z (segment disjoint); (c) top-only truncation | (a) UNKNOWN (`object_truncated_at_frame_edge`). (b) F (determinate). (c) behaves as untruncated (T/F) |
| S-27 | P0 | E | RED confirmed (as S-03); frames 160–180 an ambiguous object inside Z | RED continues; `incomplete` [160,181) (`class_unknown_or_ambiguous`); state never shown complete |
| S-28 | P0 | E | YELLOW active; (a) one `adapter_error` frame at 100; (b) `adapter_error` frames 100–119 | (a) Absorbed, `incomplete` [100,101) (`adapter_error`); YELLOW continues. (b) Closes at 115 `inputs_unknown` (`adapter_error`); UNKNOWN [115,120); `adapter_error` is not bridgeable, so P1's dwell restarts at 120: GREEN [120,165), YELLOW start 165 |
| S-31 | P0 | E | Source frame table with non-increasing `pts` | Run refused (V-16). Live `timestamp_anomaly` handling is a P1 design item |
| S-39 | P0 | E | P1 inside Z at frame 0 (left-censored) | YELLOW start 45 (dwell lower bound from first observation), `left_censored=true`, `dwell_since`=0 |
| S-40 | P0 | E | (a) RED active at last frame; (b) person leaves 3 frames before the last frame during RED | (a) Closes `end_of_stream`, end = stream end, `right_censored`. (b) Closing run unconfirmed at end ⇒ `end_of_stream`, `condition_until` set, no `rule_cleared` |
| S-41 | P0 | E | Untracked person object inside Z (`track_ref=null`) | UNKNOWN (`identity_unavailable`); (variant) with tracked P1 YELLOW active: YELLOW continues with `incomplete` (`identity_unavailable`) |
| S-42 | P0 | E | (a) P1 absent 10 frames inside Z (bridged); (b) absent 40 frames then returns; (c) declared identity break at frame 100 | (a) Bridged T, dwell continues. (b) Unbridged: U during absence, dwell restarts on return (`track_discontinuity`). (c) Dwell restarts at 100; open YELLOW closes `track_discontinuity` |
| S-43 | P0 | E | UNKNOWN→RED: P1 in Z; vehicle ambiguous frames 60–89 then determinate vehicle from 90 | UNKNOWN [60,90); candidate starts 90; RED start 105; U frames never count toward the open hold |
| S-44 | P0 | C | Zones A and B: A has an ambiguous object; B empty | A: UNKNOWN; B: GREEN (per-zone independence) |
| S-30 | X1 | C | Real detector confidences with `min_confidence_permille=500`: object at 300 near Z; handling `unknown` vs `ignore` | `unknown` ⇒ UNKNOWN (`below_confidence_threshold`); `ignore` ⇒ GREEN; null-confidence objects exempt. Requires X1; P0 covers the logic via S-59 |
| S-59 | P0 | C | ε `human_annotated` objects with numeric confidences 300/700, policy threshold 500, both handlings; plus null-confidence object | Same logic as S-30 without any model |
| S-29 | P1 | C | Live recording: epoch 0 frames 0–199 with YELLOW active at 199; epoch 1 starts | YELLOW closes `epoch_boundary`; `epoch_gaps[0]` UNKNOWN; tracks not carried; dwell restarts in epoch 1; `zone_validity_unverified` |
| S-49 | P2 | C | Budgeted analysis: every 3rd frame analyzed | Unanalyzed frames UNKNOWN (`frame_not_analyzed`); coverage ≈ 333‰; expected to be noisy (may motivate a future sampling semantics version) |
| S-58 | P1 | C | Live latest-wins drops recorded as `not_analyzed_dropped` holes; record then replay | Replay evaluation equals the live final evaluation (A-P1-07) |

### 14.5 Run refusals (nothing is evaluated)
| ID | Tier | Cut | Condition | Expected |
|---|---|---|---|---|
| S-32 | P0 | E | Observation set `source_digest` ≠ loaded source | Refuse (V-01) |
| S-33 | P0 | E | Frame dimensions or `frame_count` mismatch | Refuse (V-02) |
| S-34 | P0 | E | Replay adapter declares `model_inference`; or confidence numeric on `synthetic_scripted` | Refuse (V-09, V-10) |
| S-48 | P0 | E | `analysis_stride ≠ 1` | Refuse (V-15) |
| S-50 | P0 | E | Unknown `coord_space`; unknown fields in a hashed body; float/duplicate key/non-NFC (canonical profile) | Refuse (V-04, V-17, V-20) |
| S-51 | P0 | E | Bbox out of frame or w/h<1; `truncated` flag inconsistent with bbox | Refuse (V-06, V-07) |
| S-52 | P0 | E | Invalid zones: <3 vertices, duplicate vertex, collinear consecutive vertices, zero area, self-intersection, touching non-adjacent edges, out of frame, dimension mismatch | Refuse / reject at creation (V-12, V-13) |
| S-53 | P0 | E | Frames∪ranges not exactly covering `[0,N−1]` (gap, overlap, duplicate) | Refuse (V-05) |
| S-54 | P0 | E | Invalid policy: unknown rule or rule version, parameter out of range, `track_gap_tolerance_ms > track_unknown_hold_ms`, `semantics_version` mismatch | Refuse (V-14, V-19) |
| S-55 | P0 | C | Any forbidden physical-unit field while `calibration` is null | Refuse (V-11) |
| S-57 | P0 | E | Duplicate `obj_key` in a frame; undeclared `track_ref`; track with two objects in one frame; track label inconsistency | Refuse (V-08) |
| S-56 | P1 | C | Export of a source record carrying a credential canary / URL with userinfo | Refuse or scrub (V-18) |
Counts: **59 scenarios = 54 P0 (42 evaluation, 12 refusal as listed) + 1 X1 (S-30) + 3 P1 (S-29, S-56, S-58) + 1 P2 (S-49)**; of the 54 P0, **37 are essential (E)** and 17 cuttable (C) (counts verified by tier/cut column).
