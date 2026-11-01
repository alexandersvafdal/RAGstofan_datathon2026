# RAGstofan

**Ask Statistics Iceland (Hagstofa Íslands) a question in plain English or Icelandic, and get the official number
back:** its unit, the table it came from (with a link), a chart, and every step that produced it.

Built for Gagnaþon 2026 (Track 2: an agentic AI over Statistics Iceland's PX-API).

## Objectives

1. **Right number, from the right official table.** Statistics Iceland publishes 1,857 tables. The hard part of a
   question like "Hve margir búa í Kópavogi?" is finding the one table and the categories that answer it.
2. **Never an invented number.** The language model reads, plans and chooses; it never does arithmetic and never
   gets the last word on a figure. Every calculation is done in Python, and every number in the written answer is
   checked against the data before it is shown.
3. **Icelandic and English alike.** Questions in either language, answers in the same language.
4. **Transparent.** Each answer shows the source table, the categories chosen, any calculation, and each step's
   time, so a user can check it.
5. **Runs on open models.** Any OpenAI-compatible endpoint: Ollama on a laptop or a GPU box, or vLLM.

## Example questions

Answered from one table:

- How many people live in Reykjavík? · *Hvað búa margir í Reykjavík?*
- What is the annual inflation rate? · *Hver er verðbólgan?*
- What is the unemployment rate? · *Hvert er atvinnuleysið?*
- How many foreign passengers came through Keflavík airport in August?
- How much fish did Iceland export last year? · *Hve mikið var flutt út af sjávarafurðum á síðasta ári?*

Comparisons, changes and shares (computed in code):

- How many more men than women live in Iceland? · *Hve mörgum fleiri karlar en konur búa á Íslandi?*
- How much did Reykjavík grow from 2025 to 2026?
- What share of Iceland's people live in Reykjavík?
- How many people live in Kópavogur and Hafnarfjörður together?
- How has the consumer price index changed since 2020?

Several tables in one question:

- How many people live in Iceland, and what is the unemployment rate?
- How have wages developed compared with the consumer price index?
- How many children were born in Iceland in 2025 per 1,000 inhabitants on 1 January 2025?
  (births from one table, the population from another, divided in code)

Follow-ups use the conversation: "and in Akureyri?", "what about last year?", "put them in the same chart".

The full test sets are in `eval/` (the organisers' 60 questions are in `eval/organizers.jsonl`).

## How to use it

### Run it

```bash
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt -r requirements-e5.txt
cp .env.example .env            # model endpoint, data source, models in the picker
ollama pull qwen2.5:14b         # or any model you set as LLM_MODEL; bigger Qwen 3 models score higher
.venv/bin/uvicorn hagbot.api:app --host 0.0.0.0 --port 8000
```

The first start embeds every table for the e5 search (a few minutes on CPU) and caches the vectors in `.cache/`.
Without `requirements-e5.txt` the app warns and uses keyword search alone.

The default data source reads the organisers' snapshot of every table (data release of 18 October 2026, 250 MB
zipped, 5.3 GB unpacked) and asks the live PX-API only for what the snapshot lacks. Get it once:

```bash
mkdir -p data/release && cd data/release
for i in 01 02 03 04 05 06; do curl -L -O https://raw.githubusercontent.com/SyedMuhammadAzhar/gagnvist-starter/HEAD/data-release/gagnvist-pxweb-data-2026-10-18.zip.part$i; done
cat gagnvist-pxweb-data-2026-10-18.zip.part* > release.zip && unzip -q release.zip && rm release.zip *.part*
```

Without it, set `DATA_SOURCE=pxapi` in `.env` to use the live API only.

### Ask it

| Where | How |
|---|---|
| **Website** | Open http://localhost:8000. Type or speak a question; example questions float on the front page. Under each answer: the source table, a data table, the steps with their times, chips for related questions. |
| **HTTP API** | `POST /api/ask {"question": "Hve margir búa á Íslandi?"}` returns the answer as JSON. `POST /api/ask/stream` streams each step as it finishes. `table_id` fixes the table; `model` picks the model. |
| **Track 2 endpoint** | `curl -X POST localhost:8000/spyrja_px_api -H 'Content-Type: application/json' -d '{"prompt": "Hve margir búa á Íslandi?"}'` |
| **Claude, ChatGPT and other MCP clients** | `mcp_server/` offers `ask_icelandic_statistics`, `search_tables` and `describe_table`. Setup: [mcp_server/README.md](mcp_server/README.md). |
| **iPhone / iPad app** | SwiftUI app in `ios/` (TestFlight build script `ios/testflight.sh`); it talks to the same API. |
| **Voice** | `voice/server.py` (port 8001): Whisper for speech in; Kokoro (English) and Piper (Icelandic) for speech out. The website and app use it when it runs. |

On the team's GPU server ("the Spark") everything starts at boot with `scripts/start_spark.sh`.

## How it connects to Statistics Iceland

All numbers come from Statistics Iceland's **PX-API** (PxWeb v1, `https://px.hagstofa.is`), or from the organisers'
snapshot of that same API. The code is in `hagbot/sources.py`.

**The table catalogue.** `scripts/build_catalog.py` walked the PX-API's table tree (`/pxen/api/v1/en/...`) and saved
every table's ID, English and Icelandic titles, dimensions, periods and category labels to `data/catalog_live.json`
(1,857 tables). Search runs over this file; nothing is sent to the API to find a table.

**Reading a table.** Once a table is chosen, the app:

1. reads its metadata, a `GET` on `https://px.hagstofa.is/pxen/api/v1/en/<folder path>/<table id>.px`
   (dimensions, their codes and labels, which dimension is time);
2. sends a `POST` to the same URL with only the categories needed and `"response": {"format": "json-stat2"}`, for
   example `{"query": [{"code": "Kyn", "selection": {"filter": "item", "values": ["1", "2"]}}, ...]}`;
3. turns the json-stat2 answer into tidy rows (one value per combination of categories).

The live API answers `429` when called too fast, so calls are spaced at least `PX_MIN_INTERVAL_S` apart (1 s),
retried with back-off (honouring `Retry-After`), and every answer is cached on disk in `.cache/`.

**The snapshot.** With `DATA_SOURCE=local` (the default) the same metadata (`metadata.json`) and data (`data.px`, PX
format, parsed by the app) are read from the organisers' release on disk: no network and no rate limit, and the same
numbers the official questions were checked against. A table missing from the snapshot, or a period published after
18 October 2026, goes to the live API. `scripts/refresh_tables.py` refetches the tables Statistics Iceland has updated
since the snapshot, in the snapshot's own format.

**Sources in the answer.** Every answer names its table and links to it on the PxWeb site
(`https://px.hagstofa.is/pxen/pxweb/en/<database>/<folder>/<table id>.px`), so the user can open the official table.

## How a question becomes an answer

The full diagram, with every LLM call marked: [docs/architecture.md](docs/architecture.md).

1. **Understand.** A follow-up is rewritten into a question that stands alone. A planner (LLM) restates the question
   in English with search words and reads its facts: places, period, breakdown wanted. Code drops any fact that is
   not in the user's own words.
2. **Find the table, one table first.** The whole question is first treated as one part. Three methods each suggest a
   table, and the model confirms or rejects each with a yes or no ("does this table hold the figures asked for?"):
   - the **keyword dictionary** (`data/keywords.json`: everyday words, Icelandic or English, mapped to subjects and
     their tables, e.g. *verðbólga* → consumer price index → VIS01000);
   - **search**: BM25 keyword search and e5 vector search (`intfloat/multilingual-e5-large`) over all 1,857 tables,
     run in parallel and fused, with code rules that drop variants nobody asked for (seasonally adjusted, forecasts,
     old index bases);
   - **every table**: the model names its best three from a list of all tables, written to fit its context
     (`data/models.json`), or a subject area first when the list does not fit.

   If no table is confirmed, the planner splits the question into 2, then 3, then 4 parts, and each part goes
   through the same search. If a part still has no confirmed table, the user picks from each method's top three.
3. **Read the numbers.** For each part, the model chooses the categories (year, place, sex, …). Code checks every
   choice against the table, fills defaults (the total, the latest period), and applies the user's own words (only
   the places named, the period asked, quantity or value). Then the data is fetched.
