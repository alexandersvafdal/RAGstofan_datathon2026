"""Regressions at the boundary between the teammate's finder/arithmetic and the correctness harness."""

import unittest
from unittest.mock import AsyncMock, patch

from hagbot.outcomes import AnswerFailure
from hagbot.pipeline import Pipeline
from hagbot.schemas import AskRequest, Row
from hagbot.sources import TableMeta, Variable
from hagbot.specification import Compatibility, Evidence, QuestionSpec, Requirement, RequirementCheck
from hagbot.steps.compose import Written
from hagbot.steps.compute import compute
from hagbot.steps.plan import Combination, Plan, SubQuestion, merge_period_parts
from hagbot.steps.ratio import difference_parts, share_parts
from hagbot.steps.select import resolve

from test_answer_integrity import CatalogStub, ScriptedLLM, SourceStub, entry, meta, part, selection, specification
from test_semantic_compatibility import proof, requirement, spec, table


class TeammateIntegrationTests(unittest.TestCase):
    def test_missing_place_cannot_be_replaced_with_a_total_and_a_note(self):
        with self.assertRaises(AnswerFailure) as error:
            resolve(meta(), selection(selections={"Area": ["1"]}, operation="value"), "Population in Egilsstaðir?")
        self.assertEqual(error.exception.status, "unavailable")
        self.assertEqual(error.exception.code, "requested_filter_unavailable")

    def test_unnamed_categories_become_chips_after_total_selection(self):
        result = resolve(meta(), selection(selections={"Area": ["0", "1", "2"]}, operation="series"), "Population trend")
        self.assertEqual(result.selections["Area"], ["0"])
        self.assertEqual(result.alternatives["Area"], ["1", "2"])

    def test_sum_of_named_places_keeps_both_and_excludes_total(self):
        s = spec(requirement("measure", "population", quote="Population"), requirement("geography", "Reykjavík"),
                 requirement("geography", "Akureyri"), operation="sum")
        picked = selection(selections={"Area": ["0", "1", "2"]}, operation="value", compatibility=proof(
            [("title", "Population")], [("value:Area:1", "Reykjavík")], [("value:Area:2", "Akureyri")]))
        result = resolve(meta(), picked, "Population in Reykjavík and Akureyri together?", specification=s)
        self.assertEqual(result.operation, "sum")
        self.assertEqual(result.selections["Area"], ["1", "2"])
        rows = [Row(key={"Year": "2025", "Area": code, "Sex": "0"},
                    labels={"Year": "2025", "Municipality": label, "Sex": "Total"}, value=value)
                for code, label, value in [("1", "Reykjavík", 10), ("2", "Akureyri", 20)]]
        calculation, facts = compute(meta(), rows, result, "en")
        self.assertEqual(calculation.result, 30)
        self.assertEqual(facts[-1].value, 30)

    def test_sum_cannot_double_count_total_and_a_component(self):
        with self.assertRaises(AnswerFailure) as error:
            resolve(meta(), selection(selections={"Area": ["0", "1"]}, operation="sum", compare_dimension="Area"))
        self.assertEqual(error.exception.code, "overlapping_total")

    def test_spelled_out_time_window_is_preserved(self):
        with self.assertRaises(AnswerFailure) as error:
            resolve(meta(), selection(selections={}, operation="series"), "Population over the last fifty years?")
        self.assertEqual(error.exception.code, "requested_window_unavailable")

    def test_ordinal_quarter_is_not_replaced_with_another_quarter(self):
        for question in ("Population in the second quarter of 2025?", "Population á öðrum ársfjórðungi 2025?"):
            with self.subTest(question=question), self.assertRaises(AnswerFailure) as error:
                resolve(meta(["2025Q1", "2025Q3"]), selection(selections={"Year": ["2025Q3"]}, operation="value"), question)
            self.assertEqual(error.exception.code, "requested_period_unavailable")

    def test_period_merge_keeps_specification_and_calculation(self):
        parts = [SubQuestion(question=f"Population in {year}?", search_query="population", specification=spec(
            requirement("measure", "population", quote="Population"), requirement("period", year))) for year in ("2024", "2025")]
        merged = merge_period_parts(Plan(sub_questions=parts), "Population change from 2024 to 2025?")
        self.assertEqual(len(merged.sub_questions), 1)
        self.assertEqual(merged.sub_questions[0].specification.operation, "difference")
        self.assertEqual([r.value for r in merged.sub_questions[0].specification.requirements if r.facet == "period"], ["2024", "2025"])

    def test_period_merge_does_not_merge_different_definitions(self):
        parts = [SubQuestion(question=f"Income in {year}?", search_query="income", specification=spec(
            requirement("measure", "income"), requirement("statistic", statistic), requirement("period", year)))
            for year, statistic in [("2024", "mean"), ("2025", "median")]]
        self.assertEqual(len(merge_period_parts(Plan(sub_questions=parts)).sub_questions), 2)

    def test_ordered_share_does_not_guess_smaller_is_numerator(self):
        result = share_parts(part(value=120), part(value=100), "en", ordered=True)
        self.assertEqual(result[0].computation.result, 120)
        zero = share_parts(part(value=0), part(value=100), "en", ordered=True)
        self.assertEqual(zero[0].computation.result, 0)

    def test_cross_table_share_requires_matching_units_and_periods(self):
        different_year = part(value=100)
        different_year.facts[0].label = "Population 2024"
        self.assertIsNone(share_parts(part(), different_year, "en", ordered=True))
        different_unit = part(value=100)
        different_unit.facts[0].unit = "ISK"
        self.assertIsNone(share_parts(part(), different_unit, "en", ordered=True))

    def test_percentage_change_is_not_an_absolute_difference(self):
        changed = difference_parts(part(value=300), part(value=200), "en", operation="pct_change", ordered=True)
        self.assertEqual(changed[0].computation.result, 50)
        self.assertEqual(changed[0].computation.unit, "%")
        self.assertIsNone(difference_parts(part(value=300), part(value=0), "en", operation="pct_change", ordered=True))


class PipelineIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_planned_cross_table_roles_control_the_calculation(self):
        for operation, values, expected in (("share", (120, 100), 120), ("pct_change", (300, 200), 50)):
            with self.subTest(operation=operation):
                parts = [SubQuestion(question=f"Population group {i}", search_query="population", specification=spec(
                    requirement("measure", "population", quote="population"))) for i in (0, 1)]
                planned = Plan(sub_questions=parts, combination=Combination(operation=operation, first=0, second=1))
                pipeline = Pipeline(llm=ScriptedLLM(planned), source=SourceStub(), catalog=CatalogStub())
                with patch.object(pipeline, "_answer_part", new=AsyncMock(side_effect=[part(value=v) for v in values])), \
                     patch("hagbot.pipeline.compose", new=AsyncMock(return_value=("Calculated result", "template"))):
                    answer = await pipeline.answer(AskRequest(question="What share of population?", language="en"))
                self.assertEqual(answer.status, "answered")
                self.assertEqual(answer.parts[0].computation.op, operation)
                self.assertEqual(answer.parts[0].computation.result, expected)

    async def test_cross_table_share_without_operand_roles_is_not_marked_answered(self):
        parts = [SubQuestion(question=f"Population group {i}", search_query="population", specification=spec(
            requirement("measure", "population", quote="population"))) for i in (0, 1)]
        pipeline = Pipeline(llm=ScriptedLLM(Plan(sub_questions=parts)), source=SourceStub(), catalog=CatalogStub())
        with patch.object(pipeline, "_answer_part", new=AsyncMock(side_effect=[part(value=120), part(value=100)])):
            answer = await pipeline.answer(AskRequest(question="What share of population?", language="en"))
        self.assertEqual(answer.status, "unverified")
        self.assertEqual(answer.issues[0].code, "missing_combination_roles")

    async def test_dictionary_fast_path_still_requires_source_validation(self):
        s = spec(requirement("measure", "inflation", quote="verðbólgan"))
        plan = Plan(sub_questions=[SubQuestion(question="Inflation?", search_query="inflation", specification=s)])
        llm = ScriptedLLM(plan, selection(selections={"Year": ["2025"], "Measure": ["median"]}, operation="value",
                                         compatibility=proof([("title", "income")])))
        source = SourceStub(table_meta=table().model_copy(update={"id": "VIS01000"}))
        with patch("hagbot.steps.tiered.voting_on", return_value=False), patch("hagbot.steps.select.voting_on", return_value=False):
            answer = await Pipeline(llm=llm, source=source, catalog=CatalogStub(entry("VIS01000"))).answer(
                AskRequest(question="Hver er verðbólgan?", language="is"))
        self.assertIn(answer.status, ("unavailable", "unverified"))
        self.assertEqual(source.queries, [])
        self.assertEqual(llm.calls, 2)

    async def test_published_rate_is_used_and_verified_without_division(self):
        s = spec(requirement("measure", "GDP"), requirement("population", "per capita"), requirement("period", "2024"))
        plan = Plan(sub_questions=[SubQuestion(question="GDP per capita in 2024?", search_query="GDP", specification=s)])
        selected = selection(selections={"Year": ["2024"]}, operation="value", compatibility=proof(
            [("title", "Gross domestic product")], [("title", "per capita")], [("value:Year:2024", "2024")]))
        llm = ScriptedLLM(plan, selected, Written(text="GDP per capita in 2024: 40 ISK."))
        source = SourceStub(table_meta=TableMeta(id="THJ01401", path="Economy/GDP", title="Gross domestic product per capita",
            unit="ISK", unit_source="source", variables=[Variable(code="Year", text="Year", values=["2024"], value_texts=["2024"], time=True)]),
            values={"2024": 40})
        card = entry("THJ01401", "2024")
        card.title_en = "GDP per capita"
        with patch("hagbot.pipeline.ratio_parts") as divide:
            answer = await Pipeline(llm=llm, source=source, catalog=CatalogStub(card)).answer(
                AskRequest(question="What was GDP per capita in 2024?", language="en"))
        self.assertEqual(answer.status, "answered")
        self.assertEqual(len(answer.parts), 1)
        self.assertTrue(answer.parts[0].verifications)
        self.assertEqual(llm.calls, 3)
        divide.assert_not_called()


if __name__ == "__main__":
    unittest.main()
