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

1. Publish the reviewed retry harness and verify the Spark preview. The user authorized committing, pushing and
   deployment on 1 November. Spark main advanced to `262569f` and has untracked team artifacts, so the feature
   branch is deployed in a separate Git worktree on port 8002; the existing main app on port 8000 is preserved.
2. Assess added model calls and compare latency before/after on the same serving setup. Do not claim a speed
   improvement or a sub-30-second result from offline tests.
3. Expand PX definitions, footnotes and methodological metadata; improve discovery with these profiles.
4. Strengthen cross-table population, geography, period, denominator and unit compatibility and source freshness.
5. Validate final prose against structured claims as well as numeric values.
6. Calibrate correctness, coverage, false refusals, clarification and latency with development questions; frozen
   and holdout sets remain measurement-only.

Edit code only on this computer. Spark testing must use code pulled from GitHub after it has been published;
do not copy unpublished local changes onto the Spark. Preview URL: `http://10.10.40.10:8002/`. Runtime smoke tests
and observed latency must be reported separately from the offline regression results.
