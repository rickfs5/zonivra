# MG-MVP1-STABILIZATION-001 — MVP 1 stabilization and privacy acceptance (record)

Status: PROPOSED (evidence record; MVP 1 remains NOT accepted). Date: 2026-10-25.
Scope: owner order "MVP 1 STABILIZATION & PRIVACY ACCEPTANCE" after the night sprint. Reconciled baseline: HEAD `9b455794196adea61c07d224b7e080a4ade55c62`, application code `0eb3fd539005dd3e5e3dd790d89de24550623dee`, clean tree at the start. All work used synthetic data and isolated storage; no owner data was opened, copied, rendered or deleted. No Codex review was started. Evidence files: `logs/stabilization/` (git-ignored).

## 1. Privacy acceptance (synthetic, isolated storage only) — PASS
| # | Requirement | Result | Evidence |
|---|---|---|---|
| 1 | Privacy ON is the default | PASS | `test_privacy.py::test_default_is_privacy_on_and_no_image_is_ever_written` (session started without any privacy field is `metadata_only`); browser: "Privacy ON is the DEFAULT in the UI before any capture" |
| 2 | ON keeps no image or recoverable pixel data | PASS | same test scans the whole test storage for image extensions, image magic bytes and base64-JPEG markers (none, also while running); `test_real_model_privacy_on_detects_without_persisting_any_pixel` (real model) |
| 3 | OFF explicitly keeps images | PASS | `test_privacy_off_keeps_frames_evidence_and_deep_verification`; browser: OFF warning, fixed-mode indicator "RETENIDOS" |
| 4 | Delete images / delete sessions exercised | PASS | `test_session_deletion.py` (23), `test_night_review1/2.py`, browser deletion checks (dialog with count and data types, ELIMINAR required, cancel deletes nothing) |
| 5 | Confirmation, rollback, path safety, isolation | PASS | confirmation/count/shape; traversal and NUL ids; symlinked directory and inner symlink refused (target survives); look-alike directory refused; swap-after-pin refused; media set changed or directory substituted before the rename is refused AND restored; failed removal reported as failed; browser suites refuse to run without the isolated runner |
| 6 | Image endpoint fails after deletion | PASS | `GET /api/sessions/{id}/frames/{i}` returns 410 (`test_privacy.py:86,187`) |
| 7 | Metadata remains after delete images | PASS | `test_delete_media_removes_images_keeps_metadata_and_is_reported_honestly`; tombstone `media_deleted.json`; UI "Imágenes eliminadas" |
| 8 | Delete session removes its data and trust | PASS | `test_deleting_a_session_forgets_its_trust_and_leaves_no_reference`; the only per-session data lives inside the session directory |
| 9 | Owner's 10 sessions and 4 runs untouched | PASS | `lstat` fingerprint (name, mode, size, mtime, inode) of all 5 739 entries under `runs/` identical before/after; newest mtime in `runs/` is 05:45 on 2026-10-25, before this order (started 11:16); no `_tmp_` directory; 10 sessions, 4 runs |
| 10 | Inventory of MineGuard-managed copies (no image opened) | PASS | §6 |

Limit (unchanged): Privacy ON keeps metadata and a per-frame decoded-pixel SHA-256, which are not anonymous (MG-PRIVACY-001). A pass here is a statement about this code and synthetic data, not about copies made by the browser, the OS, backups or exports.

## 2. E2E instability ("timeout waiting for analysis started") — root cause UNVERIFIED
Observed once at N8 (11/12 checks), then 64/64 twice. Reproduction attempts in this phase: six consecutive isolated runs (port 8793, isolated storage, host at pressure level 2, 43–49 % free), all 65/65 (the suite gained one diagnostic check), 34–47 s each.
Candidate causes, assessed by reading the code path (the wait ends only when `POST /api/webcam/sessions` returns; `SessionManager.start` loads the model on first use and takes the inference lock without waiting):
- Test synchronization race on the button: excluded by reading the code (`cam-analyze` is never disabled; `dims` is set before `cam="on"`). Note that `click()` ignored its own return value; the test now records it.
- Test-server startup or identity error: the runner verifies health and listener identity before the suite starts, and the six runs show none; for the single failing run the server log was not kept, so it is NOT excluded for that run (UNVERIFIED).
- Application failure (409 `job_in_progress` / `session_in_progress`): would answer in milliseconds and show a Spanish error; not seen in six runs.
- Slow model initialization / host stall: the cold start-up measured 2.1–3.0 s (included in the request). The 15 s limit needs a ≥ 5× stall. The same night showed a 7.5× anomaly in another module (`rc1_closeout` 1 048 s vs ~140 s), which is consistent with a host stall but does not prove it.
Remaining hypothesis: transient host stall (memory compression at pressure level 2) during the first model load. The timeout was NOT increased. Change made: `e2e/mvp_acceptance.mjs` now records the click result, the HTTP status and duration of the session-start request and the UI error text on failure (`logs/mvp_e2e/analysis_start_failure.json`), so the next occurrence is attributable. This is a test-only change.

