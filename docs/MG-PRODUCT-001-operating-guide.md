# MG-PRODUCT-001 — Operating guide (MVP 1)

Status: PROPOSED (night sprint 2026-10-25; MVP 1 candidate, acceptance record in MG-MVP1-ACCEPTANCE-001). Date: 2026-10-25.
Every statement distinguishes IMPLEMENTED (exists and is tested) from PLANNED (see MG-ROADMAP-001). Nothing here is legal, licence, patent or competition-eligibility clearance (gates in MG-GATES-001 stay OPEN).

## 1. Product overview
MineGuard is a private, local-first, experimental **decision-support video-review** tool for mining-safety footage. It detects people and vehicles with RF-DETR Nano running on this machine, tracks them with a simple IoU tracker, evaluates user-drawn restricted zones with a deterministic risk kernel `mg-risk-kernel/2.0.3` (YELLOW: person dwells in a zone with no vehicle; RED: person and vehicle in the same zone) and lets a reviewer inspect, export and verify what happened. It is **not** a safety-critical, certified or collision-avoidance system; it does not measure distance or predict collisions (uncalibrated image space only). The interface is in Spanish.
Ten areas (IMPLEMENTED): Resumen (operator dashboard), Video MP4, Cámara, **Multi-fuente**, Sesiones, Eventos y alertas (alert center), Simulaciones, Estadísticas (trend), Desarrollador (developer dashboard), Configuración (alert and notification settings). Unfinished features say «No disponible» (heat maps, per-physical-camera metrics, several browser cameras, external notifications).

## 2. Deployment and startup (IMPLEMENTED, loopback only)
Prerequisites (git-ignored): `.venv` (Python 3.13), `models/rf-detr-nano.pth`, `test-assets/*.mp4` for MP4 analysis, `frontend/dist`.
```
(cd frontend && npm ci --ignore-scripts && npm run build)
scripts/run.sh          # http://127.0.0.1:8000, loopback only, writes logs/session_token
```
The server binds to 127.0.0.1; there is no authentication beyond a per-launch token delivered to the page, and no public deployment. Internet access by the application is blocked by an in-process network guard.

## 3. Configuration and environment variables
`MG_WRITE_TOKEN_FILE=1` (set by `scripts/run.sh`) writes the session token file. `MG_RUNS_DIR` selects the storage directory (tests and the E2E runner use an isolated, ownership-marked directory). `MG_E2E_PORT` selects the port of the browser E2E runner (it refuses an occupied port). There are no user-editable thresholds: risk parameters are versioned built-in policy (`mg-risk-kernel/2.0.3`); webcam sampling interval is set in the Cámara area (default 300 ms). Privacy is chosen per session in the UI (§5), never by environment variable.

## 4. MP4 usage (IMPLEMENTED)
Video MP4: pick a registered video (files in `test-assets/`), set stride/threshold, «Analizar». The run (`run_<digest>`) stores observations, frames as evidence, provenance and manifest. Draw restricted zones on the video, view the per-zone risk timeline, step through analysed frames, export JSON, and run «Verificar» (deep verification re-decodes stored frames and re-runs the model). A run found on disk after a restart is «SIN VERIFICAR» until verified.

## 5. Webcam usage (IMPLEMENTED)
Cámara: choose **Privacy** (default **ON**, see MG-PRIVACY-001), allow camera access in the browser (nothing starts without the browser permission), «Iniciar cámara», draw zones, «Iniciar análisis», «Detener». Frames are sent only to the local server. The capture origin is declared by the browser and is **not** verified by the server (a test source is not a physical-camera acceptance; physical-camera acceptance is PENDING). Leaving the Cámara area releases the camera at once; a live session that is no longer fed finalizes by itself after the idle timeout (15 s). The device is whatever the browser selects.

## 6. Privacy ON/OFF and metadata
See MG-PRIVACY-001. Summary: ON (default, restored after every capture) stores no image of any kind; real inference still runs; metadata (classes, boxes, tracks, zones, risk states, events, timings) is kept and is **not anonymous**; deep image-based re-verification is impossible afterwards. OFF stores the analysed JPEG frames of that session, shows a «retained» indicator, and can be undone with «Eliminar imágenes».

## 7. Session deletion (IMPLEMENTED)
Sesiones: list with filter by retention, select several, «Eliminar seleccionadas», «Eliminar todas las sesiones de cámara», per-row «Eliminar» and «Eliminar imágenes». A dialog shows the count and the data types and requires typing ELIMINAR; the result shown is the server's per-session outcome. Only webcam sessions are ever deletable; MP4 runs, simulations, videos, weights and review archives are not touched. Details and safety rules: MG-PRIVACY-001 §4.

