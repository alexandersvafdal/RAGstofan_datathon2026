"""Orchestrator: plan → (per sub-question, in parallel: retrieve → select → fetch → compute → chart) → compose."""

import asyncio
import re
from collections.abc import Callable

from .catalog import Catalog
from .config import settings
from .llm import LLM, Timer
from .schemas import (Answer, AnswerPart, AskRequest, Clarification, ClarifyOption, Step, TableRef)
from .sources import DataSource, make_source, table_url
from .steps.chart import choose_chart
from .steps.compose import compose
from .steps.compute import compute
from .steps.plan import Plan, SubQuestion, plan
from .steps.retrieve import retrieve
from .steps.select import select
from .text import detect_language

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
        lang = req.language or detect_language(req.question)
        total, steps = Timer(), StepLog(on_step)
        calls_before = self.llm.calls
        ans = Answer(question=req.question, language=lang, status="error", text="", model=self.llm.model)
        try:
            if req.table_id:  # the user already chose a table: skip planning
                p = Plan(sub_questions=[SubQuestion(question=req.question, search_query=req.question)])
            else:
                t = Timer()
                p = await plan(self.llm, req.question)
                steps.append(Step(name="plan", ms=t.ms(), detail=" | ".join(s.question for s in p.sub_questions)))

            results = await asyncio.gather(
                *(self._answer_part(sq, req.question, lang, req.table_id, steps, alone=len(p.sub_questions) == 1)
                  for sq in p.sub_questions),
                return_exceptions=True,
            )
            clar = next((r for r in results if isinstance(r, NeedsClarification)), None)
            if clar:
                ans.status, ans.clarification, ans.text = "clarify", clar.clarification, clar.clarification.message
                return ans
            errors = [r for r in results if isinstance(r, Exception)]
            parts = [r for r in results if isinstance(r, AnswerPart)]
            for e in errors:
                steps.append(Step(name="error", ms=0, detail=f"{type(e).__name__}: {e}"))
            if not parts:
                ans.text = _no_answer(lang, errors)
                return ans

            t = Timer()
            ans.text, ans.text_source = await compose(self.llm, req.question, parts, lang)
            steps.append(Step(name="compose", ms=t.ms(), detail=ans.text_source))
            ans.parts, ans.status = parts, "answered"
            ans.sources = list({p.table.id: p.table for p in parts}.values())
            return ans
        except Exception as e:  # the API always returns an Answer, never a stack trace
            steps.append(Step(name="error", ms=0, detail=f"{type(e).__name__}: {e}"))
            ans.text = _no_answer(lang, [e])
            return ans
        finally:
            ans.steps, ans.total_ms, ans.llm_calls = list(steps), total.ms(), self.llm.calls - calls_before

    async def _answer_part(self, sq: SubQuestion, original: str, lang: str, forced_table: str | None, steps: list[Step],
                           alone: bool = True) -> AnswerPart:
        t = Timer()
        # a multi-part question's full wording would pull every part toward the same tables
        r = await retrieve(self.llm, self.catalog, sq.question, sq.search_query, settings.retrieve_top_k, forced_table,
                           original if alone else "")
        steps.append(Step(name="retrieve", ms=t.ms(),
                          detail=f"{r.table.id} from [{', '.join(r.candidates)}]" + (" (ambiguous)" if r.ambiguous else "")))
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

        computation, facts = compute(meta, rows, res, lang)
        return AnswerPart(
            sub_question=sq.question,
            table=TableRef(id=meta.id, title=meta.title, url=table_url(meta.path, meta.id)),
            selection=chosen,
            rows=rows,
            unit=meta.unit,
            computation=computation,
            facts=facts,
            chart=choose_chart(meta, rows, res.selections, computation),
        )


def _no_answer(lang: str, errors: list[Exception]) -> str:
    reason = str(errors[0]) if errors else ""
    if lang == "is":
        return f"Ég fann ekki svar við þessu í töflum Hagstofunnar. {reason}".strip()
    return f"I could not answer this from Statistics Iceland's tables. {reason}".strip()
