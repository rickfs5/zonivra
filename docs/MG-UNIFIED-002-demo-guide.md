# MG-UNIFIED-002 — Two-minute demonstration of the unified Zonivra / MineGuard

Status: EXPERIMENTAL, PROPOSED. Not accepted by the owner. Decision-support prototype; not a safety system.
Date: 2026-11-01.

## 1. Start (isolated server and storage; never the owner's live server or data)
From the root of the `integration/meta-local-first` worktree (the directory that contains `backend/`, `frontend/` and `scripts/`):

```
MG_DEMO_PORT=8790 bash scripts/demo_v07.sh      # prints http://127.0.0.1:8790 when healthy; Ctrl-C stops it and removes its storage
```
(Choose any free port; the script refuses a port already in use.) Automated, scripted version of the same journey (headless Chrome, screenshots to `logs/unified_sprint/`):

```
MG_E2E_PORT=8771 bash scripts/run_mvp_e2e.sh e2e/unified_acceptance.mjs
```
Rebuild the UI after a frontend change: `cd frontend && npm run build` (uses the already installed `node_modules`; nothing is downloaded).

## 2. Script (about 2 minutes)
| Time | Say / do | What is REAL | What is SIMULATED or SYNTHETIC |
|---|---|---|---|
| 00:00 | "Zonivra: local-first review of mining video; the AI observes, deterministic rules decide." Show Resumen (no sources yet, no invented tiles). | the empty state | — |
| 00:15 | Multi-fuente → "Añadir fuente" (the registered nonpersonal MP4 `test-assets/pexels_6565442.mp4`) → Iniciar. | local file, SHA-pinned | — |
| 00:30 | Wait for "analizados" ≥ 2: RF-DETR Nano runs locally on this machine. | **real local inference** (model `rf-detr-nano-coco`, weights hash-verified) | its accuracy is NOT validated |
| 00:50 | Point at the class counts and the source lifecycle list (started → first observation → person/vehicle appearances). | counts come from the model | — |
| 01:10 | Open the zone editor; the risk badge equals the deterministic kernel's state (this MP4 produced no RED; none is faked). | kernel decision over real detections | — |
| 01:25 | Simulaciones → "Simulación ROJO" (labelled SINTÉTICO) → Eventos y alertas → open the incident. | the kernel's YELLOW → RED decision | the scripted scenario |
| 01:40 | Incident drawer → Investigación: timeline (two tracks), UNKNOWN intervals, evidence panel (metadata only), "Exportar JSON del incidente". | the projection of stored facts | — |
| 01:50 | "Generar explicación simulada": badge EXPLICACIÓN SIMULADA, severity unchanged, text says no model was called and no video inspected. | — | the explanation (deterministic fake, validated structurally/lexically) |
| 02:00 | Configuración → Proveedores: local READY; Meta DISABLED, no credentials, billing not approved, live not verified; Muse SIMULATED; US$ 0, 0 external calls. Close. | the state of this build | — |

## 3. Acceptance checklist for manual use
- [ ] The first source shows `real_inference` provenance and a named model; no other source is described as a camera.
- [ ] No RED appears from the real MP4 unless the kernel produced it; the RED in the drill is labelled SINTÉTICO everywhere.
- [ ] The explanation never changes the severity badge; closing and reopening the incident shows the same state.
- [ ] Configuración → Proveedores has no button, switch or input.
- [ ] The browser's network panel shows only requests to the local origin.
- [ ] Privacidad ACTIVADA and no camera started.

## 4. Known limits of the demonstration
- The real-model path produces no risk event on this MP4 within the demonstration time; the explanation of a REAL-model incident is covered by unit tests with constructed incidents, not by a browser run (the E2E records it as NOT_EXERCISED).
- A WebM clip cannot be analysed by this backend (AVFoundation MP4 route). The Meta Lab's browser import and its WebM duration fix belong to the separate experimental worktree and are not part of this branch.
- RF-DETR accuracy is not validated; local digests are consistency aids, not authenticity.
