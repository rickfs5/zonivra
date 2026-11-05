# MG-UNIFIED-008 — META-REAL-SSE-12: observed SSE profile and import of the first genuine owner-executed SAM 3.1 result

Historical builder report for commit `de8a08afb8286327b446a91b26178932e4f0f81a`. A later bounded independent review returned **INDEPENDENT_PASS** for this one observed response profile and its offline import/review workflow. That verdict is not `IN_APP_LIVE_META_VERIFIED`: the response was produced outside this application by the owner's manual sender; Zonivra only imported and reviewed it offline, in isolation. Private response bytes and authorization material are deliberately not distributed.

## 1. Source and commits
Starting HEAD `bd693c7e91491f955db91d57df59e9fd01c23592` (branch `integration/meta-local-first`; accepted by Codex review `meta-import-10-r1-review`). Final commit: the git log entry titled `MG-META-REAL-SSE-12` (a document cannot contain its own hash). Nothing pushed.

## 2. Immutable source evidence (read-only, outside Git)
| Item | SHA-256 | Note |
|---|---|---|
| Response (attempt `f892c336-…227a`) | `698b0b63…3036` | 165441 B, HTTP 200, `VERIFIED_STREAM`, not truncated |
| Source image `synthetic_truck_01.png` | `721c3589…2e1d` | 640×384 PNG |
| Request body | `5bdee92f…17f5` | declared, not recomputed by the importer |
| Sender | `4461e1a0…ff76cb` | unchanged |
Re-hashed before the work and after all tests: identical. The response is private local evidence and is **not** in this repository; the exact-response test runs only where the file exists (override with `MG_REAL_SSE_RESPONSE`, `MG_REAL_SSE_IMAGE`) and is skipped otherwise.

## 3. Observed SSE profile `META_SAM31_OBSERVED_1`
Status string: `OBSERVED_FROM_ONE_OWNER_EXECUTED_META_RESPONSE`. It is the shape of **one** response. It is not Meta's official schema and says nothing about other responses. It is selected only by a terminal `data: [DONE]` line; without that line the previous fixture grammar (`FIXTURE_V1`) applies unchanged, so the profile is never inferred from event names. A profile applies whole or not at all.

Wire: LF framing; nine `event:`+`data:` JSON blocks, then exactly one final block `data: [DONE]` (no `event:`), nothing after it.

Exact order and top-level members (sequence numbers must be exactly 0…8):
| # | event | members |
|---|---|---|
| 0 | response.created | response, sequence_number, type |
| 1 | response.in_progress | same |
| 2 | response.output_item.added | item, output_index, sequence_number, type |
| 3 | response.content_part.added | content_index, item_id, output_index, part, sequence_number, type |
| 4 | response.output_text.delta (exactly one) | content_index, delta, item_id, logprobs, output_index, sequence_number, type |
| 5 | response.output_text.done | content_index, item_id, logprobs, output_index, sequence_number, text, type |
| 6 | response.content_part.done | content_index, item_id, output_index, part, sequence_number, type |
| 7 | response.output_item.done | item, output_index, sequence_number, type |
| 8 | response.completed | response, sequence_number, type |

Nested schemas (exact key sets, exact values where observed constant):
- **response**: background=false, created_at (number), error/incomplete_details/instructions=null, id=`resp_[A-Za-z0-9_-]{8,64}`, model=`sam-3.1`, object=`response`, output (`[]`, or exactly the completed message), parallel_tool_calls/store (bool), service_tier=`auto`, status (`in_progress`/`completed`), temperature/top_p (numbers), tool_choice=`auto`, tools=`[]`, top_logprobs=0, truncation=`disabled`. Completed adds `completed_at` (≥ created_at) and `usage` {images_processed, input_tokens, input_tokens_details{cached_tokens}, output_tokens, output_tokens_details{reasoning_tokens}, total_tokens}: integers 0…10⁹.
- **item**: {content[1 part], id=`msg_[A-Za-z0-9_-]{8,64}`, role=`assistant`, status, type=`message`}.
- **part**: {annotations=[], logprobs=[], text, type=`output_text`}.
- Indices `output_index`=`content_index`=0; `logprobs`=[].
Cross-checks: all texts (delta, done.text, both parts, item, completed output) are identical; one message id everywhere; one response id and `created_at` across created/in_progress/completed; `completed.output[0]` equals `output_item.done.item`.
Record line: the single delta is the documented `<0f>` record grammar (unchanged) plus **one** observed terminator: it must end with `|>` + exactly one LF and contain no other LF/CR. The review parses the validated line; the stored bytes are never altered. Boxes are verified inside the image and never clipped; an absent `c=` stays `not_reported`.

## 4. Differences from the fixture grammar (`FIXTURE_V1`, unchanged)
Fixture: 3 events with minimal members, no sentinel, no terminator. Observed: 9 events, nested objects, sentinel, one terminator LF. Fixture records keep exactly the keys they always had (existing stored imports re-derive to the same digest). Observed records add `grammar` {profile, status, official_provider_schema=false, terminal_sentinel, events, provider_usage (ints only)}, the check `observed_profile_schema`, and profile-specific assumptions/limitations. Provider ids and the mask payload are never copied into the review.

