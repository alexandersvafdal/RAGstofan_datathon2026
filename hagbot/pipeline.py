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
from .steps.chart import choose_chart, comparison_chart
from .steps.compose import compose
from .steps.compute import compute
from .steps.followup import looks_like_followup, rewrite
from .steps.plan import Plan, SubQuestion, plan
from .steps.ratio import POPULATION_TABLE, detect_ratio, ratio_parts
from .steps.retrieve import period_end, retrieve
from .steps.tiered import retrieve_tiered
from .steps.select import select
from .steps.suggest import suggest
from .text import detect_language, normalize

# A label that is itself a percentage: "Annual change, %", "Unemployment rate", "Percentage of GDP". A % after a
# number is a product's description, not its unit ("wine of alcoholic strength > 8.5%"), so it does not count.
_PERCENT = re.compile(r"(?<![\d.])%|\bpercent|prosent|hlutfall|volume change|change on previous|change from previous|"
                      r"annual growth|annual rate of change|\b(un)?employment rate|activity rate|participation rate|"
                      r"vacancy rate|household rate|rate of individuals|overburden rate|magnbreyting|breyting fra fyrra|"
                      r"arleg fjolgun|atvinnuleysi")
_RELATIVE = r"\blast (year|month|quarter)\b|\bprevious year\b|\bsidasta (ar|manud|arsfjord)|\bi fyrra\b|\bfyrra ar"

