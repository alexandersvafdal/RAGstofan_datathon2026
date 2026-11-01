# Answer outcomes and integrity checks

`Answer.status` is one of:

| Status | Meaning |
| --- | --- |
| `answered` | All planned parts returned usable data and all required calculations completed. |
| `clarify` | A material ambiguity or an unspecified dimension needs the user's input. |
| `out_of_scope` | The planner identified a request outside the supported operations or sources. |
| `unavailable` | The checked table cannot provide the requested selection or calculation. This is not a claim about every table in the database. |
| `unverified` | Retrieval or query validation could not establish an answer. A search miss does not prove nonexistence. |
| `error` | The model, data service or another technical dependency failed. |

Abstentions include `issues`, with stable `code`, localized `message`, `status` and the affected `sub_question`.
The API, NDJSON stream and MCP adapter preserve these outcomes. Browser outcome labels distinguish them.
Only `answered` responses enter answered conversation history and receive generated suggestions.

## Selection and retrieval

Both retrievers reject invented table IDs and support rejecting every candidate. Even a single classic search
hit must be checked. The tiered finder retains the keyword dictionary, expansion, ranking and subject browsing;
it no longer forces a classic selection after every candidate has been rejected. Automatic headline/newer-table
replacements must pass a check too. An unknown forced table ID never triggers an unrelated search.

The selection model records explicit requirements in `requested_filters`: dimension code, quote from the question,
and required value codes. A missing dimension or unsupported filter has no value codes and stops the query.
Requirements must be grounded in the question and survive resolution. Independent checks also enforce common
explicit years, months, quarters, relative periods, literal category names and gender aliases. For one-part
questions the original wording is checked alongside the planner's wording.

Unknown codes cannot be dropped, unavailable periods cannot be moved to a table edge, and requested windows
cannot be shortened. Missing selections may use totals, the latest period, or a dimension's only value.
An unspecified dimension with multiple options and no total requires clarification. Equivalent code/label forms
still resolve normally. Unsupported calculations stop instead of becoming value lookups.

Fetched rows must cover the executed selection without missing/suppressed cells, duplicates or unexpected keys.
Missing operands and zero denominators stop calculations. With no requested period, the latest entirely empty
period may fall back to the latest populated period; displayed selections are built from the executed query.
Explicit dates and relative periods cannot use that fallback.

## Completeness and limits

If any planned part fails, composition is skipped and the response contains no success parts, sources or charts.
The message says a multi-part question could not be completed and issues identify the failing parts.
Ratio operands cannot be returned as an answer when the ratio could not be computed, and additional numerator
lookups cannot be silently discarded.

These are control-flow and data-integrity safeguards. They do not prove the model correctly interpreted every
phrase or selected the right statistical definition, nor detect every omitted sub-question. Scope classification
and extraction of requirements beyond the deterministic checks still depend on the model. This intentionally
increases abstentions; measure coverage and correctness with the actual serving model before deployment.
Cache freshness and a complete statistical compatibility gate remain separate work.

Run offline regression tests, without model, vector or PX-API calls:

```bash
python -B -m unittest discover -s tests -v
node --check frontend/app.js
```

Use development cases when changing behavior. Keep frozen and holdout question sets for measurement as described
in `eval/README.md`.
