"""Meaning can be fixed by a table or dataset; it need not be a selectable category."""

import unittest
from unittest.mock import patch

from hagbot.contexts import context_for, validate_combination
from hagbot.outcomes import AnswerFailure
from hagbot.schemas import AnswerPart, Fact, TableRef
from hagbot.schemas import AskRequest, Row
from hagbot.semantics import profile, validate_compatibility
from hagbot.sources import TableMeta, Variable
from hagbot.steps.select import resolve, select, SelectionCodes
from hagbot.pipeline import Pipeline
from hagbot.steps.plan import Plan, SubQuestion, Combination
from hagbot.steps.retrieve import Retrieved
from test_answer_integrity import CatalogStub, entry

from test_answer_integrity import ScriptedLLM, selection
from test_semantic_compatibility import proof, requirement, spec


def scoped_table():
    return TableMeta(id="VESSELS", title="Registered vessels by region", path="Transport/vessels",
                     unit="Number", unit_source="source", dataset_scope={"geography": "Nordland"}, variables=[
        Variable(code="Year", text="Year", values=["2025"], value_texts=["2025"], time=True),
        Variable(code="Region", text="Region", values=["all", "bay"], value_texts=["Total", "Bayport"])])


class FixedScopeTests(unittest.TestCase):
    def test_dataset_scope_needs_no_country_column(self):
        m = scoped_table()
        s = spec(requirement("measure", "registered vessels", quote="vessels"), requirement("geography", "Nordland"))
        picked = selection(selections={"Region": ["all"]}, operation="value", compatibility=proof(
            [("title", "Registered vessels")], [("dataset:geography", "Nordland")]),
            requested_filters=[{"dimension": None, "mention": "Nordland", "values": []}])
        result = resolve(m, picked, "Registered vessels in Nordland?", specification=s)
        self.assertEqual(result.selections["Region"], ["all"])

    def test_fixed_city_scope_needs_no_city_column(self):
        m = scoped_table()
        m.title = "Registered vessels in Bayport"
        m.variables = m.variables[:1]
        s = spec(requirement("measure", "registered vessels"), requirement("geography", "Bayport"))
        result = resolve(m, selection(selections={}, operation="value", compatibility=proof(
            [("title", "Registered vessels")], [("title", "Bayport")]),
            requested_filters=[{"dimension": None, "mention": "Bayport", "values": []}]),
            "Registered vessels in Bayport?", specification=s)
        self.assertEqual(result.selections, {"Year": ["2025"]})

    def test_dataset_context_does_not_override_country_choices_or_source_contradictions(self):
        m = scoped_table()
        m.variables.append(Variable(code="Country", text="Country", values=["north", "south"], value_texts=["Nordland", "Southland"]))
        self.assertNotIn("dataset:geography", profile(m).fields)
        s = spec(requirement("measure", "registered vessels"), requirement("geography", "Nordland"))
        with self.assertRaises(AnswerFailure):
            resolve(m, selection(selections={"Country": ["south"]}, operation="value", compatibility=proof(
                [("title", "Registered vessels")], [("value:Country:south", "Southland")])),
                "Registered vessels in Nordland?", specification=s)
        m = scoped_table()
        p = proof([("title", "Registered vessels")], [("title", "Registered vessels")])
        p.checks[1].verdict = "unsupported"
        with self.assertRaises(AnswerFailure):
            resolve(m, selection(selections={}, operation="value", compatibility=p),
                    "Registered vessels in Nordland?", specification=s)

    def test_publisher_name_alone_is_not_reporting_scope(self):
        m = scoped_table()
        m.dataset_scope, m.source = {}, "Statistics Nordland"
        m.variables = m.variables[:1]
        with self.assertRaises(AnswerFailure):
            validate_compatibility(m, {"Year": ["2025"]}, spec(
                requirement("measure", "registered vessels"), requirement("geography", "Nordland")),
                proof([("title", "Registered vessels")], [("source", "Statistics Nordland")]))

    def test_fixed_unit_is_not_an_unavailable_currency_filter(self):
        m = TableMeta(id="PAY", title="Median earnings", path="Labour", unit="ISK", unit_source="source",
                      variables=[Variable(code="Year", text="Year", values=["2025"], value_texts=["2025"], time=True)])
        s = spec(requirement("measure", "wages", quote="earnings"), requirement("unit", "ISK"))
        p = proof([("title", "earnings")], [("unit", "ISK")])
        result = resolve(m, selection(selections={}, operation="value", compatibility=p,
                                     requested_filters=[{"dimension": None, "mention": "ISK", "values": []}]),
                         "Median earnings in ISK?", specification=s)
        self.assertEqual(result.selections["Year"], ["2025"])
        changed = spec(requirement("measure", "wages", quote="earnings"), requirement("unit", "EUR"))
        with self.assertRaises(AnswerFailure):
            resolve(m, selection(selections={}, operation="value", compatibility=p), "Median earnings in EUR?", specification=changed)

    def test_fixed_population_and_period_are_not_missing_dimensions(self):
        m = TableMeta(id="AGE", title="Population aged 65 and over in 2025", path="People", unit="Persons", unit_source="source",
                      variables=[Variable(code="Sex", text="Sex", values=["all"], value_texts=["Total"])])
        s = spec(requirement("measure", "population"), requirement("population", "65 and over"), requirement("period", "2025"))
        result = resolve(m, selection(selections={}, operation="value", compatibility=proof(
            [("title", "Population")], [("title", "65 and over")], [("title", "2025")]), requested_filters=[
                {"dimension": None, "mention": "65 and over", "values": []},
                {"dimension": None, "mention": "2025", "values": []}]),
            "Population aged 65 and over in 2025?", specification=s)
        self.assertEqual(result.selections, {"Sex": ["all"]})

    def test_unrequested_default_preferences_need_no_extra_metadata_proof(self):
        m = scoped_table()
        s = spec(requirement("measure", "registered vessels"),
                 requirement("statistic", "total", quote="", origin="default"),
                 requirement("adjustment", "unadjusted", quote="", origin="default"),
                 requirement("period", "latest", quote="", origin="default"))
        checked = validate_compatibility(m, {"Year": ["2025"], "Region": ["all"]}, s,
                                         proof([("title", "Registered vessels")]))
        self.assertEqual(len(checked.checks), 4)
        self.assertEqual(checked.checks[1].verdict, "unknown")
        self.assertEqual(checked.checks[3].verdict, "supported")
        selected = resolve(m, selection(selections={}, operation="value", compatibility=proof(
            [("title", "Registered vessels")]), requested_filters=[
                {"dimension": None, "mention": "unadjusted", "values": []}]),
            "Registered vessels?", specification=s)
        self.assertEqual(selected.selections["Region"], ["all"])

    def test_explicit_adjustment_still_cannot_be_silently_changed(self):
        m = scoped_table()
        m.title = "Registered vessels, seasonally adjusted"
        s = spec(requirement("measure", "registered vessels"), requirement("adjustment", "unadjusted"))
        with self.assertRaises(AnswerFailure):
            validate_compatibility(m, {"Year": ["2025"], "Region": ["all"]}, s,
                                   proof([("title", "Registered vessels")], [("title", "seasonally adjusted")]))