4. **Calculate in code.** Differences, % changes, shares, sums, rankings, highest/lowest, and per-inhabitant
   figures across two tables.
5. **Answer.** A chart type is chosen from the shape of the data. The model writes 1–3 sentences; every number in
   them is checked against the data, and if any number is not in it, a fixed template written from the data replaces
   the answer. A final
   check reads the answer back against the question and says so up front when it is unsure or wrong.

Every LLM step has a Pydantic output schema and is validated, with retries, before it is used.

### Project structure

| Path | What |
|---|---|
| `hagbot/api.py` | FastAPI server: the website, `/api/ask`, streaming, `/spyrja_px_api`, voice and table routes |
| `hagbot/pipeline.py` | Runs the steps for a question: planning, finding tables, reading, combining, answering |
| `hagbot/steps/` | One file per step: `plan`, `followup`, `tiered` (the table finder), `retrieve` (code rules), `select` (categories), `compute`, `ratio` (combining two parts), `chart`, `compose`, `check`, `suggest` |
| `hagbot/catalog.py`, `hagbot/e5.py` | BM25 search and e5 vector search over the catalogue |
| `hagbot/sources.py` | Data sources: live PX-API, the snapshot on disk, offline fixtures |
| `hagbot/llm.py`, `hagbot/models.py` | Client for any OpenAI-compatible endpoint; each model's usable context |
| `data/` | Table catalogue, keyword dictionary, model contexts, table list from Statistics Iceland |
| `frontend/` | Website: plain HTML/JS, no build step, Vega-Lite charts |
| `ios/` | iPhone and iPad app (SwiftUI) |
| `mcp_server/` | MCP server for Claude, ChatGPT and other MCP clients |
| `voice/` | Speech in and out |
| `eval/` | Question sets with known answers, scorers, saved results |
| `scripts/` | Catalogue crawler, snapshot refresh, question-set builders, Spark start-up |
| `docs/` | Architecture diagram and design notes |

