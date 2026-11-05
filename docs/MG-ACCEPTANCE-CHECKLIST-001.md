# MG-ACCEPTANCE-CHECKLIST-001 — Owner acceptance checklist (MG-VERTICAL-02 candidate)

Status: PROPOSED (an instrument for the owner's decision; it grants nothing by itself). Date: 2026-10-25.
Scope: the product slice described in `MG-VERTICAL2-001-multi-source-monitoring.md`. Experimental decision-support tool; not a certified or safety-critical system. Nothing here is legal, licence or eligibility clearance.
Every step names the Spanish UI label exactly as it appears. Results are PASS / FAIL / BLOCKED (BLOCKED = the step could not be run and why; never PASS by assumption). Nothing in this checklist is automated against a camera: item 12 is run by a person.

## 0. Before starting
1. Close other heavy applications if you can (the model needs about 1.1 GB). MineGuard never needs, and this checklist never asks for, killing any process.
2. Optional: `python3 scripts/gate_preconditions.py` prints the approved start conditions of the *full gate* (pressure level 1, swap < 1 GB, free ≥ 60 %, …). They are NOT required for this manual checklist; they are required to run `scripts/acceptance_gate.sh`. Record the output as evidence of the machine state.
3. Launch: open a terminal in the project folder and run `scripts/run.sh` (loopback only, `http://127.0.0.1:8000`). Open that address in Chrome. Stop the server later with Ctrl-C. Do not run tests or the gate while the server is up (they refuse to overlap with it).
4. Take your own copy of the record: `mkdir -p logs/owner_acceptance && cp docs/MG-ACCEPTANCE-CHECKLIST-001.md logs/owner_acceptance/run_<date>.md` and write PASS / FAIL / BLOCKED and your notes in the *Result* lines (`logs/` is not versioned).
5. Existing data: your six Insta360 sessions are personal. Do NOT use them for the deletion tests (item 11); the four older synthetic sessions (origin «test_source», device label is a file path) are the intended targets, or sessions you create on purpose. MineGuard deletes nothing by itself.
6. To read a stored session's accounting: after loading the page once, open `http://127.0.0.1:8000/api/sessions/<session_id>` in a tab (the page's cookie authorizes read-only requests).

Evidence conventions: a screenshot per item named `A<n>_<what>.png`, the exact text of any message, and the ids shown (`feed_…`, `alt_…`, `session_…`).

## A1 · Overview and navigation
Steps: load the page; read the title (top left) and the chip next to it; click each item of the left navigation in turn: Resumen, Video MP4, Cámara, Multi-fuente, Sesiones, Eventos y alertas, Simulaciones, Estadísticas, Desarrollador, Configuración. Look at the top right and at «Estado del sistema» at the bottom of the left bar. Narrow the window: below about 1100 px the bar becomes icons, below about 820 px it hides behind the ☰ button.
Expected: ten items; the title says «MineGuard» with «Revisión de video asistida por IA» and the chip says «No es un sistema de seguridad certificado»; every area opens without an error banner; the badge «Alertas sin reconocer: N» is always visible; Resumen shows KPI tiles with real values only, genuine and simulated alerts apart, and the note that having no observed events does not prove safety; «Privacidad por defecto: ACTIVADA» appears under «Inventario de datos almacenados».
Evidence: one screenshot of Resumen; the number N of the badge. Result: ☐ PASS ☐ FAIL ☐ BLOCKED  Notes:

## A2 · MP4 playback, analysis and zones
Steps: Video MP4 (the registered clip is loaded automatically; optionally lower «Analizar 1 de cada N fotogramas» to a larger N for a faster run) → «Analizar con RF-DETR Nano» (wait until it finishes) → press «Reproducir» and «Pausar»; use «◀ Fotograma analizado anterior» / «Siguiente fotograma analizado ▶»; «Dibujar zona», click four points on the picture, «Cerrar polígono», «Guardar zonas»; read the risk panel; press «Verificar corrida».
Expected: boxes (vehicle) over the analysed frames; the zone appears with a risk badge (VERDE/DESCONOCIDO, never presented as «seguro»); after «Verificar corrida» the message «Verificación correcta (local)»; the origin line says it is real local inference; reopening the page leaves the run shown as «no verificada» until verified again.
Evidence: screenshot with zone and boxes; the verification message. Result: ☐ ☐ ☐

## A3 · Three-feed video monitoring
Steps: Multi-fuente → leave the defaults → «Crear 3 fuentes con el mismo video» → «Iniciar todas». Wait about 10 s.
Expected: three panels with a picture, the zone and boxes; the yellow banner «Varias fuentes reproducen el MISMO archivo… NO representan varias cámaras» and «FUENTE REPETIDA» in each panel; «Activas: 3 / 3 · validado en este equipo: 3»; per panel: state «Reproduciendo», FPS, «Latencia p50/p95», «Cola», counters; the risk badge VERDE or DESCONOCIDO (the clip has vehicles and no person: ROJO/AMARILLO are NOT expected from the real model).
Evidence: screenshot of the three panels; the aggregate «fotogramas analizados en total». Result: ☐ ☐ ☐

