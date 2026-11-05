# ADR-0005 — One shared model and one inference worker for all feeds, with bounded queues and least-recently-served scheduling

Status: **PROPOSED** (implemented in MG-VERTICAL-02 and measured; not accepted — the owner must accept individually). Date: 2026-10-25.

## Context
Several feeds must be analysed on one 24 GB laptop-class machine with a single local model. One model per feed would multiply memory and contend for the same accelerator; unbounded buffering would hide overload and make results late.

## Decision (proposed)
1. ONE RF-DETR Nano instance and ONE inference thread serve every feed (`feeds.FeedManager`). Readers (one light thread per feed) only decode and offer frames.
2. Every feed has a bounded queue of 2 frames. Overload is explicit: `realtime` mode drops the oldest queued frame (counted), `throttled` mode applies backpressure to the file (nothing dropped, playback slows).
3. Fairness: among feeds with a waiting frame the least recently served one goes next (a counter, not a clock), so equal workloads get equal service and a slow feed cannot starve the others.
4. Freshness is judged at the commit, under the feed lock, immediately before the sample becomes current; stale results are recorded, never shown as current.
5. State that decides risk (tracker, zone engines, kernel, events, counters) is per feed; ids carry the feed namespace; a failure ends only its feed.
6. The model lock is shared with the other model users (MP4 analysis, verification, webcam sessions): they exclude each other (409), so two heavy model processes never run at once.
7. Capacity is a stated, measured number (3 validated, 4 measured, 5 blocked), raised only by an explicit acknowledgement.

## Consequences
Throughput is the model's (≈30 frames/s here) shared equally; adding feeds lowers each feed's rate, never memory use by much. The file source can be slowed by backpressure, a live camera source (future) would use the dropping mode. A second GPU or a second model instance is a different decision.
