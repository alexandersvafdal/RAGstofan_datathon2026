"""Offline regressions: python -B -m unittest discover -s tests -v.

Scripted models and in-memory sources exercise production control flow without API or embedding requests.
"""

import json
import unittest
from datetime import date
from unittest.mock import AsyncMock, patch

from pydantic import ValidationError

from hagbot.catalog import CatalogEntry
from hagbot.outcomes import AnswerFailure
from hagbot.pipeline import NeedsClarification, Pipeline
from hagbot.schemas import Answer, AnswerPart, AskRequest, Clarification, Fact, Row, TableRef
from hagbot.sources import TableMeta, Variable
from hagbot.steps.compose import Written
from hagbot.steps.compute import compute
from hagbot.steps.constraints import RequestedFilter
from hagbot.steps.plan import Plan, SubQuestion
from hagbot.steps.retrieve import Pick, retrieve
from hagbot.steps.select import Resolved, Selection, resolve, vote_selection
from hagbot.steps.tiered import Subjects, Verdict, retrieve_tiered


def selection(**fields):
    fields.setdefault("requested_filters", [])
    return Selection(**fields)


def entry(table_id="TEST001", last="2025"):
    return CatalogEntry(id=table_id, path="People/population", title_en="Population",
                        first_period="2024", last_period=last)


def meta(periods=None, categories=True):
    variables = [Variable(code="Year", text="Year", values=periods or ["2024", "2025"],
                          value_texts=periods or ["2024", "2025"], time=True)]
    if categories:
        variables += [
            Variable(code="Area", text="Municipality", values=["0", "1", "2"],
                     value_texts=["Total", "Reykjavík", "Akureyri"]),
            Variable(code="Sex", text="Sex", values=["0", "1", "2"], value_texts=["Total", "Males", "Females"]),
        ]
    return TableMeta(id="TEST001", title="Population", path="People/population", unit="persons", variables=variables)


class ScriptedLLM:
    model = "scripted-offline"

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = 0
        self.prompts = []

    async def complete_json(self, system, user, schema, **kwargs):
        self.calls += 1
        self.prompts.append((system, user))
        if not self.replies:
            raise AssertionError(f"Unexpected {schema.__name__} call")
        reply = self.replies.pop(0)
        if not isinstance(reply, schema):
            raise AssertionError(f"Expected {type(reply).__name__}, got {schema.__name__}")
        return reply


class CatalogStub:
    def __init__(self, *entries):
        self.entries = list(entries) or [entry()]
        self.by_id = {e.id: e for e in self.entries}
        self.queries = []

    def get(self, table_id):
        return self.by_id.get(table_id)

    def search(self, query, k):
        self.queries.append(query)
        return [(e, 1.0) for e in self.entries][:k]

    def rankings(self, query, depth):
        self.queries.append(query)
        return {"bm25": list(self.by_id)[:depth]}


class SourceStub:
    def __init__(self, table_meta=None, values=None):
        self.table_meta = table_meta or meta(categories=False)
        values = values if values is not None else {"2024": 20, "2025": 30}
        self.rows = [Row(key={"Year": period}, labels={"Year": period}, value=value)
                     for period, value in values.items()]
        self.queries = []

    async def meta(self, table_id):
        return self.table_meta

    async def query(self, table_id, selection):
        self.queries.append({k: list(v) for k, v in selection.items()})
        return [r for r in self.rows if all(r.key.get(k) in v for k, v in selection.items())]


def part(question="Population", value=30):
    return AnswerPart(sub_question=question, table=TableRef(id="TEST001", title="Population", url="https://example.org"),
                      selection={"Year": ["2025"]}, rows=[], unit="persons",
                      facts=[Fact(label="Population 2025", value=value, unit="persons", formatted=str(value))])


class FailureAssertions:
    def assert_failure(self, code, fn, *args, status=None, **kwargs):
        with self.assertRaises(AnswerFailure) as raised:
            fn(*args, **kwargs)
        self.assertEqual(raised.exception.code, code)
        if status:
            self.assertEqual(raised.exception.status, status)


