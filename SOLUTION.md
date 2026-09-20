# Medication detection solution

The implementation uses one structured LLM extraction per saved note, validates
source spans locally, and resolves medication names against an indexed RxNorm
catalog. It now includes both exercise stretches: adjacent strength/form
highlighting and a more resilient catalog importer.

The separate final verification pass is complete: 141 backend tests, 22 frontend
tests, the production frontend build, both live sample-note analyses, migration
preservation, and bounded live RxNav checks passed. See [TESTING.md](TESTING.md)
for results, bugs fixed, and the remaining deployment and visual checks.

## Analysis pipeline

1. **Extract names.** The backend calls OpenAI with a strict JSON schema and
   validates the response with Pydantic. The default is `gpt-4.1-mini`, configurable
   through `LLM_MODEL`. The model returns literal names, occurrence numbers, and
   optional suggested names. It does not supply RxCUIs or authoritative drug data.
   Malformed response envelopes produce a handled provider error with a retry
   action, including null or non-object messages.
2. **Anchor source text.** Python locates exact, case-sensitive whole-name
   occurrences. Unknown source text is rejected. If a name occurs only once,
   its location is unambiguous even if the model numbers it incorrectly.
   Repeated names require a valid occurrence number. Duplicates and overlapping
   fragments are resolved before rendering.
3. **Resolve against the catalog.** Exact name, individual brand, and synonym
   maps handle direct matches. Explicit expansions cover HCTZ, APAP, ASA, and
   MTX. A trigram inverted index retrieves at most 64 aliases for fuzzy ranking.
   Similarity, edit-distance limits, and a margin between distinct concepts
   determine acceptance. Ambiguous results remain unresolved.
4. **Check missing concepts.** A bounded RxNav fallback performs exact searches
   for unresolved names or reviewed shorthand expansions. It accepts verified
   ingredient concepts, or brands/precise ingredients related to exactly one
   ingredient. Multiple candidate IDs, combination brands, unsupported term
   types, network failures, and exhausted budgets leave the mention unresolved.
5. **Extend the highlight.** A deterministic parser attaches adjacent, literal
   strength and form text on the same line. Matching always uses the drug name
   alone. The frontend renders the expanded source span and displays the details.

```mermaid
flowchart LR
    Note[Saved note] --> Extract[Structured LLM extraction]
    Extract --> Anchor[Validated source spans]
    Anchor --> Index[Indexed RxNorm matching]
    Index --> Details[Adjacent strength and form]
    Index -. Unresolved name .-> RxNav[Bounded exact RxNav lookup]
    RxNav --> Details
    Details --> UI[Highlights and medication details]
```

## Matching and scaling

The complete local catalog is indexed once per snapshot, including individual
CSV brand aliases. An analysis does not load the first API page as though it
were the entire catalog, and it does not scan every ingredient for each mention.
Expensive fuzzy comparisons operate on the retrieved shortlist.

Immutable snapshots contain plain data rather than attached SQLAlchemy objects.
Local ORM commits and bulk-import batches invalidate the cache. Lazy rebuilding
publishes a complete snapshot under a lock; a 60-second TTL bounds staleness from
other processes or external writers. The original catalog-browser lookup helper
is not used for per-mention matching.

Model-suggested names cannot override lexical evidence. Short unknown strings
are not fuzzily expanded. Similarity is not presented as a calibrated probability;
`confidence` remains unset. Distinct aliases for one RxCUI are not treated as
competing concepts.

| Written name | Match type | Correction |
| --- | --- | --- |
| metformin | exact | None |
| metformn | misspelling | metformn → metformin |
| Glucophage | brand | None |
| HCTZ | shorthand | None |
| Misspelled brand | misspelling | Written brand → correctly spelled brand |

The fallback has a per-analysis limit of 10 HTTP requests and an eight-second
request-admission deadline, with individual timeouts capped at three seconds.
An in-flight request may finish after the admission deadline. A bounded process
cache retains successful resolutions for one hour and confirmed misses for one
minute; transient failures are not cached as absence. Only medication names go
to RxNav, and this path does not modify the catalog. Set
`RXNAV_FALLBACK_ENABLED=false` to disable it.

