# Release scope

This candidate packages source commit `afe4b396a01106c78a36b7d6b3557a86d18ce0d0` (tree `491dddb1c04bc72d5b84e59ec5b681ee1a64fec1`). It combines the independently accepted Meta external-result baseline, the independently accepted premium UI with synthetic scenario presentation, and one later UI-only change (top-bar notice chip: `frontend/src/app/Shell.tsx`, `frontend/src/i18n_ext.ts`, `frontend/src/style.css`).

## Review status

- The UI-only chip delta was independently reviewed and returned `FV29_DELTA_PASS` in Codex FINAL DELTA GATE 31. The technical source is accepted.
- That acceptance is a technical verdict about the source only. It is **not** a legal, ownership, licensing, eligibility or publication clearance, and it does **not** grant permission to distribute this package.
- This package was not itself submitted to any hackathon or published anywhere by the builder.

## What is excluded or disabled

- The rejected Webcam D1-R2 patch is **not included**. Webcam remains at the earlier accepted Meta-baseline implementation and must not be described as independently accepted D1/D1-R2 functionality. If the detector cannot be created, starting a webcam session returns a generic server error (HTTP 500), not a model-unavailable message (see `docs/release/KNOWN_LIMITATIONS.md`). No physical camera acceptance was performed.
- Native Meta inference, LMX, Muse, Baseten and live provider execution remain disabled and are not claimed. No provider call path is armed.
- The Meta evidence is one genuine SAM 3.1 image response obtained **outside** the application by the owner's manual sender and imported and reviewed offline. It is external manual evidence, not native inference, and its authenticity is not cryptographically proven. The bundled `.sse` is a synthetic fixture; the private genuine response, credentials and authorization material are not distributed.
- RF-DETR weights, stock footage, webcam media, demo videos, local run stores and credentials are not distributed.

## Rights

Original Zonivra source is all rights reserved (provisional notice). Ownership relative to any employer or contractor, the evaluation licence, and third-party media rights are unresolved and listed in `docs/release/LICENSE_MATRIX.md`. See README, LICENSE, NOTICE and THIRD_PARTY_NOTICES before evaluation or publication.

## Pre-hackathon contribution disclosure

The local RF-DETR detector and deterministic Risk Kernel foundation predate the hackathon and must not be attributed to it. See `docs/release/CONTRIBUTIONS.md`.
