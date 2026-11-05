# MG-DATA-001 — Canonical Data Contracts (v0.2, MG-02R)

Status: PROPOSED. Date: 2026-10-20. Schema label: `mg.0.2` (draft). Implementation status (updated 2026-10-25): schema files, validators and code now exist (`backend/schemas/`, `backend/mineguard/schema.py`, `session_docs.py`); the implemented live webcam contract is `mg.obsset.live.vs02` and the exports are `mg.export.vs02` / `mg.export.vs02.session` (see `MG-PRODUCT-001` §10). The `mg.0.2` text below is the historical draft where it differs.
Supersedes the MG-02 draft. Resolves audit findings F-04, F-05, F-06, F-09, F-12, F-13, F-15, F-21, F-24 (see MG-DECISIONS-001 §E). Evaluation mechanics live in MG-RISK-001.
**MG-03a note (2026-10-21):** live webcam analysis needs a *sampled-stream* rule (frame table built incrementally from submitted frames, capture-time `pts_us`, drops after submission recorded as holes; finding L-1, decision pending), and real-inference ObservationSets need adapter integerization rules (rounding and clamping to edge coordinates, COCO→`mg.cls.v0` mapping, confidence → permille; finding L-5). Neither is defined in this version; both belong to the v0.3 correction pass.

## 1. Conventions
### 1.1 Canonical JSON profile (CJ-1)
RFC 8785 (JCS) canonicalization with these restrictions: all numbers are integers with |n| ≤ 2⁵³−1 (floats and exponents are invalid in any hashed body); strings are Unicode NFC; no duplicate keys;
UTF-8 without BOM; unknown fields in a hashed body are rejected (V-20). Arrays are either *sequences* (order is significant, as specified) or *sets* (sorted by the stated key). `null` is allowed only where stated.
### 1.2 Hashes and identifiers
Hash = SHA-256, lowercase hex, over canonical bytes. `digest16` = first 16 hex characters. Identifier derivations are in §3. No random identifiers appear in hashed artifacts.
### 1.3 Forbidden fields
While `calibration` is null, any field named `*_m`, `*_km`, `*_kmh`, `*_mps`, `distance*`, `speed*`, `ttc*`, or `collision*` is invalid (V-11).
### 1.4 Privacy
No credentials, no URLs with userinfo, no personal data in any contract. Sources reference credentials only through `credential_ref`. Review records use a pseudonymous `reviewer_label`.
### 1.5 Message identifiers (internationalization)
User-facing text is never stored in evaluation output. Evaluations contain `{msg_id, args}` where `msg_id` matches `^mg\.[a-z0-9_]+(\.[a-z0-9_]+)+$` (for example `mg.expl.red.person_vehicle_in_zone`, `mg.lim.image_space_only`, `mg.state.unknown`, `mg.refusal.source_digest_mismatch`) and `args` holds only ids, frame references, class names, and integers.
The UI renders from a **Spanish** message catalog (initial language, owner-approved); other languages add catalogs without changing any hashed artifact. Every `msg_id` must exist in the catalog (A-P0-21).

## 2. Artifacts and hash boundaries
| Artifact | Deterministic? | Hashed body | Excluded from hash | Mutable? |
|---|---|---|---|---|
| Source record (file) | Yes | `source_digest`, media facts, frame table (`frame_table_digest`) | — | No |
| ObservationSet | Yes | everything except `meta` | `meta` (creation time, tool, notes, `provenance_id`) | No |
| ZoneSet | Yes | canonical zones (§8) | authoring metadata | New revision per edit |
| Policy | Yes | all fields | — | No (new version per change) |
| RunConfig | Yes | all fields | — | No |
| **Evaluation** | **Yes** | whole body (§10) | `run_meta` (kept in the manifest) | No |
| **Evidence manifest** | No (encoders/decoders may vary) | not part of the evaluation hash; has its own `evidence_manifest_hash` | — | Append/regenerate |
| **Review log** | No | not part of the evaluation hash; own hash optional | — | Append-only |
| Run manifest / export manifest | Partly | digests of the artifacts above | `run_meta` | No |
Evaluation output never contains evidence file hashes or human review fields. Evidence and reviews refer to the evaluation by `evaluation_id` and `evaluation_hash`.

