# ADR-0003 — Interchangeable vision adapters with mandatory origin labeling (aligned in MG-02R)

Status: **PROPOSED** (aligned in MG-02R; not accepted — the owner must accept individually). Date: 2026-10-20.
Alignment record: added the tracker-stage boundary, adapter responsibility for coordinate and label mapping, invalid-output handling, and the origin/track-kind vocabulary (audit findings F-13, F-24). The decisions of the MG-02 version are unchanged.

## Context
Replay of known observations remains the testing mechanism. Since MG-03a, real local inference on recorded MP4 and browser webcam is mandatory P0 (formerly tier X1), subject to license verification and measured feasibility (EXP-001, EXP-002). A future event may evaluate other models. No model or tracker is selected. Simulated detections must never be presented as AI predictions.

## Decision (proposed)
1. One `VisionAdapter` contract: `describe()` returns adapter id/version, **origin**, model id, weights sha256, params hash, label-map digest; `analyze(FramePacket)` returns a `FrameObservation` (MG-DATA-001 §7).
2. `origin ∈ {synthetic_scripted, human_annotated, model_inference}` is mandatory and immutable per adapter. `ReplayAdapter` may emit only the first two and never reads pixels. Only an adapter that passed the model license/evaluation gate may emit `model_inference`, always shown as EXPERIMENTAL. Enforcement is by validation (V-09) and conformance tests, not by trust.
3. Origin appears in the UI banner, observations, events (`origin_summary`), evidence and exports.
4. The rule engine consumes only the observation contract and must not import adapter code. Swapping adapters requires zero engine changes (A-P0-19, A-X1-06).
5. The adapter maps detector output into the canonical space: it converts coordinates into `image_px_v1`, maps labels into `mg.cls.v0` (recording `label_map_digest`), clamps boxes and sets `truncated` flags. A frame it cannot represent validly is reported as `adapter_error` (UNKNOWN), never as an empty frame.
6. **Identity is a separate, optional stage.** A `TrackerStage` (unselected) supplies `track_ref`, `identity_breaks` and `terminated`; its output kind is `tracker` (compatible with origin `model_inference`). Without it, real detections are untracked and per-track rules report `identity_unavailable`.
7. Confidence for synthetic data is `null / not_applicable`, never a numeric score. Model scores are uncalibrated unless a calibration study exists. No physical distance, speed or collision prediction is derived from observations.
8. Each adapter must pass the conformance suite; failures/timeouts produce `adapter_error` frames, never empty results.

## Consequences
Honest separation of simulation from inference; a path to evaluate other detectors later. Extra conformance work and a stricter contract; per-track YELLOW in X1 depends on an unselected tracker.

## Alternatives considered
Hard-wiring one detector (couples legality/performance to one model); letting adapters emit severity (violates ADR-0002); embedding tracking inside the adapter (hides identity assertions from provenance review).

## Verification (intended, not yet demonstrated)
A-P0-04, A-P0-05, A-P0-19, A-X1-04, A-X1-06; static check that the engine has no adapter imports; contract test with a fake adapter.

## Open issues
Model selection BLOCKED by G-4; tracker selection BLOCKED by G-4 (license lineage); haul-truck coverage by the `vehicle` class is untested; eligibility of any Meta model is governed by G-5.
