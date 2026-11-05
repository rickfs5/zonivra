# Zonivra

> Experimental decision-support prototype. **Not** a certified safety system, collision-avoidance product, or replacement for trained operators. Model accuracy on industrial footage has **not** been validated. Rights: **ALL RIGHTS RESERVED** (provisional notice; no licence granted).

**One line:** AI observes, deterministic rules decide, and every result carries its provenance, its uncertainty and a human reviewer.

## Accepted source
| Item | Value |
|---|---|
| Commit | `afe4b396a01106c78a36b7d6b3557a86d18ce0d0` |
| Tree | `491dddb1c04bc72d5b84e59ec5b681ee1a64fec1` |
| Release archive | `zonivra-release-candidate-r32.tar.gz`, SHA-256 `99bc15bdb049e5e519007291680f2b3c8736c8f54a77ab63ff3509d969720535`, 268 files |
| Independent technical verdicts | UI `UI_INDEPENDENT_PASS`; delta `FV29_DELTA_PASS`; release `RELEASE_33_TECHNICAL_PASS` (technical only: no legal, ownership, contest-eligibility or publication clearance) |
| Owner manual acceptance (4 Nov 2026) | offline synthetic demo, general UI, and the external Meta evidence review: PASS (owner's own visual acceptance) |

## What it is
A local-first review console for industrial video. A local detector (RF-DETR Nano) produces observations; a **deterministic Risk Kernel** turns observations plus operator-drawn zones into `GREEN / YELLOW / RED / UNKNOWN`; incidents carry a timeline, unknown intervals, an evidence panel and a JSON export. Every record says what it is: real inference, synthetic drill, restored, simulated, or imported external evidence.

## Capabilities matrix
| Capability | State | Evidence |
|---|---|---|
| Local MP4 analysis with RF-DETR Nano (hash-pinned weights supplied by the user) | Works locally; **accuracy not validated**; pre-hackathon core | stored run `run_2c69dd64…` (248 of 742 frames), shown "stored / not verified" |
| Deterministic Risk Kernel, zones, tracker, alerts, incidents, JSON export | Works; pre-hackathon core | historical suites; 6 synthetic scenarios (case 01 → RED by rule; 02b never RED — shows a rule limit) |
| Incident investigation workbench, timeline, evidence/trust panel, source lifecycle | Works; event-window | browser acceptance |
| **External Meta SAM 3.1 result import and review** (saved stream + provenance manifest, strict observed profile `META_SAM31_OBSERVED_1`, byte/hash/image linkage, negative controls) | Works offline; event-window | 278 backend tests; owner-verified on the genuine response |
| Per-record provenance labels (real inference / synthetic / restored / simulated) | Works; **began in the earlier MineGuard prototype** | pre-event commits (see `CONTRIBUTION_TIMELINE.md`) |
| Provenance classes for imported and synthetic results (`EXTERNAL_MANUAL_PROVIDER_RESULT`, `HASHES_VERIFIED_AUTHENTICITY_NOT_PROVEN`, `SYNTHETIC_SCENARIO_REPLAY`) | Works; **added during the event** | UI + tests |
| Light / Dark / System themes; English and Spanish; risk word + glyph (not color alone) | Works | 42/42 + 43/43 browser checks |
| Cost Governor and read-only provider status; **simulated** explanation | Works; explanation is labelled SIMULATED | tests |
| Privacy-ON default; loopback-only server with socket guard | Works | tests; 0 non-loopback attempts in 279 backend tests |
| Webcam | Baseline only; generic HTTP 500 if the detector can't load; a proposed fix was independently rejected and is excluded; never exercised on a physical camera | `docs/release/KNOWN_LIMITATIONS.md` |
| Native / live Meta inference, Muse, Baseten, SAM-video, LMX | **Not available / not claimed** (disabled). Inert Meta-transport source files exist in the tree; nothing imports them (static invariant test); their independent reviews failed | `backend/tests/test_meta_invariants.py` |

## What the genuine Meta evidence is
One SAM 3.1 request was executed by the owner **outside** Zonivra on a synthetic truck image. Zonivra imports the saved stream offline and shows: provider `meta_sam31`, model `sam-3.1`, one bounding box (x 40–559, y 150–331 on a 640×384 image), one `UNCLASSIFIED` segment, confidence *not reported*, mask structure checked but not rendered, **no risk assessed**, `canonical_effect: NONE`. Response SHA-256 `698b0b6314780550ec41859bdabbdf1f19400612906155827fec7bf509bf3036`, image `721c3589325faad0ec927da224caf9bfbad41b393a9222cae39f59575d1d2e1d`, manifest `c064e40a1b70a2095187c75df64da34181ddba32031ee78a50873314ef261016`. Hashes prove these are the declared bytes, **not** that Meta produced them. The raw response is private and not distributed; a synthetic fixture lets anyone try the same workflow.

## Architecture (text source)
```
MP4 (user-supplied) ─ AVFoundation decode ─► RF-DETR Nano (local, hash-pinned weights) ─► observations (with provenance)
operator zones ────────────────────────────────────────────────────────────────────────► deterministic Risk Kernel ─► GREEN/YELLOW/RED/UNKNOWN ─► incident, timeline, evidence, JSON export
saved external SAM 3.1 stream + manifest ─► strict offline importer ─► isolated review record  (never enters the Risk Kernel)
SIMULATED explanation: reads the incident record, cannot change it
FastAPI on 127.0.0.1 (socket guard) · React/Vite UI · human reviewer decides
```

## Human-review workflow
Observe → label provenance → rule-based risk (UNKNOWN is never shown as safe) → incident with unknown intervals → reviewer inspects evidence/hashes → exports JSON. Imported external results are reviewed, never scored.

## Run it (macOS, Python 3.13 + `uv`, Node/npm)
```sh
shasum -a 256 zonivra-release-candidate-r32.tar.gz   # expect 99bc15bd…0535
mkdir zonivra && tar -xzf zonivra-release-candidate-r32.tar.gz -C zonivra && cd zonivra/zonivra-release-candidate
shasum -a 256 -c SHA256SUMS.txt
uv venv --python 3.13 .venv && uv pip install --no-deps --python .venv/bin/python -r backend/requirements.freeze.txt
(cd frontend && npm ci --ignore-scripts && npm run build)
MG_DEMO_PORT=8790 bash scripts/demo_v07.sh     # loopback, isolated storage; open http://127.0.0.1:8790/?lang=en
```
Judge path without weights or keys: **Settings → External provider results → Load synthetic demo**; **Simulations** (six deterministic scenarios); toggle theme/language. Local RF-DETR analysis additionally needs the pinned weight file (`models/MANIFEST.json`) and an MP4 you may use; neither is distributed.

## Tests and evidence (run by the project author; independent where stated)
Vitest 178/178 · typecheck and build OK · Meta backend 278 passed + 1 expected skip · 0 non-loopback connection attempts · demo health 200 and clean SIGTERM stop · browser UI 42/42 (author) and 43/43 (independent) · chip test 28/28 · release archive verified by Codex (248/248 source files byte-identical).

## Known limitations
Accuracy unvalidated; macOS-only MP4 decode; webcam baseline limitation and no physical-camera test; seven `test_d1_*` tests fail by design in this baseline; SBOM partial; no accessibility audit (screen reader, zoom, forced-colors); populated Dashboard/Feeds states not browser-tested; Python clean install and Ctrl-C not exercised; independent reviews were largely static; single image proves nothing about SAM performance.

## Security and privacy
Loopback only (`netguard`); no credentials needed or stored; no provider call path armed; Privacy-ON webcam default (metadata-only); private provider response, weights, media and local runs excluded from the release.

## Pre-existing work and event contributions
**Before the event (commits dated 2026-10-24 to 2026-10-26):** MP4 decoding, local RF-DETR Nano inference, tracker, zones, the deterministic Risk Kernel, the operations console, multi-source monitoring, and the first provenance/trust labelling.

**During the event (commits dated 2026-10-28 to 2026-11-04):** the incident investigation workbench and timeline, the Meta SAM 3.1 external-result import and review workflow with its strict observed-response profile, provenance classes for imported results, English localization, deterministic synthetic scenarios, and the Light/Dark/System interface. The commit-by-commit table, with its limits, is in [`CONTRIBUTION_TIMELINE.md`](CONTRIBUTION_TIMELINE.md).

## Rights
Original Zonivra source is **ALL RIGHTS RESERVED**. This repository grants no licence to use, copy, modify or redistribute it. Third-party components remain under their own licences (see `LICENSE`, `NOTICE`, `THIRD_PARTY_NOTICES.md` and `docs/release/LICENSE_MATRIX.md`; weights, stock media and the raw provider response are not distributed). Nothing here asserts ownership clearance or compatibility with any contest licensing requirement.

## Links
Project video: `[VIDEO_URL — not yet published]`.