class OperandScopeTests(unittest.IsolatedAsyncioTestCase):
    async def test_parent_operations_never_turn_a_scalar_operand_into_a_calculation(self):
        m = scoped_table()
        s = spec(requirement("measure", "registered vessels", quote="vessels"), requirement("geography", "Bayport"))
        picked = selection(selections={"Region": ["bay"]}, operation="share", compatibility=proof(
            [("title", "Registered vessels")], [("value:Region:bay", "Bayport")]))
        for parent in ("What share of Nordland's vessels are in Bayport?", "How many more vessels are in Bayport than elsewhere?",
                       "What is the percentage change in Bayport's vessels?"):
            with self.subTest(parent=parent), patch("hagbot.steps.select.voting_on", return_value=False):
                llm = ScriptedLLM(picked)
                result = await select(llm, m, "Registered vessels in Bayport?", parent,
                                      specification=s, filter_question="Registered vessels in Bayport?")
                self.assertEqual(result.operation, "value")
                self.assertNotIn(parent, llm.prompts[0][1])
                self.assertEqual(result.selections["Region"], ["bay"])
        self.assertNotIn("operation", SelectionCodes.model_fields)

    async def test_shared_explicit_period_survives_a_short_operand_rewrite(self):
        m = scoped_table()
        m.variables[0].values = m.variables[0].value_texts = ["2024", "2025"]
        s = spec(requirement("measure", "registered vessels", quote="vessels"), requirement("geography", "Bayport"),
                 requirement("period", "2024"))
        picked = selection(selections={"Year": ["2024"], "Region": ["bay"]}, operation="share", compatibility=proof(
            [("title", "Registered vessels")], [("value:Region:bay", "Bayport")], [("value:Year:2024", "2024")]))
        with patch("hagbot.steps.select.voting_on", return_value=False):
            result = await select(ScriptedLLM(picked), m, "Registered vessels in Bayport?", "What share of vessels were in Bayport in 2024?",
                                  specification=s, filter_question="Registered vessels in Bayport?")
        self.assertEqual(result.selections["Year"], ["2024"])

    async def test_time_window_is_derived_from_periods_not_a_required_numeric_label(self):
        m = scoped_table()
        m.variables[0].values = m.variables[0].value_texts = [str(year) for year in range(2010, 2026)]
        s = spec(requirement("measure", "registered vessels", quote="vessels"), requirement("period", "last 5 years"), operation="series")
        picked = selection(selections={"Year": ["2020", "2025"]}, operation="series", compatibility=proof(
            [("title", "Registered vessels")], [("value:Year:2020", "2020"), ("value:Year:2025", "2025")]))
        with patch("hagbot.steps.select.voting_on", return_value=False):
            result = await select(ScriptedLLM(picked), m, "Registered vessels over the last 5 years?",
                                  specification=s, filter_question="Registered vessels over the last 5 years?")
        self.assertEqual(result.selections["Year"], [str(year) for year in range(2020, 2026)])