class RetrievalTests(unittest.IsolatedAsyncioTestCase):
    async def test_classic_does_not_replace_invalid_or_rejected_choices(self):
        for table_id, code in [("INVENTED", "invalid_table_choice"), ("none", "no_verified_table")]:
            with self.subTest(table_id=table_id):
                llm = ScriptedLLM(Pick(table_id=table_id, confidence="high"))
                with self.assertRaises(AnswerFailure) as raised:
                    await retrieve(llm, CatalogStub(), "Population?", "population", 15)
                self.assertEqual(raised.exception.status, "unverified")
                self.assertEqual(raised.exception.code, code)
                self.assertEqual(llm.calls, 1)  # even a single search hit must be checked

    async def test_classic_rejects_low_confidence_without_clarification_choices(self):
        with self.assertRaises(AnswerFailure) as raised:
            await retrieve(ScriptedLLM(Pick(table_id="TEST001", confidence="low")), CatalogStub(), "Population?", "", 15)
        self.assertEqual(raised.exception.code, "uncertain_table")

    async def test_empty_search_is_unverified_not_out_of_scope(self):
        catalog = CatalogStub()
        with patch.object(catalog, "search", return_value=[]):
            with self.assertRaises(AnswerFailure) as raised:
                await retrieve(ScriptedLLM(), catalog, "Some statistic?", "", 15)
        self.assertEqual(raised.exception.status, "unverified")

    async def test_classic_newer_sibling_requires_its_own_check(self):
        catalog = CatalogStub(entry(), entry("TEST002", "2026"))
        llm = ScriptedLLM(Pick(table_id="TEST001", confidence="high"), Pick(table_id="none", confidence="high"))
        result = await retrieve(llm, catalog, "Population?", "population", 15)
        self.assertEqual(result.table.id, "TEST001")
        self.assertEqual(llm.calls, 2)

    async def test_classic_keeps_ambiguity_and_valid_choices(self):
        catalog = CatalogStub(entry(), entry("TEST002"))
        result = await retrieve(ScriptedLLM(Pick(table_id="TEST001", confidence="low", alternatives=["TEST002"])),
                                catalog, "Population?", "population", 15)
        self.assertTrue(result.ambiguous)
        self.assertEqual([e.id for e in result.alternatives], ["TEST002"])

    async def test_unknown_forced_table_never_runs_a_different_search(self):
        for retriever in (retrieve, retrieve_tiered):
            with self.subTest(retriever=retriever.__name__):
                catalog, llm = CatalogStub(), ScriptedLLM()
                with self.assertRaises(AnswerFailure) as raised:
                    await retriever(llm, catalog, "Population?", "population", 15, "UNKNOWN")
                self.assertEqual(raised.exception.status, "unavailable")
                self.assertEqual(catalog.queries, [])
                self.assertEqual(llm.calls, 0)

    async def test_tiered_rejections_never_force_a_classic_pick(self):
        catalog = CatalogStub()
        llm = ScriptedLLM(Verdict(table_id="none"), Subjects(subjects=[]))
        with patch("hagbot.steps.tiered.voting_on", return_value=False):
            with self.assertRaises(AnswerFailure) as raised:
                await retrieve_tiered(llm, catalog, "Population?", "population", 15)
        self.assertEqual(raised.exception.code, "no_verified_table")
        self.assertEqual(llm.calls, 2)
        self.assertEqual(len(catalog.queries), 1)

    async def test_tiered_invalid_id_is_not_a_valid_choice(self):
        with patch("hagbot.steps.tiered.voting_on", return_value=False):
            with self.assertRaises(AnswerFailure) as raised:
                await retrieve_tiered(ScriptedLLM(Verdict(table_id="INVENTED")), CatalogStub(), "Population?", "", 15)
        self.assertEqual(raised.exception.code, "invalid_table_choice")

    async def test_replacement_table_must_also_pass_the_check(self):
        original, replacement = entry(), entry("TEST002", "2026")
        llm = ScriptedLLM(Verdict(table_id=original.id), Verdict(table_id="none"))
        with patch("hagbot.steps.tiered.voting_on", return_value=False), \
             patch("hagbot.steps.tiered.freshest_sibling", return_value=replacement):
            result = await retrieve_tiered(llm, CatalogStub(original, replacement), "Population?", "", 15)
        self.assertEqual(result.table.id, original.id)
        self.assertEqual(llm.calls, 2)

    async def test_existing_dictionary_still_expands_and_ranks(self):
        catalog = CatalogStub(entry("VIS01000"))
        with patch("hagbot.steps.tiered.voting_on", return_value=False):
            result = await retrieve_tiered(ScriptedLLM(Verdict(table_id="VIS01000")), catalog,
                                          "Hver er verðbólgan?", "inflation", 15)
        self.assertTrue(result.concepts)
        self.assertEqual(result.via, "keywords")
        self.assertIn("dictionary", result.ranking[0]["signals"])