## 3. Identity derivation (all deterministic)
| Id | Derivation |
|---|---|
| `source_digest` | SHA-256 of the source file bytes |
| `source_id` | `src_` + `digest16(source_digest)` (file sources). Simulated/live sources: `src_` + `digest16(sha256(CJ-1 {kind, source_label, config_digest}))`, where the config excludes credentials. A prefix collision between two different sources is a refusal |
| `frame_table_digest` | SHA-256 of CJ-1 `{time_base, start_ticks, pts_ticks[], last_frame_duration_ticks, width, height, rotation}` |
| `obsset_digest` | SHA-256 of the CJ-1 ObservationSet hashed body; `obs_` + `digest16` is its short id |
| `obj_key` | Author/adapter-assigned, `^[A-Za-z0-9_-]{1,32}$`, unique within a frame. **ObjRef** = `"{epoch}:{frame_index}:{obj_key}"` |
| `track_id` | `^[A-Za-z0-9_-]{1,32}$`, unique within (source, epoch). **TrackRef** = `"{epoch}:{track_id}"` |
| `zone_ref` | `"{zone_id}@{revision}#{digest8}"` where `digest8` is the first 8 hex of the zone digest |
| `zones_digest` | SHA-256 of CJ-1 array of the evaluated zones (canonical form, sorted by `zone_id`) |
| `policy_digest` | SHA-256 of the CJ-1 policy body |
| `run_config` | `{ "analysis_stride": 1 }` (P0: must be 1); hashed |
| `input_digest` | SHA-256 of CJ-1 `{source_digest, frame_table_digest, frame_table_provider, obsset_digest, zones_digest, policy_digest, run_config, engine}` |
| `evaluation_id` | `eval_` + `digest16(input_digest)` (no random run id) |
| `determinism_hash` | SHA-256 of the CJ-1 evaluation body (§10) |
| `event_id` | `evt_` + `digest16(sha256(CJ-1 {input_digest, rule_id, rule_version, zone_ref, epoch, key, start_frame_index}))` where `key` is the TrackRef for per-track rules and `null` otherwise. Stable if contributors change later |
| `evidence_id` | `ev_` + `digest16(sha256(CJ-1 {event_id, request_index, kind}))` (not content-derived; stable across encoders) |
| `review_id` | `rev_` + UUIDv4 (allowed only in the review log) |
`frame_table_provider` = `{id, version}` of the component that produced the PTS table (selected by feasibility spike FS-01; no provider is approved).

## 4. Time
- Source frame table: `time_base {num, den}` (positive integers), `pts_ticks[i]` for frames `i = 0..N−1` in display order, `start_ticks = pts_ticks[0]`, `last_frame_duration_ticks`, `frame_count = N`.
- **P0 requires strictly increasing `pts_ticks`**; otherwise the run is refused (V-16). Live `timestamp_anomaly` handling is deferred to the pre-P1 design.
- `round_div(n, d) = (2n + d) // (2d)` (integer division, round half up) for `n ≥ 0`, `d > 0`.
- `pts_us(i) = round_div((pts_ticks[i] − start_ticks) × num × 1 000 000, den)`. Frame 0 is therefore 0 µs. Rounding is applied to each frame's absolute value, never accumulated.
  Example (30 fps, base 1/30): frame 400 → 13 333 333 µs; frame 359 → 11 966 667 µs; frame 372 → 12 400 000 µs.
- `last_frame_duration_us = round_div(last_frame_duration_ticks × num × 1 000 000, den)`; `stream_end_us = pts_us(N−1) + last_frame_duration_us`. Frame `i` covers `[pts_us(i), pts_us(i+1))`; the last covers `[pts_us(N−1), stream_end_us)`.
- Durations and thresholds are integer microseconds; threshold comparisons are `≥` on `pts_us` differences. Variable frame rate needs no special handling; `vfr` is informational.
- FrameRef = `{source_id, epoch, frame_index, pts_us}` (outputs). Observation files reference frames by `frame_index` only (the source frame table is the single source of timing truth).
- `mm:ss.mmm` display strings are rendered by the UI (floor to milliseconds) and never stored in hashed artifacts.
- Wall-clock fields (`capture_utc_us`, `clock_source`, `mono_ns`) exist only in `run_meta` or live logs and never enter decisions.

