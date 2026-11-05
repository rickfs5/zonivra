# MG-PRODUCT-VERTICAL-07 — Complete operator experience (source lifecycle, full incident timeline, demonstration)

Status: IMPLEMENTED as a local-first, experimental increment; NOT independently accepted, NOT production-ready, NOT a safety system. Evidence and review state are in section 8.
Date: 2026-10-29 (baseline `be205c8`).

MineGuard remains a decision-support review tool. Nothing here measures physical distance, predicts collisions or certifies safety. Evidence labels follow `docs/README.md`.

## 1. What was added

| Area | CONFIRMED behaviour (tests and browser journey) |
|---|---|
| Recorded source lifecycle | Each MP4 feed run keeps a bounded, ordered ledger of what THIS application did and saw: `source_started`, `source_paused`, `source_resumed`, `source_stopped`, `source_ended` (end of file), `source_failed` (with the error class). Processing time is UTC with millisecond resolution. |
| Recorded observation edges | `observation_received` (first analysed sample), `person_detected` / `vehicle_detected` (the first analysed sample in which the class appears after being absent; frame-wide, not zone-specific) and `risk_changed` per zone (including to/from UNKNOWN). They carry the SOURCE time of the sample the application really analysed. Nothing is recorded between two samples. |
| Incident timeline | The V06 two-track timeline now merges the recorded facts of the exact run behind the alert: observation track (source time: `risk_entry`, `risk_changed`, `person_detected`, …, `unknown_interval`, `episode_closed`) and processing track (UTC: `source_*`, `alert_created`, delivery, `alert_acknowledged`, `alert_resolved`). Recorded entries are tagged REGISTRADO. |
| Not recorded | Scripted simulations, restored records, runs from before this build, runs of another process, idle feeds: the incident says `timeline.lifecycle.recorded=false` with `mg.inc.life.not_recorded` and lists the unsupported kinds. Nothing is reconstructed from alert timestamps. |
| Completeness block | `completeness`: source lifecycle / observation edges recorded or not, UNKNOWN intervals available or not, stored-run consistency, images `excluded_by_design`, and a single `complete` flag (true only when this build recorded the source lifecycle of that run and the ledger was not truncated; a scripted simulation or a restored record is therefore never `complete`). Shown in the panel and in the export. |
| Source card | A collapsible "Registro de la fuente" on every feed card (lazy; `GET /api/feeds/{id}/lifecycle`) and a "Ciclo de vida" row on each Resumen tile (last recorded lifecycle fact and time, or "no registrado"). |
| Export | `mg.incident.export/1` now carries a `projection` envelope: generated projection (not the original record), `is_original_record=false`, `authenticity=not_independently_verified`, and the open finding ids. The incident inside it is the same projection the panel shows. No image or footage. |

## 2. Honesty rules

1. A lifecycle fact exists only if the feed recorded it while it happened. Old history is never invented; a restart is a NEW run with its own ledger (`run_uid`), and an old alert never borrows the new run's ledger (finished runs are kept in memory, bounded to 50, not across a server restart).
2. Alert stamps have second resolution; lifecycle stamps have millisecond resolution. Across seconds the order is exact; inside one second the order is causal (open → alert created → delivery → acknowledged → resolved → close) and the panel says so (`order_within_a_second: causal_not_measured`).
3. UNKNOWN is its own state. A risk change to UNKNOWN is recorded as UNKNOWN, never GREEN.
4. The caps are separate (100 lifecycle facts, 200 observation facts per run): a long run can never push the stop/failure record out; truncation is declared.
5. A real model analysis is labelled model inference; the YELLOW → RED lab scenario stays SINTÉTICO. The authorised MP4 produced NO RED in the demonstration; no RED is faked from it.

## 3. Demonstration (test-owned server and storage)