See [RxNav exact-name search](https://lhncbc.nlm.nih.gov/RxNav/APIs/api-RxNorm.findRxcuiByString.html).
Approximate remote results are not automatically accepted as verified matches.

## Source spans, context, and dosage

Every returned highlight satisfies `note[start:end] == text`. The frontend uses
`Array.from(note)` so its slices follow Python Unicode code-point offsets.
`name_text` preserves the original drug name when `text` expands to include
strength or form. A correction therefore reads `metformn → metformin`, even
when the clickable highlight is `metformn 500 mg tablets`.

Strength/form recognition supports common units, decimals, concentrations,
parenthesized details, and forms such as tablets, capsules, inhalers, solutions,
and creams. Either strength or form may come first. It stops at another mention,
a line break, frequency instructions, or unrelated text. Unitless numbers are
not inferred to be strengths. Unresolved names can still show observed details.

Ingredient identity does not establish a specific product. `product_rxcui` and
`product_name` remain unset; exact SCD/SBD product resolution is future work.

The prompt includes supplements and held, stopped, negated, and historical drug
mentions. These are **medication mentions**, not an active prescription list.
Live checks exposed missed dosing-discussion references and incorrect global
occurrence numbering, so the prompt explicitly addresses both. A narrow local
rule excludes explicit ASA score/class phrases followed by a number or Roman
numeral. This is not a general solution to every ambiguous abbreviation.

## User interface

Saved notes are analyzed on load and after a successful save. The original note
remains readable during loading and failures. Highlights are keyboard-accessible
buttons that reveal RxCUI, normalized name, term type, brands, class, source,
match type, correction, and observed strength/form.

Unresolved mentions use a distinct style and explicit explanation. Errors show
a retry action instead of a misleading empty result. Responses are accepted only
for the requested visit and exact saved note; abort and lifecycle guards prevent
stale results from replacing current content. Keeping the analyzer mounted while
editing avoids another paid call when editing is canceled. StrictMode's abandoned
initial effect does not start a duplicate request.

Final regression testing also exposed stale save and catalog-search responses.
Navigating between visits now cancels the pending save and ignores its result,
while resetting the new visit's save state. Draft fields are disabled during
saving. New catalog searches cancel earlier requests so older results cannot
replace the current search.

## Catalog import resilience

- Names-first upserts preserve existing brand/class data. Missing, disabled, or
  failed enrichment cannot erase it. Partial brand results merge existing aliases;
  existing class assignments are retained conservatively when warnings occur.
- Import requests share a limiter paced at 15 requests per second. Transient
  transport errors, 429s, and server errors get at most three attempts. Retry-After
  seconds/dates are honored; excessive waits fail visibly instead of hanging.
- Empty or malformed ingredient payloads fail the job. Enrichment failures produce
  a completed job with warnings, whose count and first 20 messages are visible in
  the catalog UI. Successfully written ingredient batches remain available.
- An atomic PostgreSQL claim assigns an owner token and a renewable 120-second
  lease. A heartbeat runs every 20 seconds. Catalog batches lock and verify
  ownership in the same transaction as their writes. Stale owners cannot
  overwrite a replacement worker's status or catalog batches.
- Expired jobs appear interrupted and can be restarted. Restarting repeats an
  idempotent import; it does not resume from a saved checkpoint. Proper pacing
  means full class/brand enrichment may take several minutes.

Migration `005_import_leases_and_warnings.py` adds the ownership and warning
fields. Apply `alembic upgrade head` before running the updated application.
The production container already runs migrations at startup.

## Configuration and delivery

Backend settings come from `backend/.env` or service environment variables.
`frontend/.env` does not configure the backend. Keep database credentials and
LLM keys server-side. The defaults are `LLM_PROVIDER=openai`,
`LLM_MODEL=gpt-4.1-mini`, and enabled RxNav fallback.

The supplied deployment is https://visit-tracker-fv1z.onrender.com. Final read-only
checks returned HTTP 200 for the homepage, health, catalog, and OpenAPI schema;
the database is healthy and the catalog contains 14,689 concepts. Its schema
still lacks the new original-name and import-warning fields, confirming that
the latest local implementation is not deployed there. Publishing these changes,
running the startup migration, and testing the updated deployment remain pending.
A connected browser was unavailable, so visual verification also remains pending.

Remaining limits include exact product/combination representation, broader
context evaluation, resumable import checkpoints, and shared index invalidation
without a TTL. The short explanation video and optional agent transcript remain
submission deliverables; no video was recorded in this implementation pass.