## 5. Coordinates and geometry
### 5.1 Space
`image_px_v1`: origin top-left, x right, y down, **continuous edge coordinates**: pixel `(i,j)` covers `[i,i+1)×[j,j+1)`; the frame is `[0,W]×[0,H]` in the decoded, display-oriented frame (container rotation applied; `rotation_applied` recorded).
All stored geometry is integers in this space. Geometry is evaluated in **doubled coordinates** (`×2`) so that half-integers are exact integers.
World/metric coordinates do not exist; image-space proximity never implies physical distance.
### 5.2 Bounding box
`bbox {x, y, w, h}` integers with `w ≥ 1`, `h ≥ 1`, `x ≥ 0`, `y ≥ 0`, `x + w ≤ W`, `y + h ≤ H`. Violation: replay observation sets are refused (V-06); inference adapters must clamp and set `truncated` flags, or emit `adapter_error` for the frame.
`truncated {left, right, top, bottom}` (booleans, default false) must agree with the geometry: `left ⇒ x = 0`, `right ⇒ x + w = W`, `top ⇒ y = 0`, `bottom ⇒ y + h = H` (V-07).
### 5.3 Reference point (`bbox_bottom_center`, fixed by rule version 1.0.0)
Doubled coordinates: `Rx2 = 2x + w`, `Ry2 = 2(y + h)`. No rounding is ever applied. Polygon vertices are doubled (`2×`) before testing.
### 5.4 Closed-polygon point test (boundary inclusive)
1. If the point lies on any polygon edge segment, including vertices (collinear by cross product = 0 and within the segment's extent), it is **inside**.
2. Otherwise use the half-open crossing rule with `cross(a,b,p) = (b.x−a.x)(p.y−a.y) − (b.y−a.y)(p.x−a.x)`: for each edge `(a,b)` with `a.y ≤ p.y < b.y` count a crossing iff `cross > 0`; with `b.y ≤ p.y < a.y` count iff `cross < 0`. An odd count is **inside**. Integer arithmetic only.
Test vectors (square (100,500),(900,500),(900,1000),(100,1000)): bbox `x=480,y=900,w=40,h=100` → (500,1000) on the bottom edge ⇒ inside; `x=880,w=40` → x=900 on the right edge ⇒ inside; `x=881,w=40` → x=901 ⇒ outside; `x=880,w=41` → x=900.5 ⇒ outside; `y+h=1001` ⇒ outside.
### 5.5 Truncated objects (uncertainty segment)
If `truncated.left`, `.right`, or `.bottom` is set, the true reference point may lie outside the frame or further along the truncated direction. Define the in-frame segment `S` at `y = y + h`: `x` ranges over `[0, ref.x]` if only left is set, `[ref.x, W]` if only right, `[0, W]` if both, else the single point `ref`.
`.top` alone does not affect the reference point. Occupancy is **never TRUE**; it is FALSE iff `S` (closed) does not intersect the closed polygon, otherwise UNKNOWN (`object_truncated_at_frame_edge`). Segment/polygon intersection: an endpoint inside the closed polygon, or closed segment–segment intersection with any polygon edge (orientation tests, collinear overlap included), all in integers.
### 5.6 Zone polygon validity and canonical form (V-12)
3 ≤ vertices ≤ 256; integer coordinates within `[0,W]×[0,H]`; no duplicate vertices; no zero-length edges; **no three consecutive collinear vertices** (remove redundant vertices); non-zero area; simple (adjacent edges share only their common vertex; non-adjacent edges do not intersect or touch).
Canonical form: orientation with positive `area2 = Σ(xᵢ·yᵢ₊₁ − xᵢ₊₁·yᵢ)` and rotation so the first vertex is lexicographically smallest by `(y, x)`. Equivalent polygons therefore hash identically.

## 6. Source record
```json
{ "source_id": "src_…", "kind": "file|simulated|webcam|rtsp", "source_digest": "<64hex>", "provenance_id": "prov_…",
  "media": { "container": "mp4", "codec": "h264", "width": 1920, "height": 1080, "rotation_applied": 0, "frame_count": 900, "vfr": false },
  "frame_table": { "time_base": {"num":1,"den":30}, "start_ticks": 0, "pts_ticks": [0,1,2], "last_frame_duration_ticks": 1 },
  "frame_table_provider": { "id": "(unselected)", "version": "…" }, "frame_table_digest": "<64hex>", "credential_ref": null, "calibration": null }
```
(`pts_ticks` abbreviated.) For `simulated` sources, `generator` references the generation-script entry and seed in MG-PROVENANCE-001. The supported container/codec set is **UNVERIFIED and must be fixed by feasibility spike FS-07**; unsupported input is refused with `mg.refusal.unsupported_media`.

## 7. ObservationSet
```json
{ "schema": "mg.obsset.0.2",
  "source_binding": { "source_digest": "<64hex>", "frame_table_digest": "<64hex>", "frame_count": 900, "width": 1920, "height": 1080 },
  "coord_space": "image_px_v1", "taxonomy": "mg.cls.v0",
  "origin": "synthetic_scripted|human_annotated|model_inference",
  "producer": { "adapter_id": "replay", "adapter_version": "0.1.0", "model_id": null, "weights_sha256": null, "params_hash": "…", "label_map_digest": null },
  "tracks": [ { "track_id": "P1", "epoch": 0, "kind": "scripted|annotated|tracker", "identity_breaks": [100],
                "terminated": null } ],
  "frames": [ { "frame_index": 372, "status": "analyzed", "objects": [
      { "obj_key": "o1", "cls": { "label": "person", "state": "known" },
        "bbox": { "x": 410, "y": 220, "w": 64, "h": 180 }, "truncated": { "left": false, "right": false, "top": false, "bottom": false },
        "confidence": { "permille": null, "kind": "not_applicable" }, "track_ref": "P1" } ] } ],
  "frame_status_ranges": [ { "from_frame": 380, "to_frame": 392, "status": "not_analyzed_dropped" } ],
  "meta": { "created_utc": "…", "tool": "…", "provenance_id": "prov_…" } }
```
- **Taxonomy `mg.cls.v0`:** labels `person`, `vehicle` (any motorized vehicle or mobile equipment; no sub-types in v0), `other` (known non-target, ignored by rules). Mapping detector labels to these is the adapter's job and is recorded by `label_map_digest`.
- `cls.state`: `known` | `ambiguous` (with `candidates`, a subset of the three labels) | `unknown_class` (ambiguous across all labels).
- **Frame coverage:** `frames` (status `analyzed`) and `frame_status_ranges` (inclusive both ends; statuses `not_analyzed_stride | not_analyzed_dropped | source_gap | adapter_error`) must partition `[0, N−1]` exactly (V-05). `analyzed` with `objects: []` means "analyzed, nothing found"; every other status means **unknown, not empty**.
- `confidence.kind`: `not_applicable | human_asserted | model_uncalibrated | model_calibrated`. `synthetic_scripted` requires `permille: null, kind: not_applicable` (never 1000). Otherwise `permille` is an integer 0–1000 or null. Null is exempt from any policy threshold.
- **Tracks:** each `track_ref` in an object must be declared; at most one object per track per frame; one class label per track unless the object state is ambiguous (V-08). `identity_breaks` lists frame indexes at which the producer does not vouch for identity continuity (dwell restarts there).
  `terminated`: `null` or `{ "frame_index": f, "reason": "exited_view|unresolved" }` with `f` after the track's last object. Track `kind` uses the same vocabulary family as observation `origin`: `scripted ↔ synthetic_scripted`, `annotated ↔ human_annotated`, `tracker ↔ model_inference`; a track's kind must be compatible with the set's origin (V-09).
- `origin` rules: a replay adapter may only declare `synthetic_scripted` or `human_annotated`. `model_inference` requires non-null `model_id` and `weights_sha256` in the producer and an approved adapter (V-09).

## 8. ZoneSet
```json
{ "zone_id": "zone_A", "revision": 3, "source_id": "src_…", "coord_space": "image_px_v1", "width": 1920, "height": 1080,
  "kind": "restricted|transit|monitored", "polygon": [[100,500],[900,500],[900,1000],[100,1000]],
  "reference_frame_digest": "<64hex>", "valid_for": "fixed_camera_only", "digest": "<64hex>" }
```
Immutable revisions (`digest` over the canonical zone without authoring metadata). Zone `kind` is **not** a severity; each rule lists the kinds it evaluates (`zone_kinds`). `width`/`height` must equal the source frame size (V-13). Camera movement is not detected in the MVP; the UI states the fixed-camera assumption.

## 9. Policy (named built-in rules, validated parameters; no expression language)
```json
{ "policy_id": "mg-policy", "version": "0.1.0", "semantics_version": 1,
  "common": { "gap_tolerance_ms": 1000, "track_gap_tolerance_ms": 500, "track_unknown_hold_ms": 3000,
              "min_confidence_permille": null, "below_threshold_handling": "unknown" },
  "rules": [ { "rule_id": "R_PERSON_AND_VEHICLE_IN_ZONE", "rule_version": "1.0.0", "params": { "debounce_ms": 500, "zone_kinds": ["restricted"] } },
             { "rule_id": "R_PERSON_DWELL_NO_VEHICLE", "rule_version": "1.0.0", "params": { "dwell_ms": 1500, "debounce_ms": 500, "zone_kinds": ["restricted"] } } ] }
```
Validation (V-14): `rule_id`/`rule_version` must exist in the engine; each parameter present with the right type: `*_ms` integers in `[0, 3 600 000]` (`dwell_ms ≥ 1`); `track_gap_tolerance_ms ≤ track_unknown_hold_ms`;
`min_confidence_permille` null or integer 0–1000; `below_threshold_handling ∈ {"unknown","ignore"}`; `zone_kinds` a non-empty subset of the zone kinds; no duplicate rule instance; no unknown keys. Severity, reference point and boundary are fixed by the rule version, not by parameters.
`policy_digest` covers the whole body. `engine` = `{engine_id, engine_version, semantics_version}` is recorded in `inputs` and the manifest; `semantics_version` must match the policy (V-19).

## 10. Evaluation (deterministic)
```json
{ "schema": "mg.eval.0.2", "evaluation_id": "eval_…", "input_digest": "<64hex>",
  "engine": { "engine_id": "mg-engine", "engine_version": "…", "semantics_version": 1 },
  "inputs": { "source_digest": "…", "frame_table_digest": "…", "obsset_digest": "…", "zones_digest": "…", "policy_digest": "…", "run_config": {"analysis_stride": 1} },
  "epochs": [ { "epoch": 0, "stream_end_us": 30000000,
    "zones": [ { "zone_ref": "zone_A@3#1a2b3c4d",
      "timeline": [ { "start": {"frame_index":0,"pts_us":0}, "end": {"frame_index":75,"pts_us":2500000}, "state": "GREEN",
                      "bridged": false, "event_ids": [], "pending": [], "causes": [] } ],
      "coverage": { "total_us": 0, "analyzed_us": 0, "analyzed_permille": 0,
                    "state_us": { "GREEN": 0, "YELLOW": 0, "RED": 0, "UNKNOWN": 0 }, "unknown_by_reason_us": {} } } ],
    "events": [ { "event_id": "evt_…", "rule_id": "R_PERSON_AND_VEHICLE_IN_ZONE", "rule_version": "1.0.0", "severity": "RED",
      "zone_ref": "zone_A@3#1a2b3c4d", "epoch": 0, "key": null,
      "condition_since": {"frame_index":60,"pts_us":2000000}, "start": {"frame_index":75,"pts_us":2500000},
      "condition_until": null, "end": {"frame_index":300,"pts_us":10000000}, "closed_reason": "end_of_stream", "closed_unknown_reasons": [],
      "left_censored": false, "right_censored": true, "dwell_since": null,
      "trigger_refs": ["0:75:o1","0:75:o2"], "contributors": { "tracks": ["0:P1","0:V1"] },
      "absorbed": [], "bridged": [], "incomplete": [],
      "facts_at_start": { "person_in_zone": "TRUE", "vehicle_in_zone": "TRUE" },
      "origin_summary": ["synthetic_scripted"], "confidence_summary": { "min_permille": null, "kind": "not_applicable" },
      "explanation": { "msg_id": "mg.expl.red.person_vehicle_in_zone", "args": { "zone": "zone_A", "tracks": ["0:P1","0:V1"] } },
      "limitations": ["mg.lim.image_space_only", "mg.lim.synthetic_observations"],
      "evidence_requests": [ {"request_index":0,"kind":"frame_still","purpose":"start","frame_index":75},
                             {"request_index":1,"kind":"observation_dump","purpose":"confirming_window","from_frame":60,"to_frame_inclusive":75},
                             {"request_index":2,"kind":"frame_still","purpose":"last_active","frame_index":299} ] } ],
    "suppressed": [ { "rule_id": "…", "key": null, "zone_ref": "…", "from_frame": 100, "to_frame_exclusive": 110, "reason": "below_open_hold", "terminal_value": "F" } ],
    "dropped_tracks": [ { "track_ref": "0:P1", "last_seen_frame": 100, "reason": "hold_expired", "dropped_at_frame": 190 } ],
    "zones_not_evaluated": [] } ],
  "epoch_gaps": [], "notes": [] }
```
- Intervals are half-open `[start.pts_us, end.pts_us)` and gap-free; `end.frame_index` is the first frame *after* the interval (or `N`). A new interval starts whenever `state` or `event_ids` changes. `causes` entries: `{reason, from_frame, to_frame_exclusive, refs[≤8], refs_truncated}`.
  `pending` entries: `{rule_id, key, since_frame}` (raw condition seen but not yet confirmed).
- `closed_reason ∈ {rule_cleared, inputs_unknown, unbridged_gap, track_discontinuity, epoch_boundary, end_of_stream, cancelled}`; `closed_unknown_reasons` uses the unknown-cause enum of MG-RISK-001 §5.1 (this removes the earlier mismatch with `unknown_reasons`).
- Evidence **requests** are deterministic functions of the event (frame indexes derived from `start`/`end`). The evaluation never contains evidence files or their hashes.
- `explanation` and `limitations` are message ids; no free text.
- `epoch_gaps[]`: `{from_epoch, to_epoch, state: "UNKNOWN", reason: "epoch_boundary", zone_validity_unverified: true}`.

## 11. Evidence manifest (separate artifact; not part of `determinism_hash`)
```json
{ "schema": "mg.evidence.0.2", "evaluation_id": "eval_…", "evaluation_hash": "<determinism_hash>",
  "items": [ { "evidence_id": "ev_…", "event_id": "evt_…", "request_index": 0, "kind": "frame_still_raw|frame_still_overlay|observation_dump",
    "frame": { "source_id": "src_…", "epoch": 0, "frame_index": 75, "pts_us": 2500000 },
    "file": { "path": "evidence/ab/….png", "file_sha256": "…", "bytes": 0, "media_type": "image/png", "encoder": { "id": "…", "version": "…" } },
    "pixel_sha256": "<sha256 of decoded RGB24 buffer, row-major, top-left origin, rotation applied>", "decoder": { "id": "…", "version": "…" },
    "pixels_origin": "source_frame|rendered_overlay", "render_recipe": { "id": "overlay", "version": "0.1" }, "origin_banner_msg_id": "mg.banner.synthetic_replay",
    "retention_class": "default", "redaction": "none" } ] }
```
Every `frame_still` request yields a raw item and an overlay item. `verify` recomputes (a) `file_sha256` of each stored file, (b) `pixel_sha256` by re-decoding the source frame with the recorded decoder, (c) the observation dump against the ObservationSet.
Encoded file bytes may differ across encoder versions; this is why evidence is outside the determinism contract. Derived stills inherit the source's provenance and retention class.

## 12. Review log (mutable, append-only; not part of `determinism_hash`)
```json
{ "schema": "mg.review.0.2", "evaluation_id": "eval_…", "evaluation_hash": "<determinism_hash>",
  "entries": [ { "review_id": "rev_<uuid>", "target": { "type": "event", "event_id": "evt_…" },
                 "state": "unreviewed|confirmed|rejected|needs_followup", "note": "…", "reviewer_label": "…", "created_utc": "…" },
               { "review_id": "rev_<uuid>", "target": { "type": "interval", "zone_ref": "…", "epoch": 0, "start_frame_index": 80 },
                 "state": "confirmed", "note": "…", "reviewer_label": "…", "created_utc": "…" } ] }
```
UNKNOWN intervals are valid review targets. A review never alters the evaluation.

## 13. Run / export manifest
```json
{ "schema": "mg.manifest.0.2", "evaluation_id": "eval_…", "mode": "batch_replay|live",
  "digests": { "source": "…", "frame_table": "…", "obsset": "…", "zones": "…", "policy": "…", "input": "…",
               "determinism_hash": "…", "evidence_manifest_hash": "…|null", "review_log_hash": "…|null" },
  "engine": { "engine_id": "…", "engine_version": "…", "semantics_version": 1 },
  "origin_summary": ["synthetic_scripted"],
  "run_meta": { "wall_utc": "…", "host_baseline": "…", "decoder": { "id": "…", "version": "…" } } }
```
**Reproducibility definition** (MG-RISK-001 §2): identical `input_digest` (which includes the engine version and rule versions) ⇒ identical `determinism_hash`. Not yet demonstrated.

## 14. Validation and refusal catalog
| ID | Rule | Scenario |
|---|---|---|
| V-01 | `source_binding.source_digest` equals the loaded source digest | S-32 |
| V-02 | Dimensions and `frame_count` match the source | S-33 |
| V-03 | `frame_table_digest` matches | S-33 |
| V-04 | `coord_space` known (`image_px_v1`) and equal to the source's | S-50 |
| V-05 | `frames` ∪ `frame_status_ranges` partition `[0, N−1]` exactly | S-53 |
| V-06 | Bbox integers, `w,h ≥ 1`, inside the frame | S-51 |
| V-07 | `truncated` flags consistent with the bbox | S-51 |
| V-08 | Unique `obj_key` per frame; declared `track_ref`; ≤1 object per track per frame; consistent track label; `terminated.frame_index` after last object | S-57 |
| V-09 | `origin` consistent with producer/adapter; replay cannot declare `model_inference`; track kinds compatible | S-34 |
| V-10 | Confidence rules (synthetic ⇒ null/not_applicable; integers 0–1000) | S-34 |
| V-11 | No forbidden physical-unit fields while `calibration` is null | S-55 |
| V-12 | Zone polygon validity (§5.6) | S-52 |
| V-13 | Zone dimensions/ids/kind valid; zone belongs to the source | S-52 |
| V-14 | Policy validity (§9) | S-54 |
| V-15 | `run_config.analysis_stride == 1` in P0 | S-48 |
| V-16 | Source frame table strictly increasing (P0) | S-31 |
| V-17 | Canonical JSON profile (no floats, duplicate keys, non-NFC) | S-50 |
| V-18 | Exports contain no credential or URL with userinfo | S-56 |
| V-19 | `semantics_version` of policy equals the engine's | S-54 |
| V-20 | No unknown fields in hashed bodies | S-50 |

## 15. Live-mode note (P1 design inputs; not detailed in MG-02R)
A live run is reproducible by replaying its **recorded observation log** (final evaluation equals the live final evaluation; live output is provisional until the lookahead of `max(gap_tolerance, hold)` elapses). Dropped frames are logged as `not_analyzed_dropped` holes; reconnects increment `epoch`.
