# Structured questions and metadata compatibility

This is the first implementation of the semantic validation milestone. It adds a shared gate after selection
and before fetching data, while preserving the dictionary, rankers and classic/tiered candidate searches.

## Question specification

Each planned sub-question contains `specification` with:

- `requirements`: exactly one measure, plus the requested statistic, population, geography, period, unit,
  frequency, adjustment, price basis and tax basis as applicable.
- `operation`: value, series, difference, percentage change, share, maximum, minimum or ranking.
- `ambiguities`: unresolved interpretations that require clarification.

Each requirement has `facet`, canonical English `value`, `origin` and supporting `quote`.
User requirements must quote the question. Context requirements may quote the question or the supplied selected-table
context. Translation changes the canonical value, not its supporting quote. Only the documented defaults are
allowed: Iceland, latest period, unadjusted figures and totals. Mean, median, an income/wealth interpretation or
an age threshold cannot be introduced as a default. A numeric population threshold must be grounded in the quote;
spelled-out thresholds currently require clarification or improved extraction rather than a guessed conversion.

Example: “Miðgildi tekna” produces separate requirements for income (`quote: tekna`) and median
(`quote: Miðgildi`). A mean-income query cannot pass merely because the table title mentions both statistics.

Forced-table requests now run the planner too. The selected table supplies context but does not waive the
question's requirements. The population operand of a per-capita question receives its own specification.

## Table profiles and evidence

`hagbot.semantics.profile` builds a profile from the selected source's `TableMeta`, with stable IDs for the title,
description, notes, dimensions, values, source-declared unit and time frequency. It fingerprints the metadata.
The source adapters preserve optional description, notes, update information and unit/title provenance.

Catalog title fallbacks and unit hints cannot be cited as source evidence. Generated catalog enrichment,
dictionary mappings and vector similarity do not enter the evidence profile.

The selection model returns `compatibility.checks` in the same call that selects codes. Each requirement index
must occur exactly once, with a supported/unsupported/unknown verdict and exact field/quote references.

The gate rejects:

- Missing/duplicate checks, invented fields, nonexistent quotes and citations of unselected values.
- Unknown compatibility or support without evidence.
- Known definition conflicts: income/wealth, mean/median, employment/unemployment, price basis, tax basis,
  adjustment and CPI level versus the annual change used for inflation.
- Additional conflicting selected statistics, even if the requested statistic is present too.
- Known currency/unit/scale or frequency mismatches. Unit conversion is not inferred.
- Numeric/geographic claims without matching evidence and an operation changed during resolution.

An unsupported verdict with grounded evidence yields `unavailable` for the checked table. Incomplete or invalid
evidence yields `unverified`. Existing date, category and completeness checks remain active.

Voting may only attach a compatibility record to a query actually produced by a voter. If per-dimension majority
voting constructs a new combination nobody checked, the system abstains.

## Answer audit record

Each answer part includes `verifications`, forwarded by the MCP adapter, containing the structured specification,
requirement checks, and fingerprints of the metadata, specification and validated selection. The executed selection
has a separate fingerprint. A permitted latest-populated-period fallback or ranking trim records an amendment.
Merged same-table series retain all their verification records.

The source citations and deterministic checks make decisions inspectable. They do not turn the model's semantic
judgment into a formal proof. A real quote can still be misinterpreted, and an omitted requirement or an unknown
statistical distinction can escape the common-rule checks.

## Remaining work in this milestone

- Expand source metadata extraction, including PX definitions/footnotes not exposed in the current metadata view.
- Use profiles to improve candidate discovery and try other candidates after a compatibility rejection.
- Calibrate acceptance and abstention with the serving model, measuring coverage and answer correctness separately.
- Evaluate multi-table definition compatibility and source freshness before claiming deployment readiness.

No serving-model or Spark accuracy is established by the offline regression tests. Frozen and holdout question
sets remain measurement-only.

```bash
python -B -m unittest discover -s tests -v
```
