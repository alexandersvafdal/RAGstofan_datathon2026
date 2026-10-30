# Design brief: Hagbot web interface and dashboard

Loose functional requirements for redesigning the frontend. Say *what* it must do; the look is yours to decide.

## Context

- **Event:** Statistics Iceland's Gagnaþon 2026. Team MOMS, Track 2 (direct PX-API). Demo on Sun 1 Nov at 20:00, 10 minutes per team.
- **Product:** a chatbot that answers questions about official Icelandic statistics in English or Icelandic. Every answer gives the number, its unit, the official table it came from, and a chart.
- **Judges score 1–5 on six criteria:** functionality, presentation, usability, clarity, deployment and user value. The design mainly serves *presentation*, *usability* and *clarity*.
- **The demo must show:**
  - a real question answered with values, units and an official source;
  - how the bot found its table;
  - the answer checked against the source;
  - an ambiguous question;
  - the response time;
  - a known limitation.

  Design the interface so each of these is visible without opening developer tools.

## Constraints

- **No build step.** Plain HTML, CSS and JavaScript in `frontend/` (`index.html`, `app.js`, `style.css`), served as static files by the FastAPI app at `http://localhost:8000`. Run it with `.venv/bin/uvicorn hagbot.api:app`.
- **Charts:** Vega-Lite 5 is already loaded from jsDelivr. Keep it, or swap in another library loaded from a CDN with a pinned version. No npm.
- **Answers are slow:** 30–75 s on a laptop, faster on the team's GPU machine. Waiting is the main user experience problem.
- **Languages:** English and Icelandic. The UI has an Auto / IS / EN toggle; all interface text should exist in both languages.
- **Layout:** works on a projector (1920×1080) and on a phone (375 px wide). Light and dark mode.

## What the backend gives you

`POST /api/ask` with `{"question": "...", "language": null | "en" | "is", "table_id": null}` returns one `Answer`:

