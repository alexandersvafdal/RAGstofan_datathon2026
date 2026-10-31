# Question sets

| File | Questions | Made by | Role |
|---|---|---|---|
| `dev_core.jsonl` | 10 (6 EN, 4 IS) | us, 28 Oct | Development. The first set, written with the prototype. Fixed against; catches regressions. |
| `dev_topics.jsonl` | 20 (13 EN, 7 IS) | us, 30 Oct | Development. New topics (inflation, wages, trade, tourism, births, GDP, migration, cars), answers checked against the live API. Fixed against. |
| `organizers.jsonl` | 60 (30 EN, 30 IS) | the organisers | The official yardstick: `questions/examples.jsonl` from the gagnvist-starter repo, 10 easy, 10 calculation and 10 two-table questions per language, answers from the 18 Oct 2026 data release. Search fixes have been made by studying its misses, so it is no longer blind. |
| `holdout_news.jsonl` | 60 (30 EN, 30 IS) | us, 31 Oct | **Final holdout.** One headline figure from each of 30 Statistics Iceland news releases (Sept-Oct 2026); the answer key is the release's own wording. Built from `holdout_news_drafts.json` by `scripts/build_news_valset.py`. |

**The holdout is for measuring only.** Never change the bot because of a question in it, or its score stops saying
anything about new questions. `retrieval_eval` leaves it out unless it is named.

How the holdout compares with the organisers' questions (English halves): the organisers ask mostly about population
(15 of 30) and two thirds of their questions need a calculation or two tables; the news holdout has no population
questions, 28 of 30 are single lookups, and its wording is far from theirs (median similarity 0.35, against 0.77 among
the organisers' own questions). A good holdout score shows the bot copes with new topics, not that it handles the
organisers' kind of question.

```bash
python -m eval.run_eval --questions eval/holdout_news.jsonl --label <name>   # full answers
python -m eval.retrieval_eval                                              # search only, dev + organizers
```
