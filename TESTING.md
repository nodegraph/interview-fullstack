# Testing summary

## Status at the implementation deadline

Final testing is intentionally deferred to a separate pass, as requested by the
user. The results below describe checks already completed during development;
they are not a claim that the final combined working tree has passed every check.
No final live-provider, migration, or deployment test was started after that
instruction. An importer test run already in progress finished afterward.

## Completed checks

| Check | Observed result | Scope and limits |
| --- | --- | --- |
| Earlier complete backend regression suite | 73 passed | Before the latest dosage, fallback, and importer additions |
| Dosage/form tests | 28 passed | Literal strengths, concentrations, forms, Unicode, parentheses, repeated names, boundaries, no inference from unitless/lab values |
| Dosage + RxNav fallback focused run | 41 passed | Includes 13 mocked HTTP fallback cases; no real external services |
| Transcript acceptance + fallback run | 15 passed | Actual sample files, mocked extraction, real matching and expanded spans |
| Importer + API run | 37 passed | 19 importer tests plus 18 API tests against isolated local Postgres; a small parser correction followed this run and is not rerun yet |
| Frontend tests | 19 passed | Three Vitest/Testing Library suites using jsdom and mocked fetch |
| Frontend production build | Passed | TypeScript and Vite; completed before the final-testing deferral |
| Earlier live OpenAI samples | 7/7 and 12/12 mentions | gpt-4.1-mini, correct RxCUIs/corrections; before the final dosage/fallback integration |
| Earlier live ambiguity example | Passed after fixes | Unicode, ASA score versus medication, APAP, and negated aspirin |
| Full ingredient matching check | Expected sample typos resolved | 14,689 live RxNav ingredients with seed brand aliases; full brand enrichment was not fetched |
| Existing Render deployment | Read-only checks passed | Homepage 200, DB healthy, catalog count 14,689; latest local code is not verified there |

These counts overlap. Do not add them together as a final suite total.

## Backend coverage

- Exact names, brands, synonyms, reviewed shorthand, misspellings, correct spelling
  display, competing concepts, short unknown strings, and repeated mentions.
- Strict provider output, invalid/invented spans, Unicode offsets, invalid
  occurrence numbers, unique-source recovery, score context, and provider errors.
- Index reuse, invalidation on committed catalog updates, rollback behavior, and
  protecting cached data from mutation by callers.
- Adjacent dosage/form expansion in either order, known units, concentration
  ratios, literal text preservation, no crossing lines or other drug mentions,
  and stable original-name corrections.
- Fallback exact-search parameters, verified ingredient properties, single-ingredient
  brand relationships, rejection of combinations and ambiguous IDs, cache behavior,
  timeout/outage degradation, request limits, and deadline exhaustion.
- Import preservation, malformed upstream results, retries, pacing, partial failure
  warnings, active/expired leases, ownership checks, and API response behavior.

## Frontend coverage

The tests exercise actual components, including keyboard detail selection,
name-only corrections for expanded highlights, brands without false corrections,
unresolved details, loading/empty/error/retry states, Unicode and repeated spans,
stale note/visit responses, request cancellation, StrictMode duplicate prevention,
edit/cancel reuse, successful-save reanalysis, failed-save draft retention, and
import warning display.

jsdom tests do not replace a visual browser check. No browser was available through
the connected computer-use tool during this pass.

## Commands for the separate verification pass

From the repository root, start local Postgres:

```bash
docker compose up -d
```

Run the complete backend suite against the dedicated test database:

```bash
cd backend
TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/visit_tracker_test .venv/bin/pytest -v
```

The fixtures create/migrate the test database. They do not use the app's configured
DATABASE_URL for test writes. RxNav fallback is disabled by default in tests;
fallback tests inject mocked HTTP responses. Two live-provider cases are skipped
unless explicitly enabled. Existing dependency deprecation warnings concern
Starlette/httpx test-client integration and the AnyIO BlockingPortal alias.

Run frontend checks:

```bash
cd frontend
npm ci
npm test
npm run build
```

An opt-in live check is now included for repeatability:

```bash
cd backend
RUN_LIVE_ANALYSIS=1 .venv/bin/pytest tests/test_live_analysis.py -v -s
```

This command sends only the two bundled exercise transcripts to OpenAI, uses the
configured key/model, fetches live RxNav ingredients, and creates a disposable
in-memory SQLite catalog with seed brand aliases. It exercises the real analysis
pipeline, including dose/form spans, without modifying application data. It incurs
provider usage and depends on network availability. The newly added opt-in tests
have not yet been run as part of this final implementation stage.

## Remaining checklist

1. Rerun the full backend and frontend suites on the final tree, including the
   importer's last parser correction and the new migration.
2. Run the opt-in live analysis cases and a targeted live fallback check for MTX,
   vitamin D, and folic acid with the small seed catalog.
3. Verify migration 005 against a disposable database upgraded from revision 004.
4. Perform an actual import with enrichment, then simulate interruption/restart and
   rate-limit failures. Automated tests cover these mechanisms; no full live import
   of the new implementation has been performed.
5. Publish/deploy the changes and confirm startup migrations, LLM service settings,
   health, and catalog preservation.
6. Open both notes in a browser: inspect every highlight, correction, dosage/form,
   keyboard interaction, edit/save/retry behavior, and slow-request navigation.
7. Record the short explanation video and include the deployed URL and repository
   link in the submission. Do not include credentials in the recording or transcript.