class SelectionTests(FailureAssertions, unittest.TestCase):
    def test_bad_codes_are_not_dropped_or_defaulted(self):
        for picks in [{"Sex": ["unknown"]}, {"Sex": ["1", "unknown"]}, {"Year": ["2099"]}]:
            with self.subTest(picks=picks):
                self.assert_failure("invalid_selection", resolve, meta(), selection(selections=picks, operation="value"))

    def test_bad_dimension_is_not_ignored(self):
        self.assert_failure("invalid_dimension", resolve, meta(), selection(selections={"Unknown": ["1"]}, operation="value"))

    def test_available_year_cannot_be_ignored_by_model(self):
        self.assert_failure("requested_period_changed", resolve, meta(),
                            selection(selections={"Year": ["2025"]}, operation="value"), "Population in 2024?")

    def test_requested_year_outside_table_is_unavailable(self):
        self.assert_failure("requested_period_unavailable", resolve, meta(),
                            selection(selections={"Year": ["2025"]}, operation="value"), "Population in 2099?", status="unavailable")

    def test_same_year_month_cannot_snap_to_table_edge(self):
        monthly = meta(["2026M03", "2026M04"])
        self.assert_failure("requested_period_unavailable", resolve, monthly,
                            selection(selections={"Year": ["2026M01"]}, operation="value"), "January 2026", status="unavailable")

    def test_explicit_month_without_year_does_not_become_latest_month(self):
        monthly = meta(["2026M09", "2026M10"])
        result = resolve(monthly, selection(selections={"Year": ["2026M09"]}, operation="value"), "Population in September?")
        self.assertEqual(result.selections["Year"], ["2026M09"])

    def test_explicit_month_year_pairs_cannot_be_swapped(self):
        monthly = meta(["2024M09", "2024M10", "2025M09", "2025M10"])
        self.assert_failure("requested_period_changed", resolve, monthly,
                            selection(selections={"Year": ["2024M10", "2025M09"]}, operation="series"),
                            "Compare September 2024 and October 2025")
        result = resolve(monthly, selection(selections={"Year": ["2024M09", "2025M10"]}, operation="series"),
                         "Compare September 2024 and October 2025")
        self.assertEqual(result.selections["Year"], ["2024M09", "2025M10"])

    def test_explicit_quarter_year_pairs_cannot_be_swapped(self):
        quarterly = meta(["2024Q1", "2024Q2", "2025Q1", "2025Q2"])
        self.assert_failure("requested_period_changed", resolve, quarterly,
                            selection(selections={"Year": ["2024Q2", "2025Q1"]}, operation="series"),
                            "Compare Q1 2024 and Q2 2025")

    def test_monthly_first_january_is_not_discarded_as_an_annual_reference_date(self):
        self.assert_failure("requested_period_changed", resolve, meta(["2026M01", "2026M02"]),
                            selection(selections={"Year": ["2026M02"]}, operation="value"), "Population on 1 January 2026?")

    def test_icelandic_and_english_gender_filters_cannot_become_total(self):
        for question in ("How many men in 2025?", "Hvað voru karlar margir árið 2025?"):
            with self.subTest(question=question):
                self.assert_failure("requested_filter_changed", resolve, meta(),
                                    selection(selections={}, operation="value"), question)

    def test_named_geography_cannot_become_total(self):
        self.assert_failure("requested_filter_changed", resolve, meta(),
                            selection(selections={}, operation="value"), "Population in Reykjavík?")

    def test_official_municipality_suffix_does_not_hide_a_requested_place(self):
        table = meta()
        table.var("Area").value_texts[1] = "Reykjavíkurborg"
        self.assert_failure("requested_filter_changed", resolve, table,
                            selection(selections={}, operation="value"), "Population in Reykjavík?")

    def test_reported_unavailable_filter_cannot_become_total(self):
        req = RequestedFilter(dimension=None, mention="65 and over", values=[])
        self.assert_failure("requested_filter_unavailable", resolve, meta(),
                            selection(selections={}, operation="value", requested_filters=[req]),
                            "Population aged 65 and over?", status="unavailable")

    def test_reported_requirement_must_be_grounded_and_preserved(self):
        for mention, values, code in [("women", ["2"], "ungrounded_filter"),
                                      ("men", ["1"], "requested_filter_changed"),
                                      ("men", ["invented"], "invalid_filter_codes")]:
            with self.subTest(code=code):
                self.assert_failure(code, resolve, meta(), selection(selections={}, operation="value", requested_filters=[
                    RequestedFilter(dimension="Sex", mention=mention, values=values)]), "How many men?")

    def test_missing_dimension_with_no_total_asks_instead_of_selecting_first(self):
        table = meta(categories=False)
        table.variables.append(Variable(code="Kind", text="Kind", values=["a", "b"], value_texts=["A", "B"]))
        self.assert_failure("missing_filter", resolve, table, selection(selections={}, operation="value"), status="clarify")

    def test_equivalent_labels_and_defaults_still_work(self):
        result = resolve(meta(), selection(selections={"municipality": ["1=Reykjavík"], "sex": ["Males"]}, operation="value"),
                         "Population of men in Reykjavík?")
        self.assertEqual(result.selections, {"Year": ["2025"], "Area": ["1"], "Sex": ["1"]})

    def test_unavailable_base_does_not_become_current_base(self):
        table = meta(categories=False)
        table.variables.append(Variable(code="Base", text="Base", values=["old", "new"], value_texts=["Base 1983", "Base 2021"]))
        self.assert_failure("requested_base_unavailable", resolve, table,
                            selection(selections={"Base": ["new"]}, operation="value"), "Index on base 1988", status="unavailable")

    def test_index_base_does_not_erase_same_observation_year(self):
        table = meta(["2021", "2025"], categories=False)
        table.variables.append(Variable(code="Base", text="Base", values=["new"], value_texts=["Base 2021"]))
        self.assert_failure("requested_period_changed", resolve, table,
                            selection(selections={"Year": ["2025"], "Base": ["new"]}, operation="value"),
                            "Index on base 2021 in 2021?")

    def test_short_window_does_not_become_an_answer_to_full_window(self):
        self.assert_failure("requested_window_unavailable", resolve, meta(),
                            selection(selections={}, operation="series"), "Population over the last 50 years?", status="unavailable")

    def test_incomplete_calculations_cannot_become_values(self):
        for operation in ("difference", "pct_change", "share"):
            with self.subTest(operation=operation):
                self.assert_failure("incomplete_calculation", resolve, meta(categories=False),
                                    selection(selections={"Year": ["2025"]}, operation=operation))

    def test_original_period_survives_a_bad_planner_rewrite(self):
        self.assert_failure("requested_period_changed", resolve, meta(), selection(selections={}, operation="value"),
                            "Population in 2024? Current population?", part="Current population?",
                            filter_question="Population in 2024? Current population?")

    def test_relative_year_is_not_overridden_by_latest_default(self):
        with patch("hagbot.steps.constraints.date") as clock:
            clock.today.return_value = date(2026, 11, 1)
            result = resolve(meta(["2025", "2026"]), selection(selections={"Year": ["2025"]}, operation="value"),
                             "Population last year?")
        self.assertEqual(result.selections["Year"], ["2025"])

    def test_voting_cannot_erase_a_requirement(self):
        req = RequestedFilter(dimension="Sex", mention="men", values=["1"])
        chosen, _ = vote_selection([selection(selections={}, operation="value", requested_filters=[req]),
                                    selection(selections={}, operation="value")])
        self.assertEqual(chosen.requested_filters, [req])