| Field | Use |
|---|---|
| `status` | `answered`, `clarify` (the user must pick a table), or `error` |
| `text` | the written answer, one or two sentences |
| `parts[]` | one per sub-question; a two-part question has two |
| `parts[].facts[]` | the headline numbers: `label`, `value`, `unit`, `formatted` |
| `parts[].computation` | when a calculation was done: `expression` (e.g. "139,804 − 138,772"), `result`, `unit` |
| `parts[].chart` | `type` `kpi` / `line` / `bar`, `title`, `x_label`, `y_label`, `unit`, `data[]` of `{x, y, series}` |
| `parts[].rows[]` | the raw cells behind the answer (for a data table view) |
| `parts[].table` | source table: `id`, `title`, `url` (the table's page on px.hagstofa.is) |
| `parts[].selection` | which categories were chosen, e.g. `{"Year": ["2025"], "Region": ["Total"]}` |
| `clarification` | `message` and `options[]` of `{label, table_id}`; asking again with `table_id` set answers from that table |
| `steps[]` | the pipeline: `name` (plan, retrieve, select, fetch, compose), `ms`, `detail` |
| `total_ms`, `llm_calls`, `model` | response time and what produced the answer |
| `text_source` | `model` (the model's sentence passed the number check) or `template` (it failed and a fixed sentence was used) |

`GET /api/health` returns the model name, the data source (`local` / `pxapi`) and the number of tables.

`GET /api/indicators` returns the dashboard's headline numbers in about 40 ms, with no model involved:

```json
{"data_as_of": "2026-10-18 snapshot", "errors": [],
 "indicators": [{"id": "inflation",
   "label": {"en": "Inflation, 12 months", "is": "Verðbólga, 12 mánuðir"}, "unit": {"en": "%", "is": "%"},
   "value": 5.9, "period": "2026M09", "previous": 5.6, "previous_period": "2026M08",
   "series": [{"x": "2024M10", "y": 5.1}, "... 24 points"],
   "table": {"id": "VIS01000", "title": "Consumer price index and changes, base 1988=100", "url": "https://px.hagstofa.is/..."},
   "question": {"en": "What is the annual inflation rate?", "is": "Hver er verðbólgan?"}}, "..."]}
```

The six indicators are population (1 January), inflation, unemployment rate, wage index 12-month change, overnight stays and GDP growth. The ids are fixed.

- **Period codes:** `2026` is a year, `2026M09` a month, `2026Q2` a quarter. Format them for display ("Sep 2026", "sept. 2026", "Q2 2026").
- **Rounding:** values arrive unrounded (GDP growth is 1.0857…). Show percentages to one decimal and counts as whole numbers.
- **Clicking a tile:** send `question` in the current language to `/api/ask`.

The backend can be extended if a requirement needs it: mark those items **[needs backend]**.

## Requirements

### 1. Question bubbles that come and go

- **Pool:** keep about 30 example questions across topics: population, Reykjavík, inflation/CPI, wages, unemployment, GDP, tourism (overnight stays, airport passengers), births, migration, house prices and cars. Write each in English and Icelandic.
- **Rotation:** show 4–6 bubbles at a time. Every few seconds one fades or drifts out and another comes in, so the screen feels alive but stays readable.
- **Hover:** pauses the rotation.
- **Click:** asks that question.
- **Language:** bubbles follow the language toggle.
- **Mix:** include an easy one (one number), a calculation ("how much did … grow"), a two-part one ("… and …") and one deliberately ambiguous one, so the demo can show all four types.
- **After an answer:** show 2–3 follow-up bubbles related to it, such as the same measure for another year or region, or a trend over time.
- **Reduced motion:** respect `prefers-reduced-motion` (no movement, just swap).

### 2. Answer card with richer charts

- **Headline:** the number large, with its unit and period. For a calculation, show the expression too (e.g. "139,804 − 138,772 = +1,032").
- **Charts by type:**
  - `kpi`: a stat tile. For the dashboard tiles, `series` from `/api/indicators` gives the sparkline; chat answers have no series yet [needs backend].
  - `line`: a line chart with a hover crosshair and tooltip, the latest point labelled, and a soft area fill.
  - `bar`: sorted bars, with the asked-for category highlighted (e.g. Reykjavík among municipalities).
- **Two-part answers:** two tiles side by side, never one chart with two y-axes.
- **Data table view:** a toggle showing the underlying `rows`.
- **Source chip on every chart:** table id and title, linking to `table.url`.
- **Export:** download the chart as PNG and the data as CSV.
- **Formatting:** numbers in the answer language's style (English 139,804; Icelandic 139.804).

### 3. The wait (30–75 s)

- **Progress, not a spinner:** show the five steps (understand question → find table → choose categories → fetch numbers → write answer) with an elapsed-time counter.
  - **Interim version (no change to `POST /api/ask` needed):** animate the steps on a timer.
  - **Later:** real step events from the server [needs backend: a streaming endpoint].
- **Interim content:** while waiting, show something useful, e.g. the question bubbles or a "did you know" fact from the dashboard.
- **Concurrency:** a second question while one is running is queued and shown as queued, not lost.

### 4. "How I got this" (evidence panel)

- **Timeline:** a visual timeline of `steps[]` with milliseconds per step. Judges want to see the route and the response time.
- **Table choice:** show which table was chosen from which candidates (`retrieve` detail lists them), and the chosen categories (`selection`).
- **Verification badge:** "numbers checked against the table" when `text_source` is `model`, "fixed wording" when `template`.
- **Placement:** collapsed by default, one click to open; it's the core of the "explain your route" demo segment.

### 5. Clarification

- When `status` is `clarify`, show `clarification.message` and the options as buttons. Clicking one asks the same question again with that `table_id`.

### 6. Dashboard

- **Purpose:** a landing view that shows the bot is about real, current statistics before anyone types.
- **Tiles:** 5–6 headline indicator tiles, for example:
  - population on 1 January
  - inflation (12-month CPI change)
  - unemployment rate
  - wage index change
  - overnight stays
  - GDP growth
- **Each tile:** the value, period, a small trend, the source table id, and a click action that asks the bot the matching question.
- **Data:** `GET /api/indicators` (see above). It answers in milliseconds; never fill the tiles through the 45-second chat route.
- **Freshness:** a "data as of" line. The data is the organisers' snapshot of 18 October 2026, with the live API for anything newer.
- **Layout:** the dashboard and the chat share the screen on desktop (dashboard above or beside the chat). On a phone the dashboard collapses into a swipeable row.

### 7. Errors and limits

- **Errors:** plain error messages in the chosen language (no table found, data source unreachable, model timeout), each with a retry button.
- **Known limitations:** a short "What this can't do yet" note, reachable from the footer:
  - answers come from one table per sub-question;
  - slow on a laptop;
  - weaker Icelandic wording.

  Judges ask for a known limitation, and showing it is a strength.

## Out of scope

User accounts, chat history across sessions, and editing or uploading data.

## Done when

- **Questions to test:** the bubbles cover an easy, a calculation, a two-part and an ambiguous question, and each renders correctly.
- **Visibility:** in a 10-minute demo, every item in the demo list above can be shown from the UI.
- **Layouts and modes:** works at 1920×1080 and 375 px wide, in light and dark mode, in both languages.
- **No regressions:** the existing `POST /api/ask` contract is unchanged; new data comes from new endpoints.
