# Question sets

The correctness runner, `python -m eval.integrity_eval --questions <dev.jsonl> --endpoint <server> --repeat 3
--output <results.json>`, measures precision, coverage, false refusals, retries, calls and p50/p95 latency separately.
It supports `expected_status`, `expected_facts` (value/unit/period/label), `expected_operation` and `expected_result`
alongside table/value labels. Table-only questions are not counted as fully graded answers. Frozen/holdout paths
require `--measurement`. `--summarize <results.json>` reports saved results without requests. Provision source
profiles and choose the freshness policy before evaluation; see [implementation notes](../docs/correctness-implementation.md).

| File | Questions | Made by | Role |
|---|---|---|---|
| `dev_core.jsonl` | 10 (6 EN, 4 IS) | us, 28 Oct | Development. The first set, written with the prototype. Fixed against; catches regressions. |
| `dev_topics.jsonl` | 20 (13 EN, 7 IS) | us, 30 Oct | Development. New topics (inflation, wages, trade, tourism, births, GDP, migration, cars), answers checked against the live API. Fixed against. |
| `organizers.jsonl` | 60 (30 EN, 30 IS) | the organisers | The official yardstick: `questions/examples.jsonl` from the gagnvist-starter repo, 10 easy, 10 calculation and 10 two-table questions per language, answers from the 18 Oct 2026 data release. Search fixes have been made by studying its misses, so it is no longer blind. |
| `holdout_news.jsonl` | 108 (54 EN, 54 IS) | us, 31 Oct | **Final holdout**, two parts. *News lookups* (`news-…`, 30 per language): one headline figure from each of 30 Statistics Iceland news releases, answer = the release's own wording (`holdout_news_drafts.json`). *Organiser-style* (`styled-…`, 24 per language): population and demography in the organisers' mix of single numbers, calculations and two-table questions; each answer computed by the build script from release figures or from table MAN02005/MAN00101 (`holdout_styled_drafts.json`). Built by `scripts/build_news_valset.py`. |

| `gen_dev_*.jsonl`, `gen_frozen_*.jsonl` | ~1,300 | `scripts/build_gen_valset.py`, 1 Nov | Generated, graded on the table (no answer key). Three sources: `tables` (3 questions × 2 languages for 141 catalogue tables, 6 per id prefix), `paraphrase` (the organisers' 60 reworded three ways, same answers), `siblings` (60 near-identical table pairs, one question per side). Split by table so the two halves share no table: fix against `gen_dev_*`, measure on `gen_frozen_*`. `retrieval_eval --by language area kind` shows the weak spots. |
| `../logs/questions.jsonl` | grows | the server | Every question asked through the app or the site, with the tables it got. Mine it for new dev questions; label the right table by hand. |

**The holdout is for measuring only.** Never change the bot because of a question in it, or its score stops saying
anything about new questions. `retrieval_eval` leaves it out unless it is named.

How the holdout compares with the organisers' questions (English halves; similarity = cosine on content words to
the closest organiser question):

| | Questions | Population | One number | Calculation | Two tables | Wording similarity (median) |
|---|---|---|---|---|---|---|
| Organisers | 30 | 16 | 10 | 10 | 10 | - |
| Holdout: news lookups | 30 | 0 | 25 | 5 | 0 | 0.35 |
| Holdout: organiser-style | 24 | 24 | 8 | 9 | 7 | 0.50 |

Report the two parts separately: the news lookups show how the bot copes with new topics, the organiser-style part
how it handles the kind of question the organisers ask. None of the organiser-style questions repeats one of theirs
(different places, periods and measures).

```bash
python -m eval.run_eval --questions eval/holdout_news.jsonl --label <name>   # full answers
python -m eval.retrieval_eval                                              # search only, dev + organizers
```
