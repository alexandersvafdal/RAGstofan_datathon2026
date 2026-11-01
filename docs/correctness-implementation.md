# Correctness implementation and evaluation boundary

The remaining engineering milestones are implemented on `codex/answer-integrity`. The dictionary, tiered finder,
table references and explicit outcomes remain the production path. Evaluation is separate: scripted tests do not
establish serving-model accuracy, false-refusal rates or latency percentiles.

## Source definitions and discovery

Local PX headers expose units, contents, source, update time, table notes, variable notes and value-specific notes.
Notes stay attached to their options: an unselected category's footnote cannot certify the executed query. Snapshot
metadata invalidates when files change; reading headers does not load numeric arrays.

`python -m scripts.build_source_profiles --source local` builds profiles from the release. Use `--source live` for
live definitions, optionally `--ids A,B`. The live warmer uses a one-cell PX export and discards its numeric cell.
This is offline provisioning, outside question handling. Methodological references are retained as published;
linked external documents are not crawled on each question. Profiles contain source material, not generated prose.

Profiles add a source-text BM25 field and short definitions on candidate cards. Dictionary priority/fast routing
is preserved. Live enrichment must match identity, schema, language, update information when supplied and
`METADATA_TTL_S`; snapshot definitions cannot certify live figures. Rebuild live profiles periodically or before
evaluation. Expired definitions become missing evidence instead of silently persisting.

## Freshness and request bounds

`FRESHNESS_POLICY=snapshot` preserves the release, disclosed through `SNAPSHOT_AT`. `FRESHNESS_POLICY=live` uses the
API for metadata and cells. `DATA_SOURCE=pxapi` always uses the API. GET caches expire after `METADATA_TTL_S` (3600),
numeric POST caches after `DATA_TTL_S` (300). Failed refreshes never serve expired evidence; writes are atomic.
These are bounded caches, not a claim that data includes an update published this instant.

Checked queries compare their source metadata version with what selection verified. Observed metadata/data
revision changes yield `unverified`. Actual source periods replace catalog hints in references. Source origin,
snapshot date and metadata checked-at time travel through the API and MCP adapter.

`ANSWER_TIMEOUT_S` defaults to 30 for the whole question, including retries. Timeout cancels coroutines and emits
`error/answer_timeout` without a partial answer. Blocking search runs outside the event loop. Already running
worker threads cannot be forcibly terminated: this is a response budget, not a guarantee of successful answers
under 30 seconds. `QUERY_CELL_LIMIT` defaults to 100000; excessive selections ask for narrower filters before data
fetching. No additional LLM validation round trips are introduced.

## Cross-table calculations

Executed `StatisticalContext` records bind measure, definitions, population, geography, periods, temporal reference
basis, unit/scale and explicit axes. Shares/differences/percentage changes require compatible measure, statistic,
adjustment, price/tax basis, unit/scale, frequency, reference basis and source vintage. Different latest/default
periods cannot be treated as aligned. Explicit comparisons preserve requested differences.

Shares need matching periods and population containment. Unproven cross-table geographic containment remains
`unverified`; a table containing both categories and its total is preferred. Population ratios require a persons
denominator, the correct place, unfiltered denominator population and a published 1 January reference. Implicit
year mismatches fail; explicitly dated numerator/denominator years may differ. Source scales are converted before
division, e.g. million ISK / thousand persons becomes base ISK/person. Multiple places or unsupported denominator
qualifications require clarification instead of selecting the first place or silently using a total.

Unknown definitions/references do not acquire compatibility because both operands are numbers. These conservative
boundaries need coverage and false-refusal measurement.

## Final claims

Raw facts carry source IDs and periods; calculated facts reference their operand indices. `Answer.claims` binds
displayed values to their label, unit, period, source and calculation basis. Every part/category is represented.
Series summaries cover each full series with endpoints and a computed peak; full data remain in facts/rows.

Final text is rendered from these records. A free-form sentence is not certified by finding its numbers somewhere
in the result. The writer-model call is removed. Forecast titles and snapshot dates produce disclosures. Tampered
claims, missing operands and dropped parts fail validation. Technical failures clear prepared claims as well as
parts/sources. MCP preserves claims/contexts and drops numeric content on non-answered outcomes.

## Evaluation after implementation

Provision profiles for the chosen source policy, restart only the feature preview and run repeated development
questions. Record outcomes, tables, values and unit/period/definition-bound facts where gold labels are available.
`eval.integrity_eval` reports coverage, precision, false refusals, error reasons, retries, calls and latency. A fast
refusal is not a correct fast answer. Frozen/holdout sets remain measurement-only; the organizer set has already
informed fixes and is not blind. Further calibration must use development cases, followed by final measurement.

Interpretation still depends on the planner and available source definitions. Implementing this plan does not
prove universal semantic correctness; genuinely unavailable methodological information remains unknown.

Protocol references: [Statistics Sweden PX tools](https://www.scb.se/px-en) and
[PxWeb API documentation](https://www.scb.se/globalassets/vara-tjanster/px-programmen/pxwebapi2022.pdf).
