"""Semantic regressions use synthetic metadata, never held-out questions or live model calls."""

import unittest
from unittest.mock import patch

from hagbot.outcomes import AnswerFailure
from hagbot.pipeline import Pipeline
from hagbot.schemas import AskRequest, Row
from hagbot.semantics import fingerprint, profile, validate_compatibility, validate_specification
from hagbot.sources import TableMeta, Variable, _meta_from_api
from hagbot.specification import Compatibility, Evidence, QuestionSpec, Requirement, RequirementCheck
from hagbot.steps.compose import Written
from hagbot.steps.plan import Plan, SubQuestion
from hagbot.steps.select import vote_selection

from test_answer_integrity import CatalogStub, FailureAssertions, ScriptedLLM, SourceStub, entry, selection


def requirement(facet, value, quote=None, origin="user"):
    return Requirement(facet=facet, value=value, origin=origin, quote=value if quote is None else quote)


def spec(*requirements, operation="value"):
    return QuestionSpec(requirements=list(requirements) or [requirement("measure", "income")], operation=operation)


def proof(*references):
    return Compatibility(checks=[RequirementCheck(requirement=i, verdict="supported", evidence=[
        Evidence(field=field, quote=quote) for field, quote in refs]) for i, refs in enumerate(references)])


def table(title="Mean and median income"):
    return TableMeta(id="TEST001", title=title, path="People/income", unit="ISK", unit_source="source", variables=[
        Variable(code="Year", text="Year", values=["2025"], value_texts=["2025"], time=True),
        Variable(code="Measure", text="Measure", values=["mean", "median"], value_texts=["Mean income", "Median income"]),
    ])


class SpecificationTests(FailureAssertions, unittest.TestCase):
    def test_user_requirements_cannot_be_invented(self):
        self.assert_failure("ungrounded_specification", validate_specification,
                            spec(requirement("measure", "wealth", quote="wealth")), "How much income?")

    def test_translation_preserves_the_original_quote(self):
        validate_specification(spec(requirement("measure", "income", quote="tekjur")), "Hverjar voru tekjur fólks?")

    def test_ambiguity_cannot_be_replaced_with_an_assumption(self):
        ambiguous = spec()
        ambiguous.ambiguities = ["income or wealth?", "which age group?"]
        self.assert_failure("ambiguous_specification", validate_specification, ambiguous, "How rich are old people?", status="clarify")

    def test_age_threshold_cannot_be_invented_from_old_people(self):
        self.assert_failure("invented_population_threshold", validate_specification,
                            spec(requirement("measure", "wealth", quote="rich"),
                                 requirement("population", "67 and over", quote="old people")), "How rich are old people?")

    def test_only_documented_defaults_are_allowed(self):
        for facet, value in (("statistic", "mean"), ("population", "67 and over"), ("measure", "wealth")):
            with self.subTest(facet=facet):
                requirements = [requirement("measure", "income")]
                if facet == "measure":
                    requirements = []
                self.assert_failure("unsupported_default", validate_specification,
                                    spec(*requirements, requirement(facet, value, quote="", origin="default")), "income")

    def test_selected_table_context_does_not_count_as_the_users_words(self):
        context = "Selected table TEST001: Income"
        validate_specification(spec(requirement("measure", "income", quote="Income", origin="context")), "Show its categories", context)
        self.assert_failure("ungrounded_specification", validate_specification,
                            spec(requirement("measure", "income", quote="Income")), "Show its categories", context)

    def test_exactly_one_measure_is_required(self):
        self.assert_failure("invalid_specification", validate_specification, spec(requirement("statistic", "mean")), "mean")


class ProfileTests(unittest.TestCase):
    def test_profile_contains_source_options_periods_and_notes(self):
        meta = table()
        meta.notes = ["Provisional figures"]
        p = profile(meta)
        self.assertEqual(p.fields["value:Measure:median"], "Median income")
        self.assertEqual(p.fields["frequency:Year"], "annual")
        self.assertEqual(p.fields["note:0"], "Provisional figures")
        self.assertEqual(p.fields["unit"], "ISK")
        self.assertEqual(profile(meta).metadata_sha256, p.metadata_sha256)
        meta.variables[1].value_texts[1] = "Median disposable income"
        self.assertNotEqual(profile(meta).metadata_sha256, p.metadata_sha256)

    def test_catalog_unit_and_title_are_not_source_evidence(self):
        catalog = CatalogStub(entry())
        catalog.entries[0].unit = "ISK"
        result = _meta_from_api("TEST001", {"variables": []}, catalog)
        p = profile(result)
        self.assertNotIn("title", p.fields)
        self.assertNotIn("unit", p.fields)
        self.assertEqual(p.unit_hint, "ISK")

    def test_source_metadata_takes_precedence_over_catalog_hints(self):
        catalog = CatalogStub(entry())
        catalog.entries[0].unit = "persons"
        result = _meta_from_api("TEST001", {"title": "Income", "unit": "ISK", "variables": [],
                                            "description": "Published income", "notes": ["Provisional"], "updated": "2026-10-01"}, catalog)
        p = profile(result)
        self.assertEqual(p.fields["title"], "Income")
        self.assertEqual(p.fields["unit"], "ISK")
        self.assertEqual(p.fields["description"], "Published income")


