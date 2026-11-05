# MG-UNIFIED-004 — English locale (EN-LOCALE-01) and contribution traceability

Status: EXPERIMENTAL, PROPOSED. Not accepted by the owner. Decision-support prototype; not a safety system.
Date: 2026-11-02.

## 1. What this sprint implemented
A real English interface, selectable at run time, with Spanish unchanged. Frontend only: no backend, risk kernel, detector, Meta transport or persisted-value change.

| Piece | Where |
|---|---|
| Locale state, selection order, `<html lang>`, `useLocale()` | `frontend/src/locale.ts` |
| Catalogs and helpers `t`, `tf` (placeholders), `tEnum` (presentation of backend enumerations), `tIn` | `frontend/src/i18n.ts` |
| English: `i18n_en_a.ts`, `i18n_en_b.ts` (translation of the original catalog); Spanish text formerly hard-coded in components and its English: `i18n_x_c.ts`, `i18n_x_d.ts` | `frontend/src/` |
| Brand, language switch, provider-claims wording | `i18n_prov.ts`, `app/Shell.tsx`, `providers/ProvidersPanel.tsx` |
| English view of the simulated incident explanation (exact template mapping; unmatched sentences are shown as received) | `i18n_expl.ts`, `incidents/explanationPresent.ts` |
| Tests | `locale.test.ts`, `incidents/explanationPresent.test.ts`, `test_fixtures/explanation_samples.json` (generated read-only from `backend/mineguard/explain.py`), `test_setup.ts` (existing suites keep running in Spanish) |

**Selection order (deterministic):** `?lang=en|es` in the URL (for reproducible recordings; not stored) → the choice stored by the language switch (`localStorage` key `zonivra.locale`) → default **English**. The top bar has an `English | Español` switch (`data-testid` `locale-en` / `locale-es`, `aria-pressed`). Switching re-renders the tree without resetting component state. `<html lang>` and the tab title follow the active language; `index.html` ships as `lang="en"`.

**Rules kept:** identifiers, digests, model names, ids and stored/compared values are never translated; a backend value with no mapping is displayed as received; Spanish strings are byte-identical (the original 664-entry catalog is unchanged; text moved out of components keeps its original wording).

**Brand:** the English UI says "Zonivra"; the Spanish UI keeps "MineGuard" (unchanged Spanish). Owner decision needed if both should say Zonivra.

**Providers screen (English and Spanish):** states RF-DETR Nano as operational local inference (accuracy not validated); SAM 3.1 as an owner-reported external Playground test outside the app, with the in-app integration disabled and unverified; Muse explanations as simulated, external smoke tests not demonstrating in-app integration; the Meta transport as frozen with no active client; severity decided only by the deterministic engine. No "connected / verified live" wording exists in either catalog (tested).

## 2. Run and verify
```
cd frontend
npm run typecheck && npm test && npm run build
cd .. && MG_DEMO_PORT=8793 bash scripts/demo_v07.sh      # isolated storage; open http://127.0.0.1:8793/  (add ?lang=es for Spanish)
```
Manual check: language switch in the top bar; every area in the left menu; Settings → "Perception and explanation providers".

## 3. Known limits (untranslated or unchanged on purpose)
- Text produced by the backend and shown verbatim: alert/trend/acceptance notes, contract notes, risk cause codes, error details, deletion reasons, source origin text. The backend is read-only in this sprint. Any simulated-explanation sentence that does not match a known backend template is shown as received.
- Messages stored in component state at the moment they are set (e.g. a closed-session notice) keep the language that was active then.
- Default names created in English sessions are "Source N" / "Zone N" (user-editable; sent to the backend like any name). The browser-notification title follows the brand.
- Existing browser acceptance scripts under `e2e/` assert Spanish text and were not changed or run in this sprint; they need `?lang=es` support or an update before they are used again.
- The synthetic drill, incident drawer and explanation were exercised in English only through unit tests (real backend sentences), not in a browser: the drill needs a stored analysis run, i.e. RF-DETR inference.

## 4. Contribution traceability (Git evidence; not a legal or eligibility statement)
Dates are Git author dates, which can be set by the author: they are evidence, not independent proof. Whether any period counts as the hackathon "building period" is for the owner to check against the Participant Guide.

| Category | Evidence | State |
|---|---|---|
| Preexisting MineGuard (VS-01 and later product work, risk kernel, RF-DETR Nano adapter, MP4/camera/multi-source analysis, sessions, alerts) | `main` @ `7b02057`: 75 commits dated 2026-10-24 … 2026-10-29, first `7f9c916` "VS-01 checkpoint 1" | operational in this build; local inference verified per `docs/unified-evidence` |
| Unified integration (provider status panel, simulated explanation, release preparation, offline SAM tools) | `main..HEAD`: 25 commits, all dated 2026-11-01 (`UNIFIED-SPRINT-01`, `RECOVERY-02`, `RELEASE-PREP-03`) | read-only provider status and SIMULATED explanation operate; no external call possible |
| Meta integration experiments | commits `META-LIVE-04`, `META-RECOVERY-05`, `META-FINAL-06`, `META-LIVE-GATE-07` (2026-11-01); Gate 07 closed FAILED (reviews FAIL, 4 open defects) | **not accepted**; transport inert and frozen |
| Localization (this sprint) | the commit whose message starts `EN-LOCALE-01` on `integration/meta-local-first` (see `git log`) | implemented; acceptance candidate pending owner review |
| Demonstrated only externally | SAM 3.1 Playground test and Muse smoke tests, reported by the owner; **no repository evidence** | not part of the product; not verified here |

History is not rewritten; no date is inferred beyond what `git log` shows.