## 8. Risk kernel, tracking and zones
Kernel semantics: MG-RISK-002 (pure, causal, deterministic, Kleene three-valued; UNKNOWN is never reported as safe). Tracker: original IoU tracker `0.3.0` (a track id is an IoU trajectory, not a verified person; tentative tracks are untracked, so the zone is UNKNOWN rather than YELLOW; ambiguous associations end the tracks involved; one person replacing another in the same place between two samples is NOT detectable and may overstate per-person dwell). Zones: integer polygon geometry, NFC names only, self-intersecting polygons refused, a persisted zone file that is inconsistent returns 409 until repaired by a valid PUT. AI detections never set severity; the built-in rules do.

## 9. RED simulation (IMPLEMENTED)
Simulaciones (and the card in Video MP4): «Simulación ROJO — persona + vehículo». It builds a **scripted, synthetic** person+vehicle scenario on a new run (`synthetic_scripted`), uses no camera and stores no image, passes it through the real risk engine (YELLOW then RED), appears in the event history labelled SIMULACIÓN, and never modifies the original observations or any session. It is never labelled RF-DETR inference and can never be verified or promoted.

## 10. Events, evidence and JSON contract
Event id = `evt_` + 16 hex of SHA-256 over canonical input, policy, zone and rule identity (MG-RISK-002 §8). Evidence references live outside the evaluation digest. Exports are canonical JSON bundles (`mg.export.vs02`, `mg.export.vs02.session`) with provenance, manifest, observation set and evaluation; contracts in MG-DATA-001 and `backend/schemas/`. Privacy ON sessions have an empty evidence list.

## 11. API overview (loopback, token-gated, Host/Origin checked)
`GET /api/health`, `/api/overview`, `/api/events?severity=&source=&include_synthetic=&limit=`; MP4: `/api/videos`, `/api/videos/{id}/analyze`, `/api/jobs/{id}`, `/api/runs[...]` (run, frames, verify, export, zones, risk); simulation/replay: `POST /api/simulations/person-vehicle`, `POST /api/replays`; webcam: `POST /api/webcam/sessions` (field `privacy`, default `"on"`), `POST .../{sid}/frames` (headers `X-MG-Seq`, `X-MG-Capture-Us`; every capture tick of the browser consumes a sequence number, so all loss is a gap), `POST .../{sid}/stop` (optional `X-MG-Next-Seq` = first sequence number not issued: the loss after the last accepted frame is recorded inside the same step that stops intake; a loss that cannot be recorded as a strictly timed unknown sample is refused with 409); `POST .../{sid}/watermark` (heartbeat with `X-MG-Next-Seq`, about once per second; it does not count as activity) lets the idle reaper record the loss after the last accepted frame even if the browser vanished; leaving the Cámara area sends the same stop; a frame that becomes older than 2 s while the detector works on it is recorded as `stale`, never as analysed; zones: `PUT /api/{runs|sessions}/{id}/zones` requires `expected_revision` (compare-and-swap, 409 with the current set on a stale write); stored sessions: `GET /api/sessions[...]`, `.../verify`, `.../frames/{i}` (410 when no image exists), `POST /api/sessions/delete`, `POST /api/sessions/{sid}/media/delete`.

## 12. Architecture and data flow
Browser (React/TS/Vite) → local FastAPI → single shared inference lock → RF-DETR Nano → IoU tracker → zone evaluation → risk kernel → JSON files under `runs/` (runs and sessions; content-addressed ids). Webcam: a bounded queue (2) per session with documented drop policy; frames lost before reaching the server (sequence gaps from failed uploads, skipped captures or failed encodes, and the declared tail at stop) are recorded as `client_loss` stubs (not analysed; the unknown interval starts one declared cadence interval after the last observation, a missing prefix starts at the session origin, and a declared tail lasts `lost × cadence`; counted in `client_loss_events` / `client_lost_frames` and in the digest-bound `accounting` block) so no hold or dwell continues silently across them; a reaper finalizes idle sessions as `client_disconnected`, after which a bounded, server-judged late declaration may be stored as a sidecar (MG-VERTICAL2-001 §4). Read-only views (`/api/events`, `/api/overview`) aggregate stored data and label every source as real inference (trust grant intact), unverified claim, synthetic simulation or human annotation; the four are never merged.

## 13. Test execution and acceptance
`scripts/acceptance_gate.sh` is the full gate (clean tree, sequential, watchdog-guarded). `scripts/test_all.sh` is a convenience script whose browser step only runs through the isolated runner; the destructive browser suites (`e2e/*.mjs`) refuse to run unless started by `scripts/run_mvp_e2e.sh` (isolated server and storage). Targeted runs: `.venv/bin/python -m pytest backend/tests/<module>.py`; `frontend`: `npm run build`, `node_modules/.bin/vitest run`; browser: `MG_E2E_PORT=<free port> bash scripts/run_mvp_e2e.sh e2e/mvp_acceptance.mjs` (and `browser_acceptance.mjs`). Results and the open gate status are in MG-MVP1-ACCEPTANCE-001.