Launch (isolated server and storage on a free port; never the owner's live server):

```
MG_E2E_PORT=8771 bash scripts/run_mvp_e2e.sh e2e/product07_acceptance.mjs
```

This starts a temporary server at `http://127.0.0.1:8771`, drives the real UI in headless Chrome, writes screenshots to `logs/product_vertical07/` and removes the server and storage afterwards. The nonpersonal fixture is the registered clip in `test-assets/`.

Interactive demonstration (one isolated server and storage kept running until Ctrl-C; never the owner's data or live server):

```
MG_DEMO_PORT=8780 bash scripts/demo_v07.sh      # prints http://127.0.0.1:8780 once healthy; Ctrl-C stops it and removes the storage
```

Manual path in that server:

1. Resumen: no sources yet (no invented tiles).
2. Multi-fuente → add a source (the registered MP4) → its "Registro de la fuente" says "no registrado" before it starts.
3. Start it: real RF-DETR analysis; the card list shows `source_started`, then `observation_received`, and the person/vehicle appearances the model really saw.
4. Pause / resume: both recorded; the Resumen tile shows the last lifecycle fact.
5. Open the zone editor: the zones are those the API holds. The risk badge equals the deterministic engine's state.
6. Add a second source on the same file: both tiles say ACTIVE overlap simulation (not two cameras). Stop one: only that source records the stop; the other keeps playing. Start it again: a NEW run with its own ledger.
7. Analyse the MP4 (feeds stopped, single model) to create the stored run the lab reads; restart the feed.
8. Simulaciones → "Simulación ROJO": the labelled SINTÉTICO YELLOW → RED scenario; open the alert.
9. Incident investigation: rule and "AI never decides", observed facts vs derived conclusions, two-track timeline with the "lifecycle NOT recorded" banner (scripted run), UNKNOWN intervals, completeness, evidence and provenance panels.
10. "Ir a la fuente" → source view with "volver al incidente" → the SAME incident reopens (one drawer, no duplicate).
11. Acknowledge (keyboard works), resolve; the delivery block stays separate.
12. "Exportar JSON del incidente": the file equals the server projection (except `generated_utc`) and what the screen shows.
13. "Volver al monitoreo": other sources keep their own state.
14. Privacidad ACTIVADA, no camera stream, deletion disabled.

REAL vs SYNTHETIC: only steps 3–7 involve the real model (the MP4); step 8 onward uses the scripted scenario and is labelled so everywhere. The personal webcam is never started.

Screenshots (`logs/product_vertical07/`, regenerated by every run): `v07_p01_overview_two_sources.png`, `v07_p02_incident_investigation.png`, `v07_p03_incident_completeness_evidence.png`, `v07_p04_incident_resolved.png`, `v07_p05_overview_after_incident.png`, `v07_p06_overview_{tablet820,mobile420}.png`, `v07_p07_incident_{tablet820,mobile420}.png`.

## 4. Defect fixed on the way

The V06 incident panel reused the CSS class `timeline` for its lists, which collided with the viewer's 16 px bar rule (`height:16px; overflow:hidden`): the investigation timeline was clipped to about one row. The lists now use `tl-list`. Found by inspecting the V07 screenshots; the V06 browser suite did not assert row visibility.

## 5. Known limits (not hidden)

- The recorded lifecycle lives in the process (bounded; finished runs are kept in memory): after a server restart an old alert shows "not recorded". It is deliberately NOT written into the saved feed summary (`mg.feedrun.v1`), whose format is a reviewed contract validated strictly; persisting it would be a separate, reviewed change.
- The real MP4 produced no feed alert in the browser journey, so the recorded-lifecycle incident path is exercised by backend tests with a scripted detector double, not by the browser suite (the suite says so as a NOTE).
- Presence edges are frame-wide, not zone-specific.
- RF-DETR accuracy is NOT validated. Local digests are consistency aids, not authenticity.

## 6. Historical open findings (unchanged)

MG-RISK-036 (coordinated alteration of locally writable run files is not authenticated) and MG-WCAM-035 (webcam document schema/producer accounting) remain OPEN. The new features do not claim to resolve them; the export names them. Deletion stays disabled (MG-DELETE-035/036).

## 7. Tests

`backend/tests/test_product_vertical07.py` (lifecycle ledger, caps, failure isolation, restart, both tracks, ordering, UNKNOWN, not-recorded, export correspondence), `e2e/product07_acceptance.mjs` (complete journey, 1500/820/420 px, keyboard), five V07 mutants in `scripts/mutation_check_p06.py`.

## 8. Evidence and review state

Collected on the working tree content committed as the V07 checkpoint, on isolated servers and storage (port 8771), host free memory ~44% and swap above 1 GB (so the full acceptance gate is BLOCKED_RESOURCE and was NOT run; manual checks A1-A12 are pending; OAF stays DRAFT / OWNER_DECISION_REQUIRED):

| Check | Result |
|---|---|
| Backend + OAF (`pytest backend/tests oaf/tests`) | 1261 passed, rc 0 |
| Frontend `tsc` / Vitest / build | rc 0 / 87 passed / OK |
| Browser: MVP, MP4, multi-source, console, journey, V06, V07 | 63/63, 34/34, 44/44, 74/74, 43/43, 27/27, 41/41; all rc 0 |
| Mutation harness (16 V06 + 5 V07) | 21/21 killed, clean control passed, rc 0 |

Independent review of this increment: see `logs/v07/EVIDENCE.txt` (status is recorded there and in the final report; the Builder never claims a PASS). Earlier Codex 35 (attempt A36, reviewed `004b9ff`): FAIL 0 blocker / 5 major / 1 minor, preserved verbatim; the four post-review corrections of V06 remain UNREVIEWED; MG-RISK-036 and MG-WCAM-035 remain OPEN.
