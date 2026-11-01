"""Orchestrator: plan → (per sub-question, in parallel: retrieve → select → fetch → compute → chart) → compose."""

import asyncio
import copy
import re
from datetime import date
from collections.abc import Callable

from .catalog import Catalog
from .config import settings
from .llm import LLM, LLMError, Timer
from .schemas import (Answer, AnswerPart, AskRequest, Clarification, ClarifyOption, Step, TableRef)
from .sources import DataSource, make_source, table_url
from .hierarchy import children
from .steps.chart import choose_chart, comparison_chart
from .steps.compose import compose
from .steps.check import check_answer
from .steps.compute import aggregate_rows, compute
from .steps.followup import looks_like_followup, rewrite
from .steps.plan import Plan, SubQuestion, merge_period_parts, plan
from .steps.ratio import difference_parts, divide_parts, share_parts
from .steps.retrieve import _INDEXATION_TABLE, period_end
from .steps.tiered import _check, retrieve_tiered
from .steps.select import guess_op, select
from .steps.suggest import suggest
from .text import detect_language, normalize

# A label that is itself a percentage: "Annual change, %", "Unemployment rate", "Percentage of GDP". A % after a
# number is a product's description, not its unit ("wine of alcoholic strength > 8.5%"), so it does not count.
_PERCENT = re.compile(r"(?<![\d.])%|\bpercent|prosent|hlutfall|volume change|change on previous|change from previous|"
                      r"annual growth|annual rate of change|\b(un)?employment rate|activity rate|participation rate|"
                      r"vacancy rate|household rate|rate of individuals|overburden rate|magnbreyting|breyting fra fyrra|"
                      r"arleg fjolgun|atvinnuleysi")
_RELATIVE = r"\blast (year|month|quarter)\b|\bprevious year\b|\bsidasta (ar|manud|arsfjord)|\bi fyrra\b|\bfyrra ar"

# shown when no method's table was confirmed: what was looked for, then the user picks from each method's best three,
# each with the reason it was turned down
CLARIFY = {
    "en": "No table was found that answers “{asked}”. These came closest, each with what it lacks. Pick one and I "
          "answer from it, or ask in another way.",
    "is": "Engin tafla fannst sem svarar „{asked}“. Þessar komust næst, hver með því sem hana vantar. Veldu eina og ég "
          "svara úr henni, eða spyrðu á annan hátt.",
}


class NoFigures(Exception):
    """The table found is on the right subject but has no figures for the period asked."""


_MONTHS_EN = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
_MONTHS_IS = ["janúar", "febrúar", "mars", "apríl", "maí", "júní", "júlí", "ágúst", "september", "október", "nóvember", "desember"]


def _say_period(p: str, lang: str) -> str:
    """A period code as words: 2026M09 -> "September 2026" / "september 2026", 2026Q3 -> "Q3 2026" / "3. ársfjórðungur 2026"."""
    m = re.match(r"(\d{4})(?:([MQ])(\d+))?", p or "")
    if not m:
        return p
    year, kind, n = m.group(1), m.group(2), int(m.group(3) or 0)
    if kind == "M" and 1 <= n <= 12:
        return f"{(_MONTHS_IS if lang == 'is' else _MONTHS_EN)[n - 1]} {year}"
    if kind == "Q":
        return f"{n}. ársfjórðungur {year}" if lang == "is" else f"Q{n} {year}"
    return year


class NeedsClarification(Exception):
    def __init__(self, clarification: Clarification):
        self.clarification = clarification


class StepLog(list):
    """The answer's step list, which also reports each step the moment it is recorded (for live progress)."""

    def __init__(self, on_step: Callable[[Step], None] | None = None):
        super().__init__()
        self.on_step = on_step

    def append(self, step: Step) -> None:
        super().append(step)
        if self.on_step:
            self.on_step(step)