## 14. Claude/Codex process
Claude is the sole Builder; Codex CLI is the independent read-only Reviewer, invoked only through the sanitizing wrapper (`logs/night_review/bin/codex`, controller `scripts/controller.py` with an owner-authorised cap). Findings are preserved verbatim, reproduced, fixed and regression-tested; fixes made after the last review are labelled UNREVIEWED. The owner has final authority.

## 15. Troubleshooting and resource limits
Port 8000 busy: another MineGuard server is running; the E2E runner refuses it (use `MG_E2E_PORT`). Model-using test modules peak near 1.1 GB; run one at a time and not with the browser. Under kernel memory pressure level ≥ 2 the full gate's watchdog aborts by design; do not loosen it, retry when pressure is 1 and ≥ 50 % free. A camera that does not start: check the browser permission; a session that is not stopped finalizes by itself after 15 s of silence.

## 16. Security and privacy boundaries
Loopback only; token + Host/Origin checks; no outbound network from the application; no cloud, no Meta APIs, no paid or commercial models; local hashes prove local integrity only, never external authenticity; any process of the same macOS user is trusted. Metadata is not anonymous; MineGuard cannot erase copies made by the browser, the OS, backups or exports.

## 17. Models and third-party licence inventory
Model: RF-DETR Nano weights (Apache-2.0 per the upstream README; no separate weights licence file found: UNVERIFIED). Packages and licences: MG-VS01-bom-and-provenance (MPL-2.0 components were accepted by the owner for the prototype; this is not legal clearance). Legal, patent, trademark, licence and hackathon-eligibility gates remain OPEN in MG-GATES-001.

## 18–20. Acceptance record, multi-feed, alerts and analytics
See MG-MVP1-ACCEPTANCE-001 (results, open risks; its test inventory is generated) and MG-VERTICAL2-001 (multi-feed, alerts, dashboards, capacity: IMPLEMENTED as a candidate, not accepted, full gate not run). RTSP, physical multi-camera and external delivery are NOT implemented.

## 21. Multi-feed monitoring (IMPLEMENTED, candidate; MG-VERTICAL2-001)
«Multi-fuente»: choose a registered video, a sampling rate (0.5–5 /s in the UI) and a mode («Tiempo real» drops frames when the model cannot keep up; «Sin pérdidas» makes the file wait), then «Añadir fuente» or «Crear 3 fuentes con el mismo video» (a repeated-source CONCURRENCY SIMULATION, labelled as such, never three cameras). «Iniciar todas» starts them; each panel has its own start/pause/resume/stop/restart, zones (editable per feed), risk state, detections, FPS, latency p50/p95, queue depth, dropped/stale counters and latest event. One shared model analyses all feeds (≈30 frames/s in total on the reference machine, shared equally). A feed that fails shows why and the others continue. Up to 3 active feeds are validated; 4 and 5 need an explicit acknowledgement («no validado»); 5 was RESOURCE_BLOCKED when measured. While feeds play, MP4 analysis, verification and webcam sessions are refused (409) because they share the single model.
## 22. Alerts (IMPLEMENTED, candidate)
«Eventos y alertas» lists alerts derived from deterministic kernel events: RED (high priority), YELLOW (warning, cooldown-merged), and «sin observar» diagnostics (never safe, never an emergency). Lifecycle Nueva → Activa → Reconocida → Resuelta, plus a separate delivery state (including «Entrega fallida»). Browser notifications and local sound are optional, need the user's consent in «Configuración», and their outcome is reported by the browser (denied / failed / delivered). Simulation and repeated-source alerts are labelled and never counted as genuine. Nothing leaves the machine; the webhook contract is documented and disabled.
## 23. Dashboards (IMPLEMENTED, candidate)
Operator: «Resumen» (feeds, risk, health, four separate counts, alerts, timeline, retention) and «Estadísticas» (alert trend by interval; an empty interval is reported as empty). Developer: «Desarrollador» (model, versions, scheduler, per-feed frame accounting, latency, queues, class distribution, tracks, measured resources, recorded acceptance state). No image, token or environment dump appears in either.
## 24. Additional API
See MG-VERTICAL2-001 §9. Webcam control messages are numbered and acknowledged (`X-MG-Msg-Seq`) and a late declaration after an unexpected finalization is a bounded sidecar (ADR-0006).
