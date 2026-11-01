"""Orchestrator: plan → (per sub-question, in parallel: retrieve → select → fetch → compute → chart) → compose."""

import asyncio
import copy
import re
from datetime import date
from collections.abc import Callable, Collection

from .catalog import Catalog
from .config import settings
from .contexts import context_for, validate_combination, validate_population_ratio
from .claims import build_claims
from .llm import LLM, Timer
from .outcomes import AnswerFailure, RETRYABLE_TABLE_FAILURES
from .schemas import (Answer, AnswerIssue, AnswerPart, AskRequest, Clarification, ClarifyOption, Step, TableAttempt, TableRef)
from .semantics import fingerprint, validate_specification
from .specification import QuestionSpec, Requirement
from .sources import DataSource, TableMeta, make_source, table_url
from .hierarchy import children
from .steps.chart import choose_chart, comparison_chart
from .steps.compose import compose
from .steps.compute import compute, validate_rows
from .steps.constraints import has_requested_period
from .steps.followup import looks_like_followup, rewrite
from .steps.plan import Plan, SubQuestion, merge_period_parts, merge_population_sum, plan
from .steps.ratio import POPULATION_TABLE, detect_ratio, difference_parts, ratio_parts, share_parts, stored_rate
from .steps.retrieve import _INDEXATION_TABLE, Retrieved, RetrievalCache, period_end
from .steps.tiered import retrieve_tiered
from .steps.select import Resolved, guess_op, select
from .steps.suggest import suggest
from .text import detect_language, normalize

# A label that is itself a percentage: "Annual change, %", "Unemployment rate", "Percentage of GDP". A % after a
# number is a product's description, not its unit ("wine of alcoholic strength > 8.5%"), so it does not count.
_PERCENT = re.compile(r"(?<![\d.])%|\bpercent|prosent|hlutfall|volume change|change on previous|change from previous|"
                      r"annual growth|annual rate of change|\b(un)?employment rate|activity rate|participation rate|"
                      r"vacancy rate|household rate|rate of individuals|overburden rate|magnbreyting|breyting fra fyrra|"
                      r"arleg fjolgun|atvinnuleysi")
_RELATIVE = r"\blast (year|month|quarter)\b|\bprevious year\b|\bsidasta (ar|manud|arsfjord)|\bi fyrra\b|\bfyrra ar"


def _result_unit(meta: TableMeta, selections: dict[str, list[str]]) -> str:
    if any(selections.get(v.code) and all(_PERCENT.search(normalize(v.label_of(c))) for c in selections[v.code])
           for v in meta.variables if not v.time):
        return "%"
    return meta.unit if meta.unit_source == "source" else ""

CLARIFY = {
    "en": "Several tables could answer this. Which one do you mean?",
    "is": "Fleiri en ein tafla gæti svarað þessu. Hverja áttu við?",
}


class NeedsClarification(Exception):
    def __init__(self, clarification: Clarification, attempts: list[TableAttempt] | None = None):
        self.clarification = clarification
        self.attempts = list(attempts or [])


class StepLog(list):
    """The answer's step list, which also reports each step the moment it is recorded (for live progress)."""

    def __init__(self, on_step: Callable[[Step], None] | None = None):
        super().__init__()
        self.on_step = on_step

    def append(self, step: Step) -> None:
        super().append(step)
        if self.on_step:
            self.on_step(step)


def _language(req: AskRequest) -> str:
    detected = detect_language(req.question)
    return "is" if detected == "is" else req.language or detected


