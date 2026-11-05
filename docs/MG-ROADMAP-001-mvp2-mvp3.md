# MG-ROADMAP-001 — MVP 2 (multi-feed) and MVP 3 (alerts and analytics) scope

Status: PROPOSED. Date: 2026-10-25 (updated by MG-VERTICAL-02). **The MVP 2 multi-feed core and the MVP 3 alert engine and dashboards described below are now IMPLEMENTED as a candidate in `MG-VERTICAL2-001` (not accepted; the full gate has not run). Not implemented: RTSP, physical multi-camera, external delivery, heat maps.** The text below is the original plan; where it differs, MG-VERTICAL2-001 is authoritative.

## MVP 2 — Multi-feed (starts only after the MVP 1 gates pass)
- Three feeds first; four and five only if each level proves safe. Every feed has its own id, provenance, zones, tracks, risk state, events, sessions and metrics.
- ONE shared, bounded inference worker (never one RF-DETR per feed); fair round-robin scheduling; bounded per-feed queues with an explicit, recorded drop policy; a per-feed rate cap.
- Measured at every level: throughput (FPS), latency p50/p95, queue growth, drops, memory, swap. The last safe level is the stated capacity. A repeated-source test (the same clip on several feeds) is labelled as such and is not three cameras.
- Out of scope: RTSP, cloud, downloaded datasets/models, unattended activation of physical cameras.
## MVP 3 — Alerts and analytics (starts only after MVP 2)
- Alert engine separate from inference: Risk Event → Alert Policy → Alert Record → Dispatcher → Outcome; RED/YELLOW/UNKNOWN rules, deduplication, cooldown, statuses (active / acknowledged / resolved / delivery-failed).
- Channels: in-app alert center; browser Notifications API with explicit user permission; optional, user-controlled sound. No SMS, WhatsApp, e-mail or any network message; simulations never dispatch.
- Operator analytics (real local data only; no invented numbers, no personal pixels) and a developer/diagnostic dashboard (no tokens or credentials).
## MVP 4 and later
Not started and not authorised: physical-camera acceptance, calibration, a better tracker (ByteTrack needs an approved dependency), certified-safety claims (not claimed).
