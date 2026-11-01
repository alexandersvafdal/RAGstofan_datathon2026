# Correctness harness progress — 1 November 2026

This branch is `codex/answer-integrity`. Our scope is answer correctness and latency regressions introduced by
these changes. A teammate owns broader speed optimization. The team targets answers in under 30 seconds.

## Completed local commits

- `d68da3f`: explicit outcomes, no forced table fallback or silent filter substitution, complete-answer checks.
- `2d2f76d`: structured question requirements, source metadata compatibility checks and audit records.

## Retry harness prepared for Spark preview

Bounded candidate retries are implemented: independent exclusions and cached rankings per
sub-question, source evidence checked before fetching numbers, fixed forced-table requests, attempt records.
The latest pre-integration run passed 96 offline tests. These use scripted models, not Spark timing measurements.
The retry default is still 3 (range 1–5). Using 1 pending latency measurement was recommended, not applied.

## Integration completed locally

The teammate's main branch advanced from `8c4ef5d` to `d95b9f9` and is merged in local commit `54ca75a`.
The single tiered finder, dictionary fast path, expanded keywords, published-rate routing, sums, period grouping,
category alternatives, language handling and LLM-server fixes are preserved. Metadata validation remains mandatory;
missing places/dates do not fall back to other values. Cross-table arithmetic has explicit operand roles and checks.
The retry draft and this checkpoint were restored and adapted to the unified finder. A pre-integration stash is
retained as backup. No live model or Spark timing claim follows from these offline checks.
Post-integration verification: 112 offline regression tests, the teammate's 16 ratio checks against the local
catalog, JavaScript syntax and conflict/whitespace checks. Integration tests cover dictionary routing without
bypassing validation, published rates, named-month recovery, sums/category chips and ordered cross-table arithmetic.

## Remaining progression

1. Continue development-set calibration of coverage and false refusals. The retry harness is published and the
   Spark preview is running. The user authorized committing, pushing and deployment on 1 November. Spark main
   has newer team changes and untracked artifacts, so the feature branch uses a separate Git worktree on port
   8002; the existing main app on port 8000 is preserved.
2. Assess added model calls and compare latency before/after on the same serving setup. Do not claim a speed
   improvement or a sub-30-second result from offline tests.
3. Extend the source definitions now read from local PX headers to live metadata and discovery profiles.
4. Strengthen cross-table population, geography, period, denominator and unit compatibility and source freshness.
5. Validate final prose against structured claims as well as numeric values.
6. Calibrate correctness, coverage, false refusals, clarification and latency with development questions; frozen
   and holdout sets remain measurement-only.

Edit code only on this computer. Spark testing must use code pulled from GitHub after it has been published;
do not copy unpublished local changes onto the Spark. Preview URL: `http://10.10.40.10:8002/`. Runtime smoke tests
and observed latency must be reported separately from the offline regression results.

## False-refusal calibration and live preview verification

Published runtime code through `acefe83` fixes observed false rejections without adding model calls:

- Misreported implicit defaults no longer reject a query when the executed codes match the resolver's policy.
  Explicit dates, places, categories, missing cells and incomplete operations remain protected.
- Named population sums are supported in the planner. Unnecessary splits merge only when the population
  definitions agree; every requested place still needs source verification.
- Selection prompts separate codes, labels and evidence IDs. Local PX headers supply source units,
  descriptions, survey names and footnotes; these are cached without loading numeric arrays.
- Unique selected official municipality names can establish geographic scope using the dictionary finder's
  existing word matching. A named national labour-force survey can establish Icelandic scope without a country
  dimension. The publisher name alone cannot establish scope; citizenship/birthplace cannot prove geography.
- Deterministic source evidence can correct the model's erroneous geographic veto. An unambiguous source
  title/selected measure label can supply missing measure evidence. A misnamed citation is rebound only when
  its exact source label uniquely identifies selected metadata, and the normalization is recorded in the audit.
  Invented text, ambiguous labels, unselected categories and contradictory definitions still fail.

126 offline regression tests pass locally and on the Spark. Final real-model smoke checks on the preview,
using `qwen3.6:35b-a3b` with thinking off, both passed their first candidate:

| Question | Result | Calls | Time |
| --- | --- | --- | --- |
| What is the unemployment rate? | Answered from VIN00001: 5.1%, September 2026 | 4 | 10.8 s |
| How many people live in Kópavogur and Hafnarfjörður together? | Answered from MAN02005: 40,286 + 32,398 = 72,684, 2026 | 5 | 24.8 s |

Earlier live checks confirmed clarification for “Hversu ríkt er gamalt fólk?” and out-of-scope handling for a poem
request. During calibration, false rejections triggered retries and some runs exceeded 30 seconds. The final
timings above are individual smoke samples, not a latency distribution or guarantee. Broader repeated development
evaluation, explicit-period availability classification, live-source freshness, cross-table definitions and final
prose validation remain on the progression timeline. Frozen/holdout questions have not been used for these fixes.
