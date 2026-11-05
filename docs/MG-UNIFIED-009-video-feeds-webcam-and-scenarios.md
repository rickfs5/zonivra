# MG-UNIFIED-009 — Webcam model-failure remediation (D1), camera state UX (D2/D3) and deterministic industrial scenarios (FINAL-ENGINEERING-15-R1)

Status: IMPLEMENTED, tested offline, **UNREVIEWED by an independent reviewer**. Baseline `de8a08a`. Nothing here was run on a physical camera; no model was loaded; no provider was contacted.

## 1. D1: webcam start when the model cannot be created
Before: `SessionManager.start` let the detector-factory exception escape → HTTP 500 "Internal Server Error" (UI: "Error: 500 Internal Server Error"). After: only a CLOSED set of initialization failures (`WeightsError`, `OSError`, `ImportError`, `RuntimeError`, `MemoryError`), caught at the single detector-construction call site, becomes HTTP **503** `{"detail":{"msg_id":"mg.refusal.model_unavailable"}}` (the application's existing HTTPException convention). No exception text, path or type is returned. At that point no session, staging directory or cached detector exists and the existing handler releases the inference lock; a retry reaches the factory again (recovery works). Programming errors (`TypeError`, `KeyError`, `ValueError`, ...) are NOT translated: they stay 500. Feeds already handled this failure (`failed` / `model_unavailable` / UNKNOWN) and are unchanged.
## 2. Camera state UX (D2, D3)
`frontend/src/camState.ts` (pure) maps the page's existing machine to seven user-visible states: stopped, requesting, **preview only**, **analysis active**, finalizing, **analysis unavailable**, permission denied, no device, device error. Preview-only shows a banner that nothing is analysed and nothing is sent; only `analyzing` is analysis (`data-analysing`). A failed model start leaves the camera on as a preview and says analysis is unavailable (no risk badge, no GREEN). `getUserMedia` failures are classified by DOMException name. The page shows the model-WEIGHTS status (`/api/model`, digest check; the model itself is loaded only when an analysis starts). EN and ES, `role=status aria-live=polite`, labelled video, hidden overlay.
## 3. Deterministic industrial scenarios
`backend/mineguard/scenarios.py` + `api_scenarios.py` (`GET /api/scenarios`, `GET /api/scenarios/{id}`) + `ScenariosPanel` in Simulations. Hand-written observations (640×360, 5 Hz) go through the PRODUCTION tracker, zone sampling and risk kernel (`live_params`) and are RECOMPUTED per request; nothing is stored, no model, no file, no provider. Each result carries `provenance` (`SYNTHETIC_SCENARIO_REPLAY`, `real_footage:false`, `model_inference:false`, `detections: HAND_WRITTEN…`), the zone, input segments, state samples and transitions, kernel events, coverage, caveats and `review: PENDING_HUMAN_REVIEW`.
| Scenario | Recomputed transitions |
|---|---|
| CASE 01 haul truck + pedestrian | GREEN → UNKNOWN(identity) → GREEN → YELLOW 3.8 s → RED 5.6 s → YELLOW 8.6 s → UNKNOWN 10.6 s → GREEN 12.8 s |
| CASE 02a forklift as a vehicle | …YELLOW 3.8 s → RED 5.6 s → UNKNOWN 8.6 s |
| CASE 02b forklift NOT recognised | YELLOW 3.8–8.6 s, never RED (false negative the rules cannot see) |
| CASE 03a excavator alone | GREEN throughout, no event (no rule for a vehicle alone) |
| CASE 03b excavator + person | RED 5.6–8.6 s |
| CONTROL source silent | UNKNOWN for the silence, never GREEN |
The kernel was not modified. The class mapping (COCO has no forklift, excavator or haul-truck class) is stated as an unverified assumption in each scenario.
## 4. Tests (all offline)
`backend/tests/test_video_feeds15.py` (27), `frontend/src/camState.test.ts` (7), `frontend/src/simulations/scenarioPresent.test.ts` (4 + parity), browser `e2e/simulated_camera_acceptance.sh` (30 checks, canvas-based fake camera, loopback server whose detector factory fails once then returns a test double that sees nothing). Acceptance inventory regenerated (`external_import` etc. unchanged; total 2005). Six negative mutations were each caught (D1 handler removed, handler widened to `Exception`, `OSError` untranslated, forklift-missed case hidden, provenance flag falsified, preview reported as analysis).
## 5. Not claimed
Physical webcam behaviour (permission prompt, labels, frame rate, latency, tab throttling) is untested: PHYSICAL_OWNER_TEST_REQUIRED (see the owner plan in video-feeds-13). The recovery shown in the browser uses a test-double detector, not RF-DETR. Scenarios are not footage, not detections, not incidents, and not evidence of accuracy. The Meta importer, SAM contract, transport, cost governor, providers and risk kernel are untouched (diff vs `de8a08a` empty).