## 5. Strict rejections (new codes; all fail closed, nothing stored)
`result_done_sentinel_misplaced` (duplicate, not last, with `event:`, CRLF, unterminated, content after), `result_unknown_event_type`, `result_unknown_event_field` (top level), `result_observed_schema_violation` (any nested key/value/size outside the profile, mis-typed values, oversize), `result_observed_order_invalid`, `result_observed_sequence_invalid` (non-consecutive, non-integer), `result_observed_text_mismatch`, `result_observed_identity_inconsistent`, `result_observed_record_terminator_invalid`. Existing refusals (`result_provider_failure_event`, box/frame/mask rules, hash/size/image checks, synthetic relabelling) are unchanged. EN/ES texts added. The C5 counterexamples (extra members, `attacker.control`) remain refused with and without a sentinel.

## 6. Unsupported / unknown (refused, not guessed)
Zero deltas or several deltas; other lifecycle events (`response.incomplete`, refusal/reasoning/annotation events); non-empty `tools`, `annotations`, `logprobs`, `error`; usage with extra counters; non-`<0f>` frames; video; missing `[DONE]`; any second sentinel; mask pixel decoding. Whether a different SAM response (several objects, no match, other image sizes) follows this profile is **unknown**: it needs its own evidence.

## 7. Tests (offline; exit codes 0)
| Command | Result |
|---|---|
| `pytest backend/tests/test_external_import.py backend/tests/test_meta_invariants.py` | 226 passed, 0 skipped (the exact-response test ran) |
| relevant backend subset, 18 files | 843 passed, 1 warning |
| `npx vitest run` / `npm run typecheck` / `npm run build` | 148 passed / OK / OK |
| `bash e2e/external_import_acceptance.sh` (existing, synthetic) | 28 PASS, 0 FAIL |
| isolated real-response browser import (outside Git, scratch) | 19 PASS, 0 FAIL |
| `scripts/acceptance_counts.py check` after `generate` | `external_import` 110 → 216, total 1872 → 1978 |
Added: 106 tests (no existing test changed or removed). Six negative mutations, each caught: nested key sets not enforced; sequence not checked; done.text mismatch accepted; duplicate/misplaced sentinel accepted; terminator rule removed; event order not enforced.

## 8. Real import trial
Original response + original image + manifest (`DECLARED_EXTERNAL_API_RESPONSE`, request digest declared, concept `truck`, captured time = sender's `finished_utc`) imported through the UI file form into isolated empty storage: **accepted**. Stored `result.bin` hash = `698b0b63…3036`. State `HASHES_VERIFIED_AUTHENTICITY_NOT_PROVEN`; origin `EXTERNAL_MANUAL_PROVIDER_RESULT`; image linkage verified from the image bytes; one segment, box (40,150)–(559,331) inside 640×384, `UNCLASSIFIED`, score `not reported`, mask 182×520 structure-checked **not rendered**; `risk.assessed=false`. The genuine stream without `[DONE]`, and an altered genuine stream, were refused with nothing stored. Product `runs/` and the providers status unchanged.

## 9. What this demonstrates and does not
Demonstrates: the owner's one SAM 3.1 call returned a complete, internally consistent stream whose single box encloses the truck on the synthetic image (screenshots in the scratch workspace, not in Git), and that Zonivra can import it under a strict, versioned, fail-closed profile. Does not demonstrate: provider authenticity (no signature exists; the request digest is declared), segmentation quality or accuracy (one synthetic image), mask correctness (undocumented raster semantics, never rendered), object class, risk, in-app inference, or behaviour on other responses.

## 10. Provenance, mask limits, risk authority, security boundaries
Provenance: owner-declared manual provider execution; hashes prove byte integrity only; structural validation is not proof of Meta's identity (the UI says so in EN/ES, row "Stream validation"). Integrity (`integrity`, scope `STORED_BYTES_REVALIDATED_NOT_AUTHENTICITY`), stream validation, declared provenance and unproven authenticity are four separate lines. Masks: structure only, never rendered. Risk: no severity, class or confidence is invented; imported evidence never reaches the Risk Kernel or RF-DETR observations. Boundaries: no network, credentials, spend, RF-DETR inference or transport; diff vs `bd693c7` is empty for `metahttp`, `metaclient`, `costgov`, `sam31`, `meta_obs`, `meta_pipeline`, `providers`, `detector`, `risk/`.

## 11. Changed files
`backend/mineguard/meta_import.py`, `backend/tests/test_external_import.py`, `frontend/src/{api.ts,i18n_ext.ts,external/ExternalResultsPanel.tsx}` (schema row and refusal texts), regenerated `docs/MG-MVP1-ACCEPTANCE-001.md` + `docs/MG-ACCEPTANCE-STATE.json`, this document. `api_external.py` needed no change.

## 12. Independent reproduction
From the product repo: `git diff bd693c7 --stat`; `.venv/bin/python -m pytest -p no:cacheprovider backend/tests/test_external_import.py backend/tests/test_meta_invariants.py` (expect 226 passed with the private response present, 1 skipped without); `cd frontend && npx vitest run && npm run typecheck && npm run build`; `bash e2e/external_import_acceptance.sh`; protected-file diff empty. Rehash the three source files against §2. For the real import, run the scratch `real_import.sh` (or hand-build the manifest as in §8) against isolated storage only; never against `runs/`.