## 3. Review-5 findings against the final code
| Finding | Disposition | Basis |
|---|---|---|
| MG-WCAM-019 (major) | PARTIALLY ADDRESSED | heartbeat watermark makes the reaper record the tail up to the last heartbeat (`test_a_vanished_client_loss_is_recorded_from_the_heartbeat_watermark`); residual in §4 |
| MG-WCAM-020 (major) | FIXED, UNREVIEWED | age re-checked after inference; stale results become a drop and never enter tracker or kernel (`test_a_frame_that_became_stale_during_inference_is_dropped_not_analyzed`) |
| MG-ZONE-019 (major) | FIXED, UNREVIEWED | simulation default zone created under the shared artifact lock with a re-check (`test_simulation_default_zone_never_overwrites_a_concurrent_user_edit`) |
| MG-ZONE-020 (major) | FIXED, UNREVIEWED | non-NFC names refused before persistence (`test_zone_names_must_be_nfc_and_a_valid_name_keeps_risk_views_working`) |
| MG-VERSION-019 (minor) | FIXED, UNREVIEWED | overview built from `TRACKER_ID/VERSION`; no `2.0.0` or tracker `0.1.0` string remains in the current docs, backend or frontend (grep) |
| MG-EVIDENCE-019 (minor) | FIXED, re-verified | `pytest --collect-only` this phase: 577 tests in 20 modules, equal to the acceptance record |
Executed this phase after the N8 code (unchanged since): `test_night_review1…5` 8/12/9/6/6, `test_privacy` 14, `test_session_deletion` 23, `test_vs02_webcam_risk` 62, `test_vs02_replay_mp4` 9, `test_vs02_integrity` 31, `test_risk_kernel` 73, `test_risk_kernel_mutations` 29 — all passed (`logs/stabilization/light_modules.txt`). The heavy model-loading modules (`rc1_closeout`, `integration_real`, `rc1s_closeout`, cycle 7/8) were NOT re-run here.

## 4. Residual limits of MG-WCAM-019 (not documented before this record)
The browser reports its issued capture count about once per second. After the idle reaper finalizes a session (15 s without frames): (a) capture ticks issued after the last heartbeat are not recorded as an unknown sample; (b) a client that never sent a heartbeat leaves no tail record; (c) a browser stop that arrives after finalization is idempotent and returns the finished session, so its higher `X-MG-Next-Seq` is ignored. Consequence: for a vanished or stalled client, up to about one second of capture ticks at the end of a session may be missing without an UNKNOWN sample; the session is closed as right-censored (`client_disconnected`), so no risk state is claimed after the last accepted frame. A complete fix would need the server to refuse a finalization it cannot account for, or to accept a late declaration on a finalized session as an addendum; either is a design change and is left to a reviewed decision.

## 5. Independent review readiness — BLOCKED (not executed)
- Wrapper resolution and the no-model smoke test are run by `logs/night_review/cycle_preflight.py` (results in the final report of this order). The staging brief is `logs/night_review/staging/cn6/evidence/REVIEW_BRIEF.md`.
- Preflight executed (label `n6`, review 20, against `9b45579`, no model call): PASS for wrapper existence/regular file/not a symlink/not the raw binary, Python shebang, PATH resolution to the wrapper (`logs/night_review/bin/codex`; raw binary `~/.codex/packages/standalone/releases/0.160.0-aarch64-apple-darwin/bin/codex`), controller lock free, no Codex process, git fsck, previous evidence preserved (1 595 files), no-model wrapper smoke test (DRYRUN ok, 102 files as sent, no excluded material). FAIL: clean tree, controller capacity (19/19), and staging set incomplete (no `TEST_EVIDENCE.txt` yet). Controller state unchanged.
- BLOCKED by: (1) the controller is at 19/19 cycles; a sixth review needs an owner authorization that raises the cap (`controller.py authorize`), and previous cycle records must not be reset; (2) the preflight requires a clean tree at a named commit, so the stabilization changes (this document, the E2E diagnostic) need a local checkpoint commit first; the evidence must then be regenerated after that commit.

## 6. Inventory of MineGuard-managed image copies (names, counts and sizes only; no image opened)
- `runs/session_*/frames`: 10 sessions, 2 020 files, 148 MB (6 owner sessions with Insta360 devices: 1 984 files; 4 synthetic: 36 files).
- `runs/run_*/frames` (MP4 analyses of the registered clip, not personal): 4 runs, 1 732 files, 428 MB; the clip itself is `test-assets/*.mp4` (69 MB).
- Archive copies under `logs/*/archive*/runs/run_2c69dd64d05481e0/frames` (248 files, 61 MB each, 6 copies): MP4-run frames only.
- `logs/mvp_e2e` (screenshots of the synthetic E2E and `fake_camera.y4m`, derived from the clip) and `logs/acceptance` (earlier gate outputs).
- No `session_*` directory exists outside `runs/` (no copy in `logs/` or `runs/review`); no image-like file is tracked by Git; `runs/` and `logs/` are git-ignored.
- Not inventoried (outside the project, cannot be removed by MineGuard): browser caches, OS snapshots, Time Machine, exports saved by the owner.

## 7. Full acceptance — RESOURCE_BLOCKED
Host at the time of writing: memory pressure level 2, 43–49 % free, swap 78 MB of 1 024 MB, memory compressor about 10.3 GB of 24 GB, no MineGuard, ffmpeg, uvicorn or Codex process running; disk 120 GB free. The start conditions applied since the recovery stages (pressure level 1, swap < 1 GB, free ≥ 60 % in `run_stageB2.sh`; ≥ 50 % in the sprint decision) are not met. The in-script hard aborts (`acceptance_gate.sh` 25 % free, `guarded_run.py` 15 % free or +20 GB swap) are looser and would not trigger; they are abort guards, not start conditions, and were not used to justify a start. No thresholds were changed, no process was stopped, and the gate was not attempted.

## 8. Owner instruction: delete personal frames safely
See the final report of this order and MG-PRIVACY-001 §4. Short form: restart the server with the current code (`scripts/run.sh`), open **Sesiones**, tick the six sessions whose device is "Insta360…", press **Eliminar imágenes** (keeps metadata; the per-session count and bytes are shown; type ELIMINAR) or **Eliminar seleccionadas** (removes the whole session). MineGuard deletes nothing by itself.