class ComputationTests(FailureAssertions, unittest.TestCase):
    def test_missing_cells_and_operands_never_become_partial_facts(self):
        res = Resolved(selections={"Year": ["2024", "2025"]}, operation="difference", compare_dimension="Year")
        good = Row(key={"Year": "2024"}, labels={"Year": "2024"}, value=20)
        for rows, code in [([], "missing_data"), ([good], "incomplete_data"),
                           ([good, Row(key={"Year": "2025"}, labels={"Year": "2025"}, value=None)], "missing_data")]:
            with self.subTest(code=code):
                self.assert_failure(code, compute, meta(categories=False), rows, res, "en", status="unavailable")

    def test_zero_denominators_are_unavailable(self):
        rows = [Row(key={"Year": year}, labels={"Year": year}, value=value) for year, value in [("2024", 0), ("2025", 30)]]
        res = Resolved(selections={"Year": ["2024", "2025"]}, operation="pct_change", compare_dimension="Year")
        self.assert_failure("undefined_calculation", compute, meta(categories=False), rows, res, "en", status="unavailable")

    def test_complete_difference_still_computes(self):
        source = SourceStub()
        res = Resolved(selections={"Year": ["2024", "2025"]}, operation="difference", compare_dimension="Year")
        computation, facts = compute(source.table_meta, source.rows, res, "en")
        self.assertEqual(computation.result, 10)
        self.assertEqual(facts[-1].value, 10)


