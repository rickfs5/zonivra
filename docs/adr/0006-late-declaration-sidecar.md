# ADR-0006 — A late client declaration after an unexpected finalization is a bounded, server-judged sidecar, never a rewrite

Status: **PROPOSED** (implemented in MG-VERTICAL-02; not accepted — the owner must accept individually). Date: 2026-10-25.

## Context
A browser that vanishes cannot say how many capture ticks it issued after its last accepted frame. A server that finalizes the session after 15 s of silence is right to stop waiting, but a client that comes back minutes later (reload, sleep, network roam) can supply information that is useful and must not be lost, and must not silently change evidence that may already have been verified or exported (the session identity is content-addressed).

## Decision (proposed)
1. The session is finalized as `client_disconnected` with an honest limit (`tail_exact: false`).
2. For `ADDENDUM_WINDOW_S` = 300 s afterwards the server admits at most 8 monotonic declarations, only for an unexpected closure, only if plausible for the elapsed time at the declared cadence; the server alone decides.
3. An admitted declaration is written to `late_declaration.json` beside the session with `digest_bound: false`. No digest-bound document is rewritten; verification checks the sidecar's form and ignores it for identity and trust.
4. Everything else is refused with a reason; the interval stays unobserved, exact count unknown.
## Consequences
Identity, trust and exports stay stable; the late information is visible but labelled informational and unverifiable. Rebuilding the content-addressed artifact (a new session identity) was rejected because it would silently invalidate earlier verification and exports. The window is memory-based, so a server restart ends it.