## A4 · Pause, stop, restart and independence
Steps: on feed 1 press «Pausar»; wait 5 s; compare the counters of feed 1 and feed 2; press «Reanudar»; on feed 3 press «Detener»; then «Reiniciar» on feed 3; press «Zonas» on feed 2, remove its zone («Eliminar»); look at feeds 1 and 3. Optionally tick «Entiendo que más de 3 fuentes NO está validado» (do not start a 4th feed unless you want to).
Expected: only the paused feed stops counting; stopping feed 3 leaves 1 and 2 playing; feed 3 restarts as a new run (counters from zero); the zone edit changes only feed 2; the capacity line never shows more than the allowed limit; nothing says a feed is safe.
Evidence: counters before/after the pause; screenshot after the zone edit. Result: ☐ ☐ ☐

## A5 · RED synthetic simulation
Steps: first make sure a real MP4 run exists (A2). Simulaciones → read the scenario → «Simulación ROJO — persona + vehículo» → read the state table → «Abrir en Video MP4 en el instante ROJO».
Expected: the laboratory stays in Simulaciones and shows the state progression (VERDE/DESCONOCIDO → AMARILLO → ROJO → VERDE), the events and the derived alerts, all labelled «SIMULACIÓN»; the MP4 area then opens on the scripted scenario with the badge «SIMULACIÓN» and the note that boxes and events are written by a script, not inferred; the page never calls it RF-DETR inference.
Evidence: screenshot with the SIMULACIÓN badge and the RED state. Result: ☐ ☐ ☐

## A6 · Alert creation, acknowledgment and resolution
Steps: after A5 open Eventos y alertas. Read the table; press «Detalle» on one alert (a side panel opens; Escape closes it) and read the three separate sections «Evento», «Alerta» and «Entrega»; press «Reconocer» on one alert; press «Resolver» on another; use «Ir al instante en Video MP4»; use the «Estado» filter.
Expected: simulation alerts (at least one ROJO and one AMARILLO) labelled «SIMULACIÓN»; initial status «Nueva» becoming «Activa» once displayed; after «Reconocer» → «Reconocida» with a time; after «Resolver» → «Resuelta»; the header badge does NOT turn red for simulations; the delivery column shows «navegador: … · sonido: …» honestly (pending until you enable them).
Evidence: screenshot of the table after both actions; the alert ids. Result: ☐ ☐ ☐

## A7 · Browser notification permission and optional sound
Steps: Configuración → read «Notificaciones del navegador» (both start OFF). Press «Activar notificaciones del navegador» and answer the browser prompt first with Block, check the table in Eventos y alertas, then (Chrome site settings) allow it and press the button again; press «Activar sonido de alerta» and «Probar sonido»; press the RED drill again (A5) and watch.
Expected: with Block: «Permiso del navegador: denied», alerts show «navegador: denied», the in-app alert still works and the state reads «Solo en la aplicación»; with Allow: a desktop notification appears with the title «MineGuard — ROJO… — SIMULACIÓN» (no image) and the alert shows «navegador: delivered»; sound plays only after you pressed the sound button; «Integraciones externas (webhook)» says DESACTIVADAS.
Evidence: screenshot of Configuración and of the delivery column; whether you heard the sound. Result: ☐ ☐ ☐

## A8 · Operator dashboard
Steps: Resumen (scroll to «Panel del operador»); Estadísticas.
Expected: «Fuentes activas»; a chip per feed with its risk and «SIN OBSERVAR» zones; the table «Cuatro cifras distintas (no se suman)»: detections per frame ≫ unique tracks, then risk events, then deliveries (stored / displayed / browser / sound); events from repeated sources are shown in their own row, not as real; the alert summary; the timeline; «Retención de sesiones de cámara»; Estadísticas shows the trend (or says there is no data) and «No disponible» for heat maps.
Evidence: screenshot; the four numbers. Result: ☐ ☐ ☐

## A9 · Developer dashboard
Steps: Desarrollador.
Expected: model id and weights hash prefix; «Motor de riesgo mg-risk-kernel/2.0.3», tracker 0.3.0, API de alertas `mg.alert-api/1`; scheduler line («one shared model, one worker…»); measured resources with their time; per feed: decoded / ofrecidos / analizados / descartados / errores, FPS, latency, queue, classes, tracks, discontinuities; «Estado de aceptación registrado» naming a commit; no token, no image.
Evidence: screenshot; the text of «Estado de aceptación registrado» (it may say that no execution is recorded for any commit) and of «Commit de la compilación en ejecución» (DESCONOCIDO is expected: the server does not record it; use `git rev-parse HEAD`). Result: ☐ ☐ ☐