class PipelineTests(unittest.IsolatedAsyncioTestCase):
    def pipeline(self, *replies, source=None):
        return Pipeline(llm=ScriptedLLM(*replies), catalog=CatalogStub(), source=source or SourceStub())

    async def test_explicit_scope_and_ambiguity_stop_before_retrieval(self):
        for outcome in ("out_of_scope", "clarify"):
            with self.subTest(outcome=outcome):
                pipeline = self.pipeline(Plan(outcome=outcome))
                with patch.object(pipeline, "_answer_part", new_callable=AsyncMock) as retrieve_part:
                    answer = await pipeline.answer(AskRequest(question="A question", language="en"))
                self.assertEqual(answer.status, outcome)
                self.assertEqual(answer.parts, [])
                retrieve_part.assert_not_called()

    async def test_failed_required_part_cannot_be_marked_answered(self):
        planned = Plan(sub_questions=[SubQuestion(question="Population", search_query="population"),
                                      SubQuestion(question="Unemployment", search_query="unemployment")])
        failures = [AnswerFailure("unavailable", "requested_period_unavailable"),
                    AnswerFailure("unverified", "no_verified_table"), RuntimeError("service failed"),
                    NeedsClarification(Clarification(message="Which measure?", options=[]))]
        for failure, status in zip(failures, ("unavailable", "unverified", "error", "clarify")):
            with self.subTest(status=status):
                pipeline = self.pipeline(planned)
                async def answer_part(sq, *args, **kwargs):
                    if sq.question == "Unemployment":
                        raise failure
                    return part()
                with patch.object(pipeline, "_answer_part", side_effect=answer_part), \
                     patch("hagbot.pipeline.compose", new_callable=AsyncMock) as compose:
                    answer = await pipeline.answer(AskRequest(question="Population and unemployment?", language="en"))
                self.assertEqual(answer.status, status)
                self.assertEqual(answer.parts, [])
                self.assertEqual(answer.sources, [])
                self.assertEqual(answer.text_source, "none")
                self.assertEqual(answer.issues[0].sub_question, "Unemployment")
                self.assertIn("every required part", answer.text)
                compose.assert_not_called()

    async def test_empty_part_cannot_be_marked_answered(self):
        planned = Plan(sub_questions=[SubQuestion(question="Population", search_query="population")])
        pipeline = self.pipeline(planned)
        empty = part().model_copy(update={"facts": []})
        with patch.object(pipeline, "_answer_part", new=AsyncMock(return_value=empty)):
            answer = await pipeline.answer(AskRequest(question="Population?"))
        self.assertEqual(answer.status, "unverified")

    async def test_latest_populated_fallback_updates_displayed_selection(self):
        source = SourceStub(values={"2024": 20, "2025": None})
        pipeline = self.pipeline(selection(selections={"Year": ["2025"]}, operation="value"),
                                 Written(text="Population in 2024: 20 persons."), source=source)
        answer = await pipeline.answer(AskRequest(question="Current population?", table_id="TEST001", language="en"))
        self.assertEqual(answer.status, "answered")
        self.assertEqual(answer.parts[0].selection, {"Year": ["2024"]})
        self.assertEqual(answer.parts[0].rows[0].key, {"Year": "2024"})
        self.assertEqual(len(source.queries), 2)

    async def test_explicit_period_with_missing_data_never_falls_back(self):
        for question in ("Population in 2025?", "Population last year?"):
            with self.subTest(question=question):
                source = SourceStub(values={"2024": 20, "2025": None})
                pipeline = self.pipeline(selection(selections={"Year": ["2025"]}, operation="value"), source=source)
                with patch("hagbot.steps.constraints.date") as clock:
                    clock.today.return_value = date(2026, 11, 1)
                    answer = await pipeline.answer(AskRequest(question=question, table_id="TEST001", language="en"))
                self.assertEqual(answer.status, "unavailable")
                self.assertEqual(answer.issues[0].code, "missing_data")
                self.assertEqual(len(source.queries), 1)

    async def test_complete_lookup_still_answers(self):
        pipeline = self.pipeline(selection(selections={"Year": ["2025"]}, operation="value"),
                                 Written(text="Population in 2025: 30 persons."))
        answer = await pipeline.answer(AskRequest(question="Population in 2025?", table_id="TEST001", language="en"))
        self.assertEqual(answer.status, "answered")
        self.assertEqual(answer.issues, [])
        self.assertEqual(answer.parts[0].facts[0].value, 30)

    async def test_complete_multi_part_question_still_answers(self):
        planned = Plan(sub_questions=[SubQuestion(question="Population", search_query="population"),
                                      SubQuestion(question="Unemployment", search_query="unemployment")])
        pipeline = self.pipeline(planned)
        with patch.object(pipeline, "_answer_part", new=AsyncMock(side_effect=[part(), part("Unemployment")])), \
             patch("hagbot.pipeline.compose", new=AsyncMock(return_value=("Both figures.", "template"))):
            answer = await pipeline.answer(AskRequest(question="Population and unemployment?", language="en"))
        self.assertEqual(answer.status, "answered")
        self.assertEqual(len(answer.parts), 2)
        self.assertEqual(answer.issues, [])

    async def test_complete_pipeline_with_repository_fixture(self):
        from hagbot.sources import FixtureSource
        planned = Plan(sub_questions=[SubQuestion(question="Population in 2026?", search_query="population")])
        llm = ScriptedLLM(planned, Pick(table_id="MAN00101", confidence="high"),
                          selection(selections={"Ár": ["2026"], "Kyn": ["0"]}, operation="value"),
                          Written(text="Population in 2026: 394,324 people."))
        pipeline = Pipeline(llm=llm, catalog=CatalogStub(entry("MAN00101", "2026")), source=FixtureSource())
        with patch("hagbot.pipeline.settings") as config:
            config.retrieval, config.retrieve_top_k = "classic", 15
            answer = await pipeline.answer(AskRequest(question="Hver var mannfjöldinn árið 2026?", language="en"))
        self.assertEqual(answer.status, "answered")
        self.assertEqual(answer.parts[0].facts[0].value, 394324)
        self.assertEqual(llm.calls, 4)

    async def test_ratio_cannot_return_only_its_operands(self):
        planned = Plan(sub_questions=[SubQuestion(question="Births in 2025?", search_query="births")])
        pipeline = self.pipeline(planned)
        numerator = part().model_copy(update={"facts": part().facts * 2})
        with patch.object(pipeline, "_answer_part", new=AsyncMock(side_effect=[numerator, part()])):
            answer = await pipeline.answer(AskRequest(question="Births per inhabitant in 2025?", language="en"))
        self.assertEqual(answer.status, "unavailable")
        self.assertEqual(answer.issues[0].code, "incomplete_ratio")
        self.assertEqual(answer.parts, [])

    async def test_ratio_cannot_drop_additional_numerator_parts(self):
        planned = Plan(sub_questions=[SubQuestion(question="Births", search_query="births"),
                                      SubQuestion(question="Deaths", search_query="deaths")])
        pipeline = self.pipeline(planned)
        with patch.object(pipeline, "_answer_part", new_callable=AsyncMock) as lookup:
            answer = await pipeline.answer(AskRequest(question="Births and deaths per inhabitant?", language="en"))
        self.assertEqual(answer.status, "out_of_scope")
        lookup.assert_not_called()