class CompatibilityTests(FailureAssertions, unittest.TestCase):
    def setUp(self):
        self.meta = table()
        self.selected = {"Year": ["2025"], "Measure": ["median"]}

    def test_grounded_compatible_query_has_an_audit_record(self):
        s = spec(requirement("measure", "income"), requirement("statistic", "median"))
        checked = validate_compatibility(self.meta, self.selected, s,
                                         proof([("title", "income")], [("value:Measure:median", "Median income")]))
        self.assertEqual(checked.selection_sha256, fingerprint(self.selected))
        self.assertEqual(checked.metadata_sha256, profile(self.meta).metadata_sha256)
        self.assertEqual(checked.specification, s)

    def test_mean_cannot_answer_median_even_if_the_title_mentions_both(self):
        self.assert_failure("incompatible_definition", validate_compatibility, self.meta,
                            {"Year": ["2025"], "Measure": ["mean"]},
                            spec(requirement("measure", "income"), requirement("statistic", "median")),
                            proof([("title", "income")], [("title", "median income")]), status="unavailable")

    def test_income_cannot_answer_wealth(self):
        self.assert_failure("incompatible_definition", validate_compatibility, self.meta, self.selected,
                            spec(requirement("measure", "wealth")), proof([("title", "income")]), status="unavailable")

    def test_unrequested_statistic_cannot_be_included_alongside_the_right_one(self):
        self.assert_failure("incompatible_definition", validate_compatibility, self.meta,
                            {"Year": ["2025"], "Measure": ["mean", "median"]},
                            spec(requirement("measure", "income"), requirement("statistic", "median")),
                            proof([("title", "income")], [("value:Measure:median", "Median income")]), status="unavailable")

    def test_unselected_option_cannot_support_a_query(self):
        self.assert_failure("invalid_metadata_evidence", validate_compatibility, self.meta,
                            {"Year": ["2025"], "Measure": ["mean"]}, spec(), proof([("value:Measure:median", "income")]))

    def test_quote_must_exist_in_the_named_field(self):
        self.assert_failure("invalid_metadata_evidence", validate_compatibility, self.meta, self.selected,
                            spec(), proof([("title", "Net wealth")]))

    def test_metadata_unknown_is_an_abstention(self):
        self.assert_failure("unknown_compatibility", validate_compatibility, self.meta, self.selected, spec(),
                            Compatibility(checks=[RequirementCheck(requirement=0, verdict="unknown")]), status="unverified")

    def test_every_requirement_must_be_checked_exactly_once(self):
        for checks in ([], [RequirementCheck(requirement=1, verdict="supported")],
                       [RequirementCheck(requirement=0, verdict="supported")] * 2):
            with self.subTest(checks=checks):
                self.assert_failure("incomplete_compatibility", validate_compatibility, self.meta, self.selected, spec(),
                                    Compatibility(checks=checks))

    def test_unknown_unit_hint_cannot_prove_a_unit(self):
        self.meta.unit_source = "catalog"
        self.assert_failure("invalid_metadata_evidence", validate_compatibility, self.meta, self.selected,
                            spec(requirement("measure", "income"), requirement("unit", "ISK")),
                            proof([("title", "income")], [("unit", "ISK")]))

    def test_unit_and_scale_must_match(self):
        for source_unit in ("EUR", "Million ISK"):
            with self.subTest(source_unit=source_unit):
                self.meta.unit = source_unit
                self.assert_failure("incompatible_unit", validate_compatibility, self.meta, self.selected,
                                    spec(requirement("measure", "income"), requirement("unit", "ISK")),
                                    proof([("title", "income")], [("unit", source_unit)]), status="unavailable")

    def test_annual_frequency_cannot_be_a_monthly_table(self):
        self.meta.variables[0] = Variable(code="Year", text="Month", values=["2025M01"], value_texts=["2025M01"], time=True)
        self.assert_failure("incompatible_frequency", validate_compatibility, self.meta,
                            {"Year": ["2025M01"], "Measure": ["median"]},
                            spec(requirement("measure", "income"), requirement("frequency", "annual")),
                            proof([("title", "income")], [("frequency:Year", "monthly")]), status="unavailable")

    def test_constant_prices_and_gross_income_are_not_substituted(self):
        for facet, wanted, label in (("price_basis", "constant prices", "Income at current prices"),
                                     ("tax_basis", "gross", "Net income"),
                                     ("adjustment", "seasonally adjusted", "Income, not seasonally adjusted")):
            with self.subTest(facet=facet):
                self.meta.var("Measure").value_texts[1] = label
                self.assert_failure("incompatible_definition", validate_compatibility, self.meta, self.selected,
                                    spec(requirement("measure", "income"), requirement(facet, wanted)),
                                    proof([("title", "income")], [("value:Measure:median", label)]), status="unavailable")

    def test_dimension_name_does_not_prove_a_named_geography(self):
        self.assert_failure("insufficient_metadata_evidence", validate_compatibility, self.meta, self.selected,
                            spec(requirement("measure", "income"), requirement("geography", "Reykjavík")),
                            proof([("title", "income")], [("dimension:Measure", "Measure")]))

    def test_cpi_level_is_not_inflation_but_annual_change_is(self):
        self.meta.title = "Consumer price index"
        self.meta.var("Measure").value_texts = ["Index value", "Annual change, %"]
        s = spec(requirement("measure", "inflation"))
        self.assert_failure("incompatible_definition", validate_compatibility, self.meta,
                            {"Year": ["2025"], "Measure": ["mean"]}, s, proof([("title", "Consumer price index")]), status="unavailable")
        checked = validate_compatibility(self.meta, self.selected, s,
                                         proof([("title", "Consumer price index"), ("value:Measure:median", "Annual change, %")]))
        self.assertEqual(checked.table_id, "TEST001")

    def test_votes_cannot_construct_a_query_nobody_verified(self):
        samples = [selection(selections=picks, operation="value") for picks in (
            {"A": ["1"], "B": ["1"], "C": ["2"]}, {"A": ["1"], "B": ["2"], "C": ["1"]},
            {"A": ["2"], "B": ["1"], "C": ["1"]})]
        self.assert_failure("unverified_vote_combination", vote_selection, samples)


class PipelineCompatibilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_bad_definition_stops_before_fetch_in_unified_finder(self):
        s = spec(requirement("measure", "income", quote="tekna"), requirement("statistic", "median", quote="Miðgildi"))
        plan = Plan(sub_questions=[SubQuestion(question="Typical income?", search_query="income", specification=s)])
        llm = ScriptedLLM(plan, selection(selections={"Year": ["2025"], "Measure": ["mean"]}, operation="value",
            compatibility=proof([("title", "income")], [("title", "median income")])))
        source = SourceStub(table_meta=table())
        answer = await Pipeline(llm=llm, source=source, catalog=CatalogStub()).answer(
            AskRequest(question="Miðgildi tekna?", table_id="TEST001", language="en"))
        self.assertEqual(answer.status, "unavailable")
        self.assertEqual(answer.issues[0].code, "incompatible_definition")
        self.assertEqual(source.queries, [])
        self.assertEqual(llm.calls, 2)

    async def test_verified_query_carries_evidence_to_the_answer(self):
        s = spec(requirement("measure", "income"), requirement("statistic", "median"), requirement("period", "2025"))
        plan = Plan(sub_questions=[SubQuestion(question="Median income in 2025?", search_query="income", specification=s)])
        llm = ScriptedLLM(plan, selection(selections={"Year": ["2025"], "Measure": ["median"]}, operation="value",
            compatibility=proof([("title", "income")], [("value:Measure:median", "Median income")], [("value:Year:2025", "2025")])),
            Written(text="Median income in 2025: 40 ISK."))
        source = SourceStub(table_meta=table())
        source.rows = [Row(key={"Year": "2025", "Measure": "median"}, labels={"Year": "2025", "Measure": "Median income"}, value=40)]
        answer = await Pipeline(llm=llm, source=source, catalog=CatalogStub()).answer(
            AskRequest(question="Median income in 2025?", table_id="TEST001", language="en"))
        self.assertEqual(answer.status, "answered")
        self.assertEqual(len(source.queries), 1)
        verification = answer.parts[0].verifications[0]
        self.assertEqual(len(verification.checks), 3)
        self.assertEqual(verification.executed_selection_sha256, fingerprint(source.queries[0]))
        self.assertEqual(verification.selection_sha256, verification.executed_selection_sha256)
        self.assertFalse(verification.amendments)


if __name__ == "__main__":
    unittest.main()