## Evaluation

```bash
.venv/bin/python -m eval.run_eval --questions eval/organizers.jsonl --label my-change   # full answers
.venv/bin/python -m eval.retrieval_eval                                                # table search only, no LLM
```

`run_eval` scores the right table and the right number per question, by level and language, and saves the results
in `eval/results/` to compare with a baseline. Question sets: the organisers' 60 (`organizers.jsonl`), the team's
dev sets (`dev_core`, `dev_topics`), about 1,300 generated questions split into a dev half and a frozen half
(`gen_dev_*`, `gen_frozen_*`), and questions from the news (`holdout_news`).

On the organisers' 60, the team measured 60/60 with Qwen 3.8 27B and 59/60 with Qwen 3.6 35B-A3B (one server error),
with a median of about 7 s per one-part answer on the GPU server (1 November 2026). The table finder and the
question splitting were redesigned after those runs (one table first, a yes/no per method) and have not been scored
yet.

## Configuration

Everything is set in `.env` (see `.env.example`): the model endpoint and default model (`LLM_BASE_URL`,
`LLM_MODEL`), the models offered in the picker (`MODELS`), the data source (`DATA_SOURCE`), e5 search (`USE_E5`),
the context the server gives each request (`LLM_CONTEXT`; otherwise `data/models.json`, refreshed with
`python -m hagbot.models`), and majority voting on the model's choices (`VOTES`, `VOTE_MODELS`).

## Known limits

- **One table per part.** A question needs tables that can each answer one part; the planner splits into at most four.
- **The snapshot is from 18 October 2026.** Newer periods come from the live API, which rate-limits bursts.
- **Speed.** An answer takes several model calls (about 5–10 for a one-table question); on a laptop that is 30–90 s;
  on the team's GPU server about 7 s at the median before the redesign. Questions that need splitting take longer.
- **Icelandic answers** are worded less well than English ones on smaller models; the numbers are checked either way.
- **No forecasts, opinions or causes** ("why did inflation rise?").

## Live demonstration

The demo runs on the team's GPU server: **http://10.10.40.10:8000**, reachable from the **Myrin-Guest** wifi at the
venue. The team also reaches it over Tailscale.

## License

MIT, see [LICENSE](LICENSE).