CLARIFY = {
    "en": "Several tables could answer this. Which one do you mean?",
    "is": "Fleiri en ein tafla gæti svarað þessu. Hverja áttu við?",
}


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
        model, reasoning = (settings.fast_model, "none") if req.fast else (req.model, req.reasoning)
        if model or reasoning or req.temperature is not None:
            other = copy.copy(self)
            other.llm = self.llm.with_options(model, req.temperature, reasoning)
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
            ratio = None if req.table_id else detect_ratio(req.question)
            forced: list[str | None]
            if req.table_id:  # the user already chose a table: skip planning
                p = Plan(sub_questions=[SubQuestion(question=req.question, search_query=req.question)])
                forced = [req.table_id]
            elif ratio:  # "X per inhabitant": X from its own table, divided by the population in code
                t = Timer()
                num = (await plan(self.llm, ratio.numerator)).sub_questions[0]
                pop = SubQuestion(question=ratio.population_question(lang), search_query="population 1 January")
                p = Plan(sub_questions=[num, pop])
                forced = [None, POPULATION_TABLE]
                steps.append(Step(name="plan", ms=t.ms(), detail=f"{num.question} | {pop.question}"))
            else:
                t = Timer()
                p = await plan(self.llm, req.question)
                forced = [None] * len(p.sub_questions)
                steps.append(Step(name="plan", ms=t.ms(), detail=" | ".join(s.question for s in p.sub_questions)))

            results = await asyncio.gather(
                *(self._answer_part(sq, ratio.numerator if ratio and i == 0 else req.question, lang, f, steps,
                                    alone=len(p.sub_questions) == 1 or (ratio is not None and i == 0))
                  for i, (sq, f) in enumerate(zip(p.sub_questions, forced))),
                return_exceptions=True,
            )
            clar = next((r for r in results if isinstance(r, NeedsClarification)), None)
            if clar:
                ans.status, ans.clarification, ans.text = "clarify", clar.clarification, clar.clarification.message
                return ans
            errors = [r for r in results if isinstance(r, Exception)]
            parts = await self._merge_series([r for r in results if isinstance(r, AnswerPart)])
            if ratio and len(parts) == 2 and (rp := ratio_parts(ratio, parts[0], parts[1], lang)):
                parts = list(rp)
                steps.append(Step(name="compute", ms=0, detail=rp[0].computation.expression))
            for e in errors:
                steps.append(Step(name="error", ms=0, detail=f"{type(e).__name__}: {e}"))
            if not parts:
                ans.text = _no_answer(lang, errors)
                return ans

            t = Timer()
            ans.text, ans.text_source = await compose(self.llm, req.question, parts, lang)
            steps.append(Step(name="compose", ms=t.ms(), detail=ans.text_source))
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
                "chart": choose_chart(meta, rows, selections, None),
            }))
        return out

    async def _answer_part(self, sq: SubQuestion, original: str, lang: str, forced_table: str | None, steps: list[Step],
                           alone: bool = True) -> AnswerPart:
        t = Timer()
        # a multi-part question's full wording would pull every part toward the same tables
        if settings.retrieval == "tiered":
            r = await retrieve_tiered(self.llm, self.catalog, sq.question, sq.search_query,
                                      settings.retrieve_top_k, forced_table, original if alone else "")
        else:
            r = await retrieve(self.llm, self.catalog, sq.question, sq.search_query, settings.retrieve_top_k, forced_table,
                               original if alone else "")
        steps.append(Step(name="retrieve", ms=t.ms(),
                          detail=f"{r.table.id} from [{', '.join(r.candidates)}]" + (" (ambiguous)" if r.ambiguous else "")
                                 + (f" via {r.via}" if r.via else "") + (f" · votes {r.votes}" if r.votes else "")
                                 + (f" · concepts: {', '.join(r.concepts)}" if r.concepts else "")))
        if r.ambiguous:
            opts = [ClarifyOption(label=e.title_en if lang == "en" else (e.title_is or e.title_en), table_id=e.id)
                    for e in [r.table, *r.alternatives]]
            raise NeedsClarification(Clarification(message=CLARIFY[lang], options=opts))

        t = Timer()
        meta = await self.source.meta(r.table.id)
        res = await select(self.llm, meta, sq.question, original)
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
        steps.append(Step(name="fetch", ms=t.ms(), detail=detail))

        # Tables like THJ01000 mix indices, counts and yearly changes and give no unit; a chosen "volume change on
        # previous year" or "annual growth" is a percentage, so say so
        # (one dimension whose every chosen value is a percentage is enough: "Sex: Total, Unit: Unemployment rate")
        if not meta.unit and any(res.selections.get(v.code) and
                                 all(_PERCENT.search(normalize(v.label_of(c))) for c in res.selections[v.code])
                                 for v in meta.variables if not v.time):
            meta = meta.model_copy(update={"unit": "%"})
        computation, facts = compute(meta, rows, res, lang)
        note = self._freshness_note(meta, res.selections, r.candidates, f"{sq.question} {original}", lang)
        if res.window_note:   # "the last 50 years" in a table that starts in 2003
            note = " ".join(x for x in (res.window_note[lang], note) if x)
        return AnswerPart(
            sub_question=sq.question,
            table=TableRef(id=meta.id, title=meta.title, url=table_url(meta.path, meta.id)),
            selection=chosen,
            rows=rows,
            unit=meta.unit,
            computation=computation,
            facts=facts,
            chart=choose_chart(meta, rows, res.selections, computation),
            note=note,
            ranking=r.ranking,
        )

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
        newer = min(pool, key=lambda e: (e.path != meta.path, e.id not in candidates, -period_end(e.last_period)), default=None)
        if lang == "is":
            note = f"{latest} er nýjasta tímabilið í þessari töflu."
            if newer:
                note += f" Nýrri tölur, til {newer.last_period}, eru í {newer.id} ({newer.title_is or newer.title_en}), sem kann að skipta þeim öðruvísi niður."
        else:
            note = f"{latest} is the latest period in this table."
            if newer:
                note += f" Newer figures, to {newer.last_period}, are in {newer.id} ({newer.title_en}), which may break them down differently."
        return note


def _no_answer(lang: str, errors: list[Exception]) -> str:
    reason = str(errors[0]) if errors else ""
    if errors and isinstance(errors[0], LLMError) and "did not answer" in reason:   # a busy machine, not a missing table
        return ("Mállíkanið svaraði ekki í tæka tíð; tölvan er upptekin. Reyndu aftur eftir smá stund." if lang == "is" else
                "The language model took too long to answer; the computer is busy. Please try again in a moment.")
    if lang == "is":
        return f"Ég fann ekki svar við þessu í töflum Hagstofunnar. {reason}".strip()
    return f"I could not answer this from Statistics Iceland's tables. {reason}".strip()