class Pipeline:
    def __init__(self, llm: LLM | None = None, catalog: Catalog | None = None, source: DataSource | None = None):
        self.llm = llm or LLM()
        self.catalog = catalog or Catalog.load(settings.catalog_path)
        self.source = source or make_source(self.catalog)

    async def answer(self, req: AskRequest, on_step: Callable[[Step], None] | None = None) -> Answer:
        """Answer with the model asked for, or the default. Another model runs on a copy of this pipeline, so
        concurrent questions on different models do not disturb each other."""
        model, reasoning = (settings.fast_model, "none") if req.fast else (req.model, req.reasoning)
        worker = self
        if hasattr(self.llm, "with_options"):
            other = copy.copy(self)
            other.llm = self.llm.with_options(model, req.temperature, reasoning)
            worker = other
        elapsed, steps, before = Timer(), [], worker.llm.calls
        def record(step):
            steps.append(step)
            if on_step:
                on_step(step)
        try:
            return await asyncio.wait_for(worker._answer(req, record), timeout=float(settings.answer_timeout_s))
        except TimeoutError:
            failure = AnswerFailure("error", "answer_timeout")
            record(Step(name="error", ms=0, detail="AnswerFailure: answer_timeout"))
            return Answer(question=req.question, language=_language(req), status="error",
                          text=failure.message(_language(req)),
                          issues=[AnswerIssue(status="error", code=failure.code, message=failure.message(_language(req)))],
                          steps=steps, total_ms=elapsed.ms(), llm_calls=worker.llm.calls - before, model=worker.llm.model)

    async def suggest(self, ans: Answer) -> list[str]:
        """Follow-up questions for an answer already shown: asked for afterwards, so they never delay it."""
        if ans.status != "answered" or not ans.parts:
            return []
        metas = await asyncio.gather(*(self.source.meta(p.table.id) for p in ans.parts), return_exceptions=True)
        pairs = [(p, m) for p, m in zip(ans.parts, metas) if not isinstance(m, Exception)]
        return await suggest(self.llm, ans.question, ans.text, [p for p, _ in pairs], [m for _, m in pairs], ans.language)

    async def _answer(self, req: AskRequest, on_step: Callable[[Step], None] | None = None) -> Answer:
        lang = _language(req)
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
            # "GDP per capita": many tables publish the rate itself, so read it rather than divide
            stored = await asyncio.to_thread(stored_rate, self.catalog, ratio, req.question) if ratio else None
            forced: list[str | None]
            target = req.table_id or (stored.id if stored else None)
            if target:
                entry = self.catalog.get(target)
                if entry is None:
                    raise AnswerFailure("unavailable", "unknown_table", target)
                # Choosing a table narrows retrieval; it does not waive interpreting or validating the question.
                t = Timer()
                context = f"Selected table {entry.id}: {entry.title_en}"
                p = await plan(self.llm, req.question, context=context)
                _check_plan(p, req.question, context)
                p = merge_population_sum(p, req.question)
                _check_plan(p, req.question, context)
                forced = [target] * len(p.sub_questions)
                if stored:
                    ratio = None  # read the published rate, after the same specification/evidence checks
                steps.append(Step(name="plan", ms=t.ms(), detail=" | ".join(s.question for s in p.sub_questions)))
            elif ratio:  # "X per inhabitant": X from its own table, divided by the population in code
                if len(ratio.places) > 1 or ratio.qualified_population:
                    raise AnswerFailure("clarify", "ambiguous_denominator_population")
                t = Timer()
                numerator_plan = await plan(self.llm, ratio.numerator)
                _check_plan(numerator_plan, ratio.numerator)
                if len(numerator_plan.sub_questions) != 1:
                    raise AnswerFailure("out_of_scope", "multiple_ratio_numerators")
                num = numerator_plan.sub_questions[0]
                place = ratio.place or "Iceland"
                requirements = [Requirement(facet="measure", value="population", origin="context", quote="people"),
                                Requirement(facet="geography", value=place, origin="context", quote=place)]
                requirements.append(Requirement(facet="period", value=ratio.year or "latest",
                                                origin="context" if ratio.year else "default", quote=ratio.year or ""))
                pop = SubQuestion(question=ratio.population_question(lang), search_query=ratio.population_search(),
                                  specification=QuestionSpec(requirements=requirements, operation="value"))
                p = Plan(sub_questions=[num, pop])
                forced = [None, None if ratio.place else POPULATION_TABLE]  # a named place: find its population table
                steps.append(Step(name="plan", ms=t.ms(), detail=f"{num.question} | {pop.question}"))
            else:
                t = Timer()
                p = await plan(self.llm, req.question)
                _check_plan(p, req.question)
                p = merge_period_parts(p, req.question)
                p = merge_population_sum(p, req.question)
                _check_plan(p, req.question)
                forced = [None] * len(p.sub_questions)
                steps.append(Step(name="plan", ms=t.ms(), detail=" | ".join(s.question for s in p.sub_questions)))

            results = await asyncio.gather(
                *(self._answer_part(sq, ratio.numerator if ratio and i == 0 else req.question, lang, f, steps,
                                    alone=len(p.sub_questions) == 1 or (ratio is not None and i == 0))
                  for i, (sq, f) in enumerate(zip(p.sub_questions, forced))),
                return_exceptions=True,
            )
            if any(isinstance(r, asyncio.CancelledError) for r in results):
                raise asyncio.CancelledError
            errors = [(sq.question, r) for sq, r in zip(p.sub_questions, results) if isinstance(r, Exception)]
            if errors:
                for _, e in errors:
                    steps.append(Step(name="stop", ms=0, detail=f"{type(e).__name__}: {e}"))
                return _stop(ans, errors, len(p.sub_questions))
            if len(results) != len(p.sub_questions) or not all(isinstance(r, AnswerPart) and r.facts for r in results):
                raise AnswerFailure("unverified", "incomplete_answer")
            # Complete the calculation before merging charts: raw operands are not an answer to a ratio question.
            parts = results
            if ratio:
                rp = ratio_parts(ratio, parts[0], parts[1], lang) if len(parts) == 2 else None
                if rp is None:
                    raise AnswerFailure("unavailable", "incomplete_ratio")
                validate_population_ratio(parts[0], parts[1], ratio.year, dated=ratio.dated)
                parts = list(rp)
                steps.append(Step(name="compute", ms=0, detail=rp[0].computation.expression))
            elif len(parts) == 2:
                operation = guess_op(req.question)
                if p.combination:
                    combo = p.combination
                    first, second = parts[combo.first], parts[combo.second]
                    validate_combination(first, second, combo.operation)
                    combined = (share_parts(first, second, lang, ordered=True) if combo.operation == "share" else
                                difference_parts(first, second, lang, operation=combo.operation, ordered=True))
                    if combined is None:
                        raise AnswerFailure("unavailable", "incomplete_calculation", combo.operation)
                    parts = list(combined)
                    steps.append(Step(name="compute", ms=0, detail=parts[0].computation.expression))
                elif operation in ("share", "difference", "pct_change") and all(len(part.facts) == 1 and not part.computation for part in parts):
                    raise AnswerFailure("unverified", "missing_combination_roles")
            parts = await self._merge_series(parts)

            t = Timer()
            ans.claims = build_claims(parts, lang)
            ans.text, ans.text_source = await compose(self.llm, req.question, parts, lang, claims=ans.claims)
            steps.append(Step(name="compose", ms=t.ms(), detail=ans.text_source))
            # Commit answer content only after all required work has succeeded.
            comparison = comparison_chart(parts, lang)
            ans.parts, ans.status, ans.comparison = parts, "answered", comparison
            ans.sources = list({p.table.id: p.table for p in parts}.values())
            return ans
        except Exception as e:  # the API always returns an Answer, never a stack trace
            steps.append(Step(name="error", ms=0, detail=f"{type(e).__name__}: {e}"))
            return _stop(ans, [(req.question, e)])
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
            context = first.context.model_copy(deep=True) if first.context else None
            if context:
                if any(p.context is None or p.context.definitions != context.definitions for p in group):
                    context = None
                else:
                    context.periods = sorted({period for p in group for period in p.context.periods})
                    context.geography = sorted({place for p in group for place in p.context.geography})
                    context.population = {axis: sorted({value for p in group for value in p.context.population.get(axis, ["total"])})
                                          for axis in {axis for p in group for axis in p.context.population}}
            out.append(first.model_copy(update={
                "sub_question": " | ".join(p.sub_question for p in group),
                "rows": rows,
                "facts": [f for p in group for f in p.facts],
                "verifications": [v for p in group for v in p.verifications],
                "attempts": [a for p in group for a in p.attempts],
                "context": context,
                "selection": {k: list(dict.fromkeys(v for p in group for v in p.selection.get(k, []))) for k in first.selection},
                "chart": (c := choose_chart(meta, rows, selections, None)) and c.model_copy(update={"title": first.table.title}),
            }))
        return out

    async def _verified_query(self, sq: SubQuestion, original: str, lang: str, forced_table: str | None,
                              steps: list[Step], alone: bool) -> tuple[Retrieved, TableMeta, Resolved, list[TableAttempt]]:
        """Try a bounded number of distinct candidates without changing the specification or fetching cells."""
        rejected, attempts, cache = set(), [], RetrievalCache()
        limit = 1 if forced_table else settings.retrieve_max_attempts
        question_sha = fingerprint(sq.specification.model_dump())
        retriever = retrieve_tiered
        # a multi-part question's full wording would pull every part toward the same tables
        for _ in range(limit):
            t = Timer()
            try:
                r = await retriever(self.llm, self.catalog, sq.question, sq.search_query, settings.retrieve_top_k,
                                    forced_table, original if alone else "", excluded=frozenset(rejected), cache=cache)
            except AnswerFailure as failure:
                failure.attempts = list(attempts)
                if attempts and failure.code in {"no_candidates", "no_verified_table"}:
                    raise AnswerFailure("unverified", "no_compatible_candidate", attempts=attempts) from failure
                raise
            if r.table.id in rejected:
                raise AnswerFailure("unverified", "repeated_candidate", r.table.id, attempts=attempts)
            steps.append(Step(name="retrieve", ms=t.ms(),
                              detail=f"{r.table.id} from [{', '.join(r.candidates)}]" + (" (ambiguous)" if r.ambiguous else "")
                                     + (f" via {r.via}" if r.via else "") + (f" · votes {r.votes}" if r.votes else "")
                                     + (f" · concepts: {', '.join(r.concepts)}" if r.concepts else "")))
            if r.ambiguous:
                opts = [ClarifyOption(label=e.title_en if lang == "en" else (e.title_is or e.title_en), table_id=e.id)
                        for e in [r.table, *r.alternatives]]
                raise NeedsClarification(Clarification(message=CLARIFY[lang], options=opts), attempts)

            t, metadata_sha = Timer(), ""
            try:
                meta = await self.source.meta(r.table.id)
                metadata_sha = fingerprint(meta.model_dump())
                if meta.id != r.table.id:
                    raise AnswerFailure("unverified", "mismatched_table_metadata", r.table.id)
                filter_question = f"{original} {sq.question}" if alone else sq.question
                res = await select(self.llm, meta, sq.question, original, filter_question=filter_question,
                                   specification=sq.specification)
                if not _result_unit(meta, res.selections):
                    raise AnswerFailure("unverified", "unknown_unit", meta.id)
                if res.verification is None:
                    raise AnswerFailure("unverified", "missing_compatibility")
            except AnswerFailure as failure:
                attempts.append(TableAttempt(table_id=r.table.id, status="rejected", code=failure.code,
                                             detail=failure.detail[:512], metadata_sha256=metadata_sha, question_sha256=question_sha, via=r.via))
                failure.attempts = list(attempts)
                if forced_table or failure.code not in RETRYABLE_TABLE_FAILURES or failure.status not in ("unavailable", "unverified"):
                    raise
                rejected.add(r.table.id)
                if len(attempts) == limit:
                    raise AnswerFailure("unverified", "candidate_limit_reached", attempts=attempts) from failure
                steps.append(Step(name="retry", ms=t.ms(), detail=f"{r.table.id} rejected: {failure.code}; checking another table"))
                continue
            except Exception as error:
                attempts.append(TableAttempt(table_id=r.table.id, status="error", code="technical_failure",
                                             metadata_sha256=metadata_sha, question_sha256=question_sha, via=r.via))
                raise AnswerFailure("error", "technical_failure", f"{type(error).__name__}: {error}", attempts=attempts) from error

            attempts.append(TableAttempt(table_id=r.table.id, status="verified", metadata_sha256=metadata_sha,
                                         question_sha256=question_sha, via=r.via))
            chosen = {meta.var(c).text: [meta.var(c).label_of(v) for v in vals] for c, vals in res.selections.items()}
            steps.append(Step(name="select", ms=t.ms(),
                              detail=f"{res.operation} {chosen}; supported {sum(c.verdict == 'supported' for c in res.verification.checks)} metadata requirements"
                                     + (f" notes: {res.notes}" if res.notes else "")))
            return r, meta, res, attempts
        raise AnswerFailure("unverified", "candidate_limit_reached", attempts=attempts)

    async def _answer_part(self, sq: SubQuestion, original: str, lang: str, forced_table: str | None, steps: list[Step],
                           alone: bool = True) -> AnswerPart:
        r, meta, res, attempts = await self._verified_query(sq, original, lang, forced_table, steps, alone)
        t = Timer()
        filter_question = f"{original} {sq.question}" if alone else sq.question
        async def fetch(selections):
            if hasattr(self.source, "query_checked"):
                return await self.source.query_checked(meta, selections)
            return await self.source.query(meta.id, selections)
        rows = await fetch(res.selections)
        detail = f"{len(rows)} rows"
        # A table's time dimension often lists a period before its numbers exist ("2026" with ".." until the year
        # ends). When the question named no period, the latest period with numbers is the answer.
        time_var = next((v for v in meta.variables if v.time), None)
        if (time_var and rows and all(r.value is None for r in rows) and res.selections.get(time_var.code) == [time_var.values[-1]]
                and not has_requested_period(filter_question)):
            wide = await fetch({**res.selections, time_var.code: time_var.values})
            filled = [r.key[time_var.code] for r in wide if r.value is not None]
            if filled:
                last = max(filled, key=time_var.values.index)
                res.selections[time_var.code] = [last]
                rows = [r for r in wide if r.key[time_var.code] == last]
                detail += f"; {time_var.values[-1]} has no numbers yet, using {last}"
        if res.operation == "rank":   # categories with a number, largest first when only the top few were asked for
            validate_rows(meta, rows, res.selections)
            rows = [r for r in rows if r.value is not None]
            if res.top:
                rows = sorted(rows, key=lambda r: -abs(r.value))[:res.top]
                res.selections[res.compare_dimension] = [r.key[res.compare_dimension] for r in rows]
                detail += f"; the largest {len(rows)}"
        steps.append(Step(name="fetch", ms=t.ms(), detail=detail))

        # Tables like THJ01000 mix indices, counts and yearly changes and give no unit; a chosen "volume change on
        # previous year" or "annual growth" is a percentage, so say so
        # (one dimension whose every chosen value is a percentage is enough: "Sex: Total, Unit: Unemployment rate")
        meta = meta.model_copy(update={"unit": _result_unit(meta, res.selections), "unit_source": "source"})
        computation, facts = compute(meta, rows, res, lang)
        if not facts or (res.operation not in ("value", "series", "rank") and computation is None):
            raise AnswerFailure("unavailable", "incomplete_calculation", res.operation)
        note = self._freshness_note(meta, res.selections, r.candidates, f"{sq.question} {original}", lang,
                                    excluded={a.table_id for a in attempts if a.status == "rejected"})
        if res.window_note:   # "the last 50 years" in a table that starts in 2003
            note = " ".join(x for x in (res.window_note[lang], note) if x)
        if res.place_note:    # "Egilsstaðir" in a table of the 2008 municipalities
            note = " ".join(x for x in (res.place_note[lang], note) if x)
        # the table named in the answer's language: chart titles, legends and the source card read it
        entry = self.catalog.get(meta.id)
        title = entry.title_is if lang == "is" and entry and entry.title_is else meta.title
        chart = choose_chart(meta, rows, res.selections, computation)
        verification = res.verification.model_copy(update={"executed_selection_sha256": fingerprint(res.selections)})
        if verification.executed_selection_sha256 != verification.selection_sha256:
            verification = verification.model_copy(update={"amendments": [*verification.amendments, detail]})
        return AnswerPart(
            sub_question=sq.question,
            table=TableRef(id=meta.id, title=title, url=table_url(meta.path, meta.id),
                           updated=next((v.values[-1] for v in meta.variables if v.time and v.values), ""),
                           source_kind=meta.source_kind, retrieved_at=meta.retrieved_at, snapshot_at=meta.snapshot_at),
            selection={meta.var(c).text: [meta.var(c).label_of(v) for v in vals] for c, vals in res.selections.items()},
            rows=rows,
            unit=meta.unit,
            computation=computation,
            facts=facts,
            verifications=[verification],
            context=context_for(meta, res.selections, sq.specification),
            attempts=attempts,
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

    async def _sibling_with(self, meta, candidates: list[str], period: str):
        """A table in the same folder, among the candidates, whose figures reach the period asked for: the one
        sharing the most options with the chosen table first. None when no neighbour has it."""
        own = self.catalog.get(meta.id)
        if not own:
            return None
        siblings = [e for cid in candidates if (e := self.catalog.get(cid)) and e.id != meta.id and e.path == own.path]
        siblings.sort(key=lambda e: -len(set(own.values) & set(e.values)))
        for e in siblings[:3]:
            try:
                m = await self.source.meta(e.id)
            except Exception:
                continue
            if any(v.time and period in v.values for v in m.variables):
                return m
        return None

    def _freshness_note(self, meta, selections: dict[str, list[str]], candidates: list[str], question: str, lang: str,
                        excluded: Collection[str] = ()) -> str:
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
                if e.id != meta.id and e.id not in excluded and e.path.split("/")[:3] == area and re.match(r"\d{4}", e.last_period or "")
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


def _check_plan(p: Plan, question: str, context: str = "") -> None:
    if p.outcome != "lookup":
        raise AnswerFailure(p.outcome, "ambiguous_question" if p.outcome == "clarify" else "unsupported_request")
    for sub_question in p.sub_questions:
        validate_specification(sub_question.specification, question, context)


def _stop(ans: Answer, errors: list[tuple[str, Exception]], expected_parts: int = 1) -> Answer:
    """No success payload is published when any required part fails."""
    issues = []
    for question, e in errors:
        if isinstance(e, NeedsClarification):
            issues.append(AnswerIssue(status="clarify", code="ambiguous_table", message=e.clarification.message,
                                      sub_question=question, attempts=e.attempts))
        else:
            failure = e if isinstance(e, AnswerFailure) else AnswerFailure("error", "technical_failure")
            issues.append(AnswerIssue(status=failure.status, code=failure.code,
                                      message=failure.message(ans.language), sub_question=question, attempts=failure.attempts))
    priority = {s: i for i, s in enumerate(("error", "out_of_scope", "unverified", "unavailable", "clarify"))}
    first = min(issues, key=lambda i: priority[i.status])
    ans.status, ans.issues, ans.text = first.status, issues, first.message
    if expected_parts > 1:
        prefix = ("Ég gat ekki svarað öllum hlutum spurningarinnar. " if ans.language == "is" else
                  "I could not answer every required part of the question. ")
        ans.text = prefix + ans.text
    ans.parts, ans.sources, ans.claims, ans.comparison, ans.text_source = [], [], [], None, "none"
    if ans.status == "clarify":
        clar = next((e.clarification for _, e in errors if isinstance(e, NeedsClarification)), None)
        ans.clarification = clar or Clarification(message=ans.text, options=[])
    else:
        ans.clarification = None
    return ans