## A10 · Privacy ON / OFF
Steps: open Cámara and look at «Privacidad de imágenes» WITHOUT pressing «Activar cámara» (this item only reads the selector; the camera is used only in item 12). «Privacidad ACTIVADA — no guardar imágenes de la cámara» must already be selected. Select «Privacidad DESACTIVADA…» and read the warning, then go back to ACTIVADA. Leave the tab and return.
Expected: ACTIVADA by default and again after returning; the OFF option shows «ATENCIÓN: se guardarán los fotogramas…»; the choice is fixed per session and is never remembered.
Evidence: screenshots of both states. Result: ☐ ☐ ☐

## A11 · Delete Images and Delete Session
Steps (use ONLY a synthetic session or one you created on purpose; see 0.5): Sesiones → filter by retention if needed → row of a session with retained images → «Eliminar imágenes» → read the dialog (count and data types) → type ELIMINAR → «Eliminar definitivamente». Then for another disposable session: «Eliminar» (or tick it and use «Eliminar seleccionadas») with ELIMINAR.
Expected: the dialog shows exact counts and says MP4 videos, model weights and review files are not touched; after «Eliminar imágenes» the row says «Imágenes eliminadas», the metadata remains and a request for an image answers 410 (open `/api/sessions/<id>/frames/10`); after deleting the session it disappears from the list; Cancelar deletes nothing; the other sessions and the MP4 runs remain.
Evidence: dialog screenshot; list before and after; the 410. Result: ☐ ☐ ☐

## A12 · Physical webcam acceptance (owner-controlled, separate)
Run only if you want to; nobody automates it. Close MP4 analysis and feeds first (they share the single model).
Steps: Cámara → keep «Privacidad ACTIVADA» → «Activar cámara» → allow the browser prompt → draw a zone («Dibujar zona»… «Cerrar polígono») → «Iniciar análisis» → move in and out of the zone for about 30 s → «Detener y guardar sesión». Then repeat once and CLOSE THE TAB in the middle of the capture; reopen the page → Sesiones.
Expected: live counters advance; the stored session says «No se guardó ninguna imagen…» and «Verificar» only checks metadata integrity; the origin text says the capture origin is declared by the browser and not verified as a physical camera; in `/api/sessions/<id>` the block `source.live_stats.accounting` shows `accepted`, `never_observed_frames`, `rejected_invalid_frames`, `tail_basis`, `closure`; the tab-closed session shows `closure: unexpected_disconnect` or `clean_stop` (a best-effort stop may arrive) with an honest tail; no image exists on disk for Privacy ON.
Evidence: the session id, the accounting block (copy the JSON), a statement of what was in front of the camera (do not attach images). Result: ☐ ☐ ☐

## Owner observations already recorded (OWNER_OBSERVED, 2026-10-26; not PASS records)
The owner reported inspecting the redesigned console manually: dark navigation and overview; three local feeds from one authorized MP4 with real local detector output; one feed paused (UNKNOWN, `last_observed_risk` shown apart), a second playing, a third stopped (UNKNOWN, `feed_closed`); the shared inference worker stayed available; Privacy ON visible. This is preliminary evidence for part of A1, A3 and A4 only. NOT yet observed or recorded: A2 (analysis and zone editing), A5, A6 acknowledge/resolve by hand, A7, A8, A9, A10 OFF path, A11, A12. Nothing below is marked PASS on the owner's behalf.

## Result summary (copy into your record)
| Item | Result | Evidence file / note |
|---|---|---|
| A1 Overview and navigation | | OWNER_OBSERVED (partial, 2026-10-26) — complete the rest |
| A2 MP4 playback, analysis and zones | | |
| A3 Three-feed video monitoring | | OWNER_OBSERVED (partial, 2026-10-26) |
| A4 Pause, stop, restart, independence | | OWNER_OBSERVED pause/stop only; restart not recorded |
| A5 RED synthetic simulation | | |
| A6 Alert lifecycle | | |
| A7 Browser notification and sound | | |
| A8 Operator dashboard | | |
| A9 Developer dashboard | | |
| A10 Privacy ON/OFF | | |
| A11 Delete Images / Delete Session | | |
| A12 Physical webcam | | |
Formal acceptance needs: all A1–A11 PASS, A12 PASS or an explicit owner waiver, the full gate (`scripts/acceptance_gate.sh`) passed under its approved start conditions, and the owner's own decision. A FAIL stops acceptance until the cause is fixed and the item is repeated.