class ContractTests(unittest.TestCase):
    def test_all_answer_outcomes_round_trip(self):
        for status in ("answered", "clarify", "out_of_scope", "unavailable", "unverified", "error"):
            answer = Answer(question="Q", language="en", status=status, text="A")
            self.assertEqual(Answer.model_validate_json(answer.model_dump_json()).status, status)

    def test_plan_cannot_claim_lookup_without_required_parts(self):
        with self.assertRaises(ValidationError):
            Plan()
        with self.assertRaises(ValidationError):
            Plan(outcome="out_of_scope", sub_questions=[SubQuestion(question="Q", search_query="q")])

    def test_model_cannot_omit_the_filter_requirement_field(self):
        with self.assertRaises(ValidationError):
            Selection.model_validate({"selections": {}, "operation": "value"})


class ApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_json_and_stream_preserve_abstention_outcomes(self):
        import httpx
        from hagbot import api
        for path, status in (("/api/ask", "out_of_scope"), ("/api/ask/stream", "out_of_scope"),
                             ("/api/ask", "unverified"), ("/api/ask/stream", "unverified")):
            with self.subTest(path=path, status=status):
                planned = Plan(outcome="out_of_scope") if status == "out_of_scope" else Plan(sub_questions=[
                    SubQuestion(question="Population?", search_query="population")])
                pipeline = Pipeline(llm=ScriptedLLM(planned), catalog=CatalogStub(), source=SourceStub())
                with patch.object(api, "pipeline", pipeline), patch.object(api, "_log_question"), \
                     patch.object(pipeline, "_answer_part", new=AsyncMock(side_effect=AnswerFailure("unverified", "no_verified_table"))):
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app), base_url="http://test") as client:
                        response = await client.post(path, json={"question": "A question", "language": "en"})
                self.assertEqual(response.status_code, 200)
                if path.endswith("stream"):
                    events = [json.loads(line) for line in response.text.splitlines()]
                    self.assertEqual(events[-1]["type"], "answer")
                    payload = events[-1]["answer"]
                else:
                    payload = response.json()
                answer = Answer.model_validate(payload)
                self.assertEqual(answer.status, status)
                self.assertTrue(answer.issues)
                self.assertEqual(answer.parts, [])


if __name__ == "__main__":
    unittest.main()