class GeographicContainmentTests(unittest.TestCase):
    def test_country_total_can_contain_a_region_without_equal_geography_labels(self):
        m = scoped_table()
        m.title, m.notes, m.unit = "Population by region", ["Population on 1 January"], "Persons"
        contexts = [context_for(m, {"Year": ["2025"], "Region": [code]},
                                spec(requirement("measure", "population"), requirement("geography", place)))
                    for code, place in (("bay", "Bayport"), ("all", "Nordland"))]
        parts = [AnswerPart(sub_question="Population", table=TableRef(id="P", title=m.title, url="https://example.org", source_kind="fixture"),
                            selection={}, rows=[], unit="Persons", context=context,
                            facts=[Fact(label="Population 2025", value=value, unit="Persons", formatted=str(value))])
                 for context, value in zip(contexts, (20, 100))]
        validate_combination(*parts, "share")
        parts[1].context.geography_scope = "Southland"
        with self.assertRaises(AnswerFailure):
            validate_combination(*parts, "share")


class ComposedLookupTests(unittest.IsolatedAsyncioTestCase):
    async def test_scalar_operands_and_dataset_scope_support_general_population_shares(self):
        regional = scoped_table()
        regional.id, regional.title, regional.unit = "REGIONS", "Population by region", "Persons"
        regional.notes, regional.source_kind = ["Population on 1 January"], "fixture"
        country = regional.model_copy(deep=True)
        country.id, country.title = "COUNTRY", "Population"
        country.variables = country.variables[:1]
        planned = Plan(sub_questions=[SubQuestion(question=f"Population in {place}?", search_query="population", specification=spec(
            requirement("measure", "population"), requirement("geography", place))) for place in ("Bayport", "Nordland")],
            combination=Combination(operation="share", first=0, second=1))
        catalog = CatalogStub(entry("REGIONS"), entry("COUNTRY"))
        class Model:
            model, calls = "synthetic", 0
            async def complete_json(self, system, user, schema, **kwargs):
                self.calls += 1
                if schema is Plan:
                    return planned
                if "Table REGIONS," in user:
                    return SelectionCodes(selections={"Year": ["2025"], "Region": ["bay"]}, compatibility=proof(
                        [("title", "Population")], [("value:Region:bay", "Bayport")]), requested_filters=[])
                return SelectionCodes(selections={"Year": ["2025"]}, compatibility=proof(
                    [("title", "Population")], [("dataset:geography", "Nordland")]), requested_filters=[])
        class Source:
            async def meta(self, table_id):
                return regional if table_id == "REGIONS" else country
            async def query(self, table_id, selections):
                return [Row(key={k: values[0] for k, values in selections.items()},
                            labels={k: values[0] for k, values in selections.items()}, value=20 if table_id == "REGIONS" else 100)]
        async def retrieve(llm, catalog, question, *args, **kwargs):
            target = "REGIONS" if "Bayport" in question else "COUNTRY"
            return Retrieved(table=catalog.get(target), candidates=[target])
        with patch("hagbot.pipeline.retrieve_tiered", new=retrieve), patch("hagbot.steps.select.voting_on", return_value=False):
            answer = await Pipeline(llm=Model(), source=Source(), catalog=catalog).answer(
                AskRequest(question="What share of Nordland's population lives in Bayport?", language="en"))
        self.assertEqual(answer.status, "answered", answer.model_dump())
        self.assertEqual(answer.parts[0].computation.result, 20)
        self.assertEqual(answer.claims[0].units[-1], "%")


if __name__ == "__main__":
    unittest.main()
