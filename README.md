# RAGstofan — Gagnaþon 2026 prototype

Ask about Icelandic statistics in English or Icelandic. You get the number, its unit, the official table it came from, a chart, and the steps that produced it.

## Run

```bash
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env            # pick model endpoint and data source
ollama pull qwen2.5:14b         # 7b works too; 14b scored better
.venv/bin/uvicorn hagbot.api:app --reload
```

Open http://localhost:8000. API: `POST /api/ask {"question": "...", "language": null, "table_id": null}`, or the Track 2 brief's `POST /spyrja_px_api {"prompt": "..."}`.

The default data source (`DATA_SOURCE=local`) reads the organizers' snapshot of every table (data release of 18 October 2026, 250 MB zipped, 5.3 GB unpacked) and asks the live PX-API only for what the snapshot lacks. Get it once:

```bash
mkdir -p data/release && cd data/release
for i in 01 02 03 04 05 06; do curl -L -O https://raw.githubusercontent.com/SyedMuhammadAzhar/gagnvist-starter/HEAD/data-release/gagnvist-pxweb-data-2026-10-18.zip.part$i; done
cat gagnvist-pxweb-data-2026-10-18.zip.part* > release.zip && unzip -q release.zip && rm release.zip *.part*
```

Without it, set `DATA_SOURCE=pxapi` to use the live API only.

## How a question becomes an answer

```
question
  └─ plan        LLM   split into 1–3 sub-questions, one table each; English search query
      └─ per sub-question, in parallel:
         retrieve  BM25 over the catalog → LLM picks a table, or asks back when two fit equally
         select    LLM picks dimension codes + operation; code validates codes, fills defaults,
                   and overrides "value" when the wording clearly asks for a change
         fetch     data source returns tidy rows
         compute   Python does every calculation (difference, pct_change, share)
         chart     rules pick kpi / line / bar from the data shape
  └─ compose     LLM writes 1–3 sentences from the facts; every number is checked against the facts,
                 and a template answer replaces it if anything was invented
```

Every LLM step has a Pydantic output schema (`hagbot/steps/*.py`). The model never does arithmetic and never chooses chart code.

| Path | What |
|---|---|
| `hagbot/schemas.py` | The answer contract the frontend renders from |
| `hagbot/llm.py` | Client for any OpenAI-compatible endpoint (Ollama, vLLM on LUMI, hosted) with JSON validation and retry |
| `hagbot/sources.py` | `FixtureSource` (offline) and `PxApiSource` (live, throttled, cached). Add the kit's `data.px` reader here |
| `hagbot/catalog.py` | Table catalog + BM25. Add vector search (kit's bge-m3 index) here and fuse scores |
| `hagbot/steps/` | plan, retrieve, select, compute, chart, compose |
| `frontend/` | No-build HTML/JS; Vega-Lite charts; clarification buttons; data table; step timings |
| `eval/run_eval.py` | Scores right table / right answer by level × language, saves results, compares to a baseline |
| `scripts/build_catalog.py` | Crawls the live PX-API into a catalog (slow on purpose, resumable) |

## Evaluate after every change

```bash
.venv/bin/python -m eval.run_eval --label my-change --baseline eval/results/<previous>.json
```

The ten seed questions in `eval/questions.jsonl` only use numbers from the organizers' slides. On Friday, convert the kit's 52 questions into this format and record the baseline first.

Results so far (live PX-API, full 1,857-table catalog):

| Change | Model | Right answers |
|---|---|---|
| Keyword search over titles only | 7b | 1/10 (right table among candidates: 0/10) |
| + option labels, prefix matching, stopwords, recency, coverage boost, 15 candidates | 7b | 1/10 (candidates: 5/10) |
| + latest period when the question names none | 7b | 3/10 |
| Same pipeline | 14b | 4/10 (candidates: 5/10) |
| + synonyms (people/live/búa → population), title-year recency, Icelandic glossary in the planner, each part of a multi-part question searched on its own | 14b | 3/10 (candidates: 9/10) |
| + table rules in code: drop trend/seasonal/projection/census/old-division tables unless asked; a named place must be an option; a month needs a monthly table | 14b | 7/10 (candidates: 10/10) |
| + with no period asked, swap to the most recent table of the same survey; reject answers in another script | 14b | 9/10 |
| + "Iceland" means the whole country, not Icelandic citizens | 14b | 10/10 (median 42 s) |

| + annual-change options, multi-word synonyms, forecast filter, share completion (fixes from the second set) | 14b | 10/10 (median 49 s) |

Second question set, `eval/questions_heldout.jsonl`: 20 questions on inflation, wages, exports, hotel stays,
Keflavík passengers, house prices, births, GDP, migration, mean age and cars (13 English, 7 Icelandic), answers
read from the live API on 30 Oct 2026.

| Change | Model | Right answers |
|---|---|---|
| First run, rules from the first set only | 14b | 11/20 (4 of the misses: PX-API 429) |
| + "inflation"/"12 months"/"growth" use the table's own annual-change option; month regex fix; place filter only when a top hit has the place; glossary | 14b | 16/20 |
| + inflation/hagvöxtur search synonyms, forecast tables dropped unless asked, *verðmæti* in glossary | 14b | 20/20 (median 44 s) |

What moved the score was turning table-choice rules into code (`narrow` and `freshest_sibling` in
`hagbot/steps/retrieve.py`, the annual-change and share rules in `hagbot/steps/select.py`), not a bigger model:
the same rules written in the prompt were followed unreliably. 11/20 is the honest score on unseen questions;
both sets have since been fixed against, so neither is held out any more. The kit's 52 questions are the next
unseen test. "Right table" is below "right answer" because equivalent tables (MAN04203 for Reykjavík population)
are not on the accepted lists; the numbers match.

## Known limits

- **Fixtures** (`data/fixtures`) hold only the few values shown on the 28 Oct slides; codes are placeholders, not the live API's.
- **PX-API** rate-limits bursts (`429`). The source and crawler space calls 1 s apart, retry with back-off, and cache on disk.
- **Catalog** `data/catalog_live.json` was crawled on 28 Oct 2026 (1,857 tables, option labels included); 1 table was skipped.
- **"Current" answers move**: the latest unemployment month changed from 2026M08 to 2026M09 during development.
- **Icelandic answers** from the 7B model are grammatically weak. Options: a bigger model on LUMI, or template sentences for Icelandic.
- **Latency** on a laptop is 10–45 s per question (3–5 sequential model calls). vLLM on LUMI and parallel sub-questions should cut this.

## Friday checklist

1. Clone `gagnvist-starter`, run its `gagnvist evaluate` for the official baseline.
2. Load the kit's `cards/` into `Catalog` and its `data.px` files into a `LocalPxSource`.
3. Point `LLM_BASE_URL` at the LUMI tunnel.
4. Port the 52 questions to `eval/questions.jsonl`; work on table choice first.
