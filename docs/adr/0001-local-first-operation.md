# ADR-0001 — Local-first operation (amended in MG-02R)

Status: **PROPOSED** (amended in MG-02R; not accepted — the owner must accept individually). Date: 2026-10-20.
Amendment record: MG-02 text said "no outbound network", which conflicted with the camera sources in the architecture. This version separates internet, loopback, camera-LAN and install-time network use (audit finding F-20).

## Context
The prototype handles video of identifiable workers and must stay independent, privacy-preserving, free of paid/cloud services and Meta APIs, and runnable on an Apple Silicon Mac mini (M4 Pro, 24 GB, CONFIRMED baseline).

## Decision (proposed)
Network use is divided into four boundaries (SPEC §10):
1. **NB-1 Internet:** prohibited for the application at run time (no CDN, telemetry, update checks, external APIs). The UI loads no external assets (CSP `'self'`).
2. **NB-2 Loopback:** all processing, storage and UI run locally; the server binds 127.0.0.1 with Host/Origin validation and a per-session token (other local users/processes can reach loopback).
3. **NB-3 Camera LAN (future, not in MVP):** outbound connections to cameras are allowed only after separate explicit owner authorization (gate G-9), per source, to user-configured allowlisted host/port pairs; everything else is refused and logged.
4. **NB-4 Install/fetch time:** any download of packages, models or assets is a separate, explicit, owner-approved, out-of-band action with pinned versions and recorded hashes; never performed by the application.
Remote Control sessions do not change this: the development conversation context is processed by Anthropic's service, so "local-first" describes the application, not the development assistant.

## Consequences
No hosted collaboration or sync. Local security (loopback attacks, retention, credentials) is our responsibility (SPEC §10). The browser plays local MP4 only if the native path passes FS-03/FS-04; otherwise server-extracted frames are used. RTSP needs a server-side path (deferred).

## Alternatives considered
Cloud inference/storage (violates constraints); hybrid sync (deferred, not MVP); a blanket "no network at all" rule (incompatible with the future camera sources).

## Verification (intended, not yet demonstrated)
A-P0-12 (offline run, CSP, loopback binding, Host/Origin negative tests, no outbound attempts); A-P1-10 (allowlist enforcement); A-P1-09 (credential canary).

## Open issues
Token delivery mechanism (decided in MG-03); FileVault is On (CONFIRMED 2026-10-21); no dependency has been audited by running it. MG-03a research found run-time network behavior in candidate libraries (checkpoint download on first use, Hugging Face backbone download and telemetry unless disabled): such artifacts must be pre-cached by an approved NB-4 step and the library paths set offline.