class Pipeline:
    def __init__(self, llm: LLM | None = None, catalog: Catalog | None = None, source: DataSource | None = None):
        self.llm = llm or LLM()
        self.catalog = catalog or Catalog.load(settings.catalog_path)
        self.source = source or make_source(self.catalog)

    async def answer(self, req: AskRequest, on_step: Callable[[Step], None] | None = None) -> Answer:
        """Answer with the model asked for, or the default. Another model runs on a copy of this pipeline, so
        concurrent questions on different models do not disturb each other."""
        model, reasoning = (settings.fast_model, "none") if req.fast else (req.model, None)
        if model or reasoning:
            other = copy.copy(self)
            other.llm = self.llm.with_options(model, None, reasoning)
            return await other._answer(req, on_step)
        return await self._answer(req, on_step)

    async def suggest(self, ans: Answer) -> list[str]:
        """Follow-up questions for an answer already shown: asked for afterwards, so they never delay it."""
        if ans.status != "answered" or not ans.parts:
            return []
        metas = await asyncio.gather(*(self.source.meta(p.table.id) for p in ans.parts), return_exceptions=True)
        pairs = [(p, m) for p, m in zip(ans.parts, metas) if not isinstance(m, Exception)]
        return await suggest(self.llm, ans.question, ans.text, [p for p, _ in pairs], [m for _, m in pairs], ans.language)

    async def _answer(self, req: AskRequest, on_step: Callable[[Step], None] | None = None) -> Answer:
        lang = req.language or detect_language(req.question)
        if lang == "en" and detect_language(req.question) == "is":
            # unmistakably Icelandic (ð, þ, æ … or Icelandic question words) under an English setting: the question's
            # own language wins, so a spoken Icelandic question is not answered, and read aloud, in English
            lang = "is"
        total, steps = Timer(), StepLog(on_step)
        calls_before = self.llm.calls
        ans = Answer(question=req.question, language=lang, status="error", text="", model=self.llm.model)
        try:
            if not req.table_id and looks_like_followup(req.question, req.history):
                # "what about last year?" becomes a full question from the previous turn; the rest of the pipeline
                # then runs on it exactly as if the user had typed it
                t = Timer()
                full = await rewrite(self.llm, req.question, req.history)
                steps.append(Step(name="context", ms=t.ms(), detail=full))
                req = req.model_copy(update={"question": full})
            forced: list[str | None]
            if req.table_id:  # the user already chose a table: skip planning
                p = Plan(sub_questions=[SubQuestion(question=req.question, search_query=req.question)])
                forced = [req.table_id]
            else:
                t = Timer()
                p = merge_period_parts(await plan(self.llm, req.question))
                forced = [None] * len(p.sub_questions)
                steps.append(Step(name="plan", ms=t.ms(), detail=" | ".join(
                    s.question + (f" [places {s.places}, period {s.period_kind} {s.period!r}, breakdown {s.breakdown}{' by ' + s.breakdown_by if s.breakdown_by else ''}]"
                                  if s.places or s.period_kind != "none" or s.breakdown else " [no facts]")
                    for s in p.sub_questions) + (f" · per {p.per_inhabitant} inhabitant(s): part 1 ÷ part 2" if p.per_inhabitant else "")))

            results = await asyncio.gather(
                *(self._answer_part(sq, req.question, lang, f, steps, alone=len(p.sub_questions) == 1)
                  for i, (sq, f) in enumerate(zip(p.sub_questions, forced))),
                return_exceptions=True,
            )
            clar = next((r for r in results if isinstance(r, NeedsClarification)), None)
            if clar:
                ans.status, ans.clarification, ans.text = "clarify", clar.clarification, clar.clarification.message
                return ans
            errors = [r for r in results if isinstance(r, Exception)]
            parts = await self._merge_series([r for r in results if isinstance(r, AnswerPart)])
            # per inhabitant, as the planner split it: the figure divided by the population
            ratio = p.per_inhabitant > 0 and len(p.sub_questions) == 2
            if ratio and len(parts) == 2 and (rp := divide_parts(parts[0], parts[1], p.per_inhabitant, lang)):
                parts = list(rp)
                steps.append(Step(name="compute", ms=0, detail=rp[0].computation.expression))
            elif (not ratio and len(parts) == 2 and guess_op(req.question) == "share"
                  and (sp := share_parts(parts[0], parts[1], lang))):   # a share the planner split across two tables
                parts = list(sp)
                steps.append(Step(name="compute", ms=0, detail=sp[0].computation.expression))
            elif (not ratio and len(parts) == 2 and guess_op(req.question) in ("difference", "pct_change")
                  and (dp := difference_parts(parts[0], parts[1], lang))):   # a change the planner split in two
                parts = list(dp)
                steps.append(Step(name="compute", ms=0, detail=dp[0].computation.expression))
            for e in errors:
                steps.append(Step(name="error", ms=0, detail=f"{type(e).__name__}: {e}"))
            if not parts:
                ans.text = _no_answer(lang, errors)
                return ans

            t = Timer()
            ans.text, ans.text_source = await compose(self.llm, req.question, parts, lang)
            steps.append(Step(name="compose", ms=t.ms(), detail=ans.text_source))
            # the finished answer read back against the question: wrong subject or a miss on the measure, place or
            # period asked, and the user is told so up front, the figures kept as the closest found
            t = Timer()
            try:
                v = await check_answer(self.llm, req.question, parts, ans.text)
                ans.verdict = {"answers": "ok", "uncertain": "uncertain", "wrong": "wrong"}[v.fit]
                ans.verdict_reason = v.reason
                steps.append(Step(name="check", ms=t.ms(), detail=f"{v.fit}; {v.reason}"))
                if ans.verdict == "wrong":
                    ans.text = f"{WRONG[lang]} {ans.text}"
                elif ans.verdict == "uncertain":
                    ans.text = f"{UNCERTAIN[lang]} {ans.text}"
            except Exception as e:   # the check is a safeguard; without it the answer stands as written
                steps.append(Step(name="check", ms=t.ms(), detail=f"skipped: {type(e).__name__}: {e}"))
            ans.parts, ans.status = parts, "answered"
            ans.comparison = comparison_chart(parts, lang)
            ans.sources = list({p.table.id: p.table for p in parts}.values())
            return ans
        except Exception as e:  # the API always returns an Answer, never a stack trace
            steps.append(Step(name="error", ms=0, detail=f"{type(e).__name__}: {e}"))
            ans.text = _no_answer(lang, [e])
            return ans
        finally:
            ans.steps, ans.total_ms, ans.llm_calls = list(steps), total.ms(), self.llm.calls - calls_before

    async def _merge_series(self, parts: list[AnswerPart]) -> list[AnswerPart]:
        """"How have stays by Icelanders and foreigners developed" is planned as two questions on the same table. Two
        line charts of one table side by side say less than one chart with a line each, so such parts become one."""
        out, by_table = [], {}
        for p in parts:
            if p.chart and p.chart.type == "line" and not p.computation:
                by_table.setdefault(p.table.id, []).append(p)
            else:
                out.append(p)
        for table_id, group in by_table.items():
            if len(group) == 1:
                out.append(group[0])
                continue
            meta = await self.source.meta(table_id)
            rows = list({tuple(sorted(r.key.items())): r for p in group for r in p.rows}.values())
            selections = {v.code: [c for c in v.values if any(r.key.get(v.code) == c for r in rows)] for v in meta.variables}
            first = group[0]
            out.append(first.model_copy(update={
                "sub_question": " | ".join(p.sub_question for p in group),
                "rows": rows,
                "facts": [f for p in group for f in p.facts],
                "selection": {k: list(dict.fromkeys(v for p in group for v in p.selection.get(k, []))) for k in first.selection},
                "chart": (c := choose_chart(meta, rows, selections, None)) and c.model_copy(update={"title": first.table.title}),
            }))
        return out

    async def _answer_part(self, sq: SubQuestion, original: str, lang: str, forced_table: str | None, steps: list[Step],
                           alone: bool = True) -> AnswerPart:
        t = Timer()
        # a multi-part question's full wording would pull every part toward the same tables
        r = await retrieve_tiered(self.llm, self.catalog, sq.question, sq.search_query,
                                  settings.retrieve_top_k, forced_table, original if alone else "", sq.breakdown_by)
        steps.append(Step(name="retrieve", ms=t.ms(),
                          detail=f"{r.table.id} from [{', '.join(r.candidates)}]" + (" (ambiguous)" if r.ambiguous else "")
                                 + (f" via {r.via}" if r.via else "") + (f" · votes {r.votes}" if r.votes else "")
                                 + (f" · concepts: {', '.join(r.concepts)}" if r.concepts else "")))
        if r.ambiguous:
            why = {c["id"]: c.get("reason", "") for c in r.ranking if c["signals"][-1:] == ["no"]}
            opts = [ClarifyOption(label=e.title_en if lang == "en" else (e.title_is or e.title_en), table_id=e.id,
                                  note=why.get(e.id, "")) for e in [r.table, *r.alternatives]]
            asked = (original if alone else sq.question).strip().rstrip("?.! ")
            raise NeedsClarification(Clarification(message=CLARIFY[lang].format(asked=asked), options=opts))

        t = Timer()
        meta = await self.source.meta(r.table.id)
        facts = sq if sq.places or sq.period_kind != "none" or sq.breakdown or sq.all_places else None
        res = await select(self.llm, meta, sq.question, original, facts=facts)
        if res.beyond:
            # "September 2026" in a table whose figures stop at August, while the table next door has September
            # (LAU04000 / LAU04001 in the snapshot): the neighbour answers, selected afresh. With no neighbour the
            # latest month answers, labelled as such (the figure may just not be out yet); a year the table never
            # had is refused instead (missing_period).
            if alt := await self._sibling_with(meta, r.candidates, res.beyond, sq.question, original):
                swapped = f"{res.beyond} is after {meta.id}'s last period; switched to {alt.id}"
                meta = alt
                res = await select(self.llm, meta, sq.question, original, facts=facts)
                res.notes.append(swapped)
        if res.missing_period:
            if alt := await self._sibling_with(meta, r.candidates, res.missing_period, sq.question, original):
                swapped = f"{meta.id} has no {res.missing_period}; switched to {alt.id}"
                meta = alt
                res = await select(self.llm, meta, sq.question, original, facts=facts)
                res.notes.append(swapped)
            else:
                time_var = next(v for v in meta.variables if v.time)
                first, last = (_say_period(x, lang) for x in (time_var.value_texts[0], time_var.value_texts[-1]))
                asked_p = _say_period(res.missing_period, lang)
                entry = self.catalog.get(meta.id)
                title = entry.title_is if lang == "is" and entry and entry.title_is else meta.title
                steps.append(Step(name="select", ms=t.ms(), detail=f"{res.operation} notes: {res.notes}"))
                raise NoFigures(f"Taflan {title} ({meta.id}) nær yfir {first} til {last}, ekki {asked_p}."
                                if lang == "is" else
                                f"{title} ({meta.id}) covers {first} to {last}, not {asked_p}.")
        chosen = {meta.var(c).text: [meta.var(c).label_of(v) for v in vals] for c, vals in res.selections.items()}
        steps.append(Step(name="select", ms=t.ms(),
                          detail=f"{res.operation} {chosen}" + (f" notes: {res.notes}" if res.notes else "")))

        t = Timer()
        rows = await self.source.query(meta.id, res.selections)
        detail = f"{len(rows)} rows"
        # A table's time dimension often lists a period before its numbers exist ("2026" with ".." until the year
        # ends). When the question named no period, the latest period with numbers is the answer.
        time_var = next((v for v in meta.variables if v.time), None)
        if (time_var and rows and all(r.value is None for r in rows) and res.selections.get(time_var.code) == [time_var.values[-1]]
                and not re.search(r"\b(?:18|19|20)\d\d\b", f"{sq.question} {original}")):
            wide = await self.source.query(meta.id, {**res.selections, time_var.code: time_var.values})
            filled = [r.key[time_var.code] for r in wide if r.value is not None]
            if filled:
                last = max(filled, key=time_var.values.index)
                res.selections[time_var.code] = [last]
                rows = [r for r in wide if r.key[time_var.code] == last]
                detail += f"; {time_var.values[-1]} has no numbers yet, using {last}"
        if res.aggregate_over:   # a dimension with no total the question did not name: its options summed in code
            rows = aggregate_rows(meta, rows, res.aggregate_over, lang)
            for code in res.aggregate_over:
                res.selections[code] = ["__all__"]
                chosen[meta.var(code).text] = [f"{'Samtals' if lang == 'is' else 'Total'} ({len(meta.var(code).values)} {'lagðir saman' if lang == 'is' else 'summed'})"]
            detail += f"; summed over {', '.join(meta.var(c).text for c in res.aggregate_over)}"
        if res.operation == "rank":   # categories with a number, largest first when only the top few were asked for
            rows = [r for r in rows if r.value is not None]
            if res.top:
                rows = sorted(rows, key=lambda r: -abs(r.value))[:res.top]
                res.selections[res.compare_dimension] = [r.key[res.compare_dimension] for r in rows]
                detail += f"; the largest {len(rows)}"
        steps.append(Step(name="fetch", ms=t.ms(), detail=detail))

        # Tables like THJ01000 mix indices, counts and yearly changes and give no unit; a chosen "volume change on
        # previous year" or "annual growth" is a percentage, so say so
        # (one dimension whose every chosen value is a percentage is enough: "Sex: Total, Unit: Unemployment rate")
        if meta.unit != "%" and any(res.selections.get(v.code) and
                                 all(_PERCENT.search(normalize(v.label_of(c))) for c in res.selections[v.code])
                                 for v in meta.variables if not v.time):
            meta = meta.model_copy(update={"unit": "%"})
        computation, facts = compute(meta, rows, res, lang)
        note = self._freshness_note(meta, res.selections, r.candidates, f"{sq.question} {original}", lang)
        if res.window_note:   # "the last 50 years" in a table that starts in 2003
            note = " ".join(x for x in (res.window_note[lang], note) if x)
        if res.place_note:    # "Egilsstaðir" in a table of the 2008 municipalities
            note = " ".join(x for x in (res.place_note[lang], note) if x)
        # the table named in the answer's language: chart titles, legends and the source card read it
        entry = self.catalog.get(meta.id)
        title = entry.title_is if lang == "is" and entry and entry.title_is else meta.title
        chart = choose_chart(meta, rows, res.selections, computation)
        return AnswerPart(
            sub_question=sq.question,
            table=TableRef(id=meta.id, title=title, url=table_url(meta.path, meta.id),
                           updated=entry.last_period if entry else ""),
            selection=chosen,
            rows=rows,
            unit=meta.unit,
            computation=computation,
            facts=facts,
            chart=chart.model_copy(update={"title": title}) if chart else None,
            note=note,
            ranking=r.ranking,
            drill=self._drill(meta, res, rows, lang, original if alone else sq.question),
        )

    @staticmethod
    def _drill(meta, res, rows, lang: str, asked: str) -> list[dict]:
        """Chips under the answer that ask this table again. Two kinds: categories listed that open further (each
        with the question that lists its own subcategories, read from the classification codes, so any coded table
        gets it), and categories the model chose that the question did not name, answered with the default instead
        ("Whole country" for a housing index): each chip asks the same question for that category."""
        out = []
        if res.breakdown:
            var = meta.var(res.breakdown)
            for r in rows:
                code = r.key.get(var.code)
                if code and children(var, code):
                    label = r.labels[var.text]
                    out.append({"label": label, "table_id": meta.id, "kind": "subcategories",
                                "question": f"Hverjir eru undirflokkar {label}?" if lang == "is" else f"What are the subcategories of {label}?"})
        for code, codes in res.alternatives.items():
            var = meta.var(code)
            for c in codes:
                label = var.label_of(c)
                out.append({"label": label, "table_id": meta.id, "kind": "alternative", "language": lang,
                            "question": f"{asked.rstrip('?. ')} — {label}"})
        return out

    async def _sibling_with(self, meta, candidates: list[str], period: str, question: str = "", original: str = ""):
        """A table in the same folder, among the candidates, whose figures reach the period asked for ("2017",
        "2026M09"). The model picks among those that have it (the check it uses on the hitlist), or says none:
        the one sharing the most options with the chosen table is not always the one on the same subject."""
        own = self.catalog.get(meta.id)
        if not own:
            return None
        siblings = [e for cid in candidates if (e := self.catalog.get(cid)) and e.id != meta.id and e.path == own.path]
        siblings.sort(key=lambda e: -len(set(own.values) & set(e.values)))
        with_period = []
        for e in siblings[:6]:
            try:
                m = await self.source.meta(e.id)
            except Exception:
                continue
            if any(v.time and any(x.startswith(period) or t.startswith(period) for x, t in zip(v.values, v.value_texts))
                   for v in m.variables):
                with_period.append((e, m))
        if not with_period:
            return None
        hit = await _check(self.llm, [e for e, _ in with_period], question or meta.title, original)
        return next((m for e, m in with_period if hit and e.id == hit.id), None)
        return None

    def _freshness_note(self, meta, selections: dict[str, list[str]], candidates: list[str], question: str, lang: str) -> str:
        """When nobody asked for a period and the newest one here is from an earlier year, say so, and name a table
        on the same subject with newer figures: same subject area in Statistics Iceland's folders, the same subfolder
        first. It may measure or break things down differently, so it is only named."""
        time_var = next((v for v in meta.variables if v.time), None)
        # a named year, or "last year" / "í fyrra": that period was asked for, so newer figures elsewhere are beside the point
        if not time_var or re.search(r"\b(?:18|19|20)\d\d\b", question) or re.search(_RELATIVE, normalize(question)):
            return ""
        latest = selections.get(time_var.code, [""])[-1]
        if not re.match(r"\d{4}", latest) or int(latest[:4]) >= date.today().year:
            return ""
        area = meta.path.split("/")[:3]
        pool = [e for e in self.catalog.entries
                if e.id != meta.id and e.path.split("/")[:3] == area and re.match(r"\d{4}", e.last_period or "")
                and int(e.last_period[:4]) >= date.today().year and period_end(e.last_period) > period_end(latest)]
        # the same breakdown first (most categories in common), never a table published ahead for indexing loans
        own = set(entry.values) if (entry := self.catalog.get(meta.id)) else set()
        pool = [e for e in pool if not re.search(_INDEXATION_TABLE, e.title_en, re.I)]
        newer = min(pool, key=lambda e: (-len(own & set(e.values)), e.path != meta.path, e.id not in candidates,
                                         -period_end(e.last_period)), default=None)
        if lang == "is":
            note = f"{latest} er nýjasta tímabilið í þessari töflu."
            if newer:
                note += f" Nýrri tölur, til {newer.last_period}, eru í {newer.id} ({newer.title_is or newer.title_en}), sem kann að skipta þeim öðruvísi niður."
        else:
            note = f"{latest} is the latest period in this table."
            if newer:
                note += f" Newer figures, to {newer.last_period}, are in {newer.id} ({newer.title_en}), which may break them down differently."
        return note


WRONG = {"en": "No answer was found for this question. Here is what I found that is connected:",
         "is": "Ekkert svar fannst við þessari spurningu. Hér er það sem ég fann sem tengist henni:"}
UNCERTAIN = {"en": "This may not answer the question exactly.",
             "is": "Þetta svarar spurningunni ef til vill ekki nákvæmlega."}


def _no_answer(lang: str, errors: list[Exception]) -> str:
    reason = str(errors[0]) if errors else ""
    if errors and isinstance(errors[0], LLMError) and "did not answer" in reason:   # a busy machine, not a missing table
        return ("Mállíkanið svaraði ekki í tæka tíð; tölvan er upptekin. Reyndu aftur eftir smá stund." if lang == "is" else
                "The language model took too long to answer; the computer is busy. Please try again in a moment.")
    if lang == "is":
        return f"Ég fann ekki svar við þessu í töflum Hagstofunnar. {reason}".strip()
    return f"I could not answer this from Statistics Iceland's tables. {reason}".strip()
