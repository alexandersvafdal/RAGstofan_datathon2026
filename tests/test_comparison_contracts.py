import unittest

from hagbot.contexts import context_for, validate_combination, validate_population_ratio
from hagbot.outcomes import AnswerFailure
from hagbot.schemas import StatisticalContext
from hagbot.steps.ratio import RatioAsk, ratio_parts
from hagbot.sources import TableMeta, Variable

from test_answer_integrity import part
from test_semantic_compatibility import requirement, spec


class ComparisonTests(unittest.TestCase):
    def assert_code(self, code, fn, *args, **kwargs):
        with self.assertRaises(AnswerFailure) as error:
            fn(*args, **kwargs)
        self.assertEqual(error.exception.code, code)

    def test_matching_definitions_allow_ordered_calculation(self):
        a, b = part(value=100), part(value=200)
        validate_combination(a, b, "difference")
        b.context.periods = ["2024"]
        self.assert_code("unaligned_default_periods", validate_combination, a, b, "difference")
        a.context.explicit = b.context.explicit = ["period"]
        validate_combination(a, b, "difference")

    def test_measure_definition_unit_frequency_reference_and_vintage_cannot_drift(self):
        cases = [("measure", "wealth", "incompatible_comparison_measure"),
                 ("definitions", {"statistic": ["mean"]}, "incompatible_comparison_definition"),
                 ("unit", "ISK", "incompatible_comparison_unit"),
                 ("scale", 1000, "incompatible_comparison_unit"),
                 ("periods", ["2025Q1"], "incompatible_comparison_frequency"),
                 ("reference", "annual_average", "incompatible_comparison_reference")]
        for field, value, code in cases:
            with self.subTest(field=field):
                a, b = part(), part()
                setattr(b.context, field, value)
                self.assert_code(code, validate_combination, a, b, "share")
        a, b = part(), part()
        b.table.source_kind = "live"
        self.assert_code("incompatible_source_vintages", validate_combination, a, b, "difference")

    def test_missing_context_and_unknown_units_are_not_assumed_compatible(self):
        a, b = part(), part()
        b.context = None
        self.assert_code("unknown_comparison_context", validate_combination, a, b, "share")
        b.context = a.context.model_copy(update={"unit": ""})
        self.assert_code("unknown_comparison_unit", validate_combination, a, b, "share")

    def test_share_population_subset_is_checked_separately_from_geography(self):
        a, b = part(), part()
        a.context.population, b.context.population = {"sex": ["females"]}, {"sex": ["total"]}
        validate_combination(a, b, "share")
        b.context.population = {"sex": ["males"]}
        self.assert_code("incompatible_share_population", validate_combination, a, b, "share")
        b.context.population = {"sex": ["total"]}
        a.context.geography = ["reykjavik"]
        self.assert_code("unproven_geographic_containment", validate_combination, a, b, "share")

    def test_population_ratio_requires_correct_place_date_and_population(self):
        a, b = part(), part(value=100)
        a.context.measure, a.context.unit = "gross domestic product", "ISK"
        validate_population_ratio(a, b, "2025")
        for field, value, code in [("geography", ["akureyri"], "incompatible_ratio_geography"),
                                   ("population", {"age": ["65 and over"]}, "incompatible_ratio_population"),
                                   ("reference", "annual_average", "incompatible_population_reference"),
                                   ("periods", ["2024"], "incompatible_ratio_period"),
                                   ("unit", "index", "invalid_population_denominator")]:
            with self.subTest(field=field):
                old = b.context.model_copy(deep=True)
                setattr(b.context, field, value)
                self.assert_code(code, validate_population_ratio, a, b, "2025")
                b.context = old

    def test_explicitly_different_population_date_is_preserved(self):
        a, b = part(), part(value=100)
        a.context.measure, a.context.unit = "gross domestic product", "ISK"
        a.context.periods, b.context.periods = ["2024"], ["2025"]
        a.context.explicit = b.context.explicit = ["period"]
        self.assert_code("incompatible_ratio_period", validate_population_ratio, a, b, "2025")
        validate_population_ratio(a, b, "2025", dated=True)

    def test_per_capita_arithmetic_converts_source_scale(self):
        a, b = part(value=10), part(value=2)
        a.context.unit, a.context.scale = "ISK", 1000000
        b.context.scale = 1000
        result = ratio_parts(RatioAsk("GDP", 1, "2025", "per capita"), a, b, "en")[0]
        self.assertEqual(result.computation.result, 5000)
        self.assertEqual(result.facts[-1].unit, "ISK")
        self.assertEqual(result.facts[-1].basis_indices, [0, 1])

    def test_context_reads_selected_definition_notes_and_not_unselected_notes(self):
        m = TableMeta(id="GDP", title="Gross domestic product", path="Economy", unit="Million ISK", unit_source="source", variables=[
            Variable(code="Year", text="Year", values=["2025"], value_texts=["2025"], time=True),
            Variable(code="Basis", text="Basis", values=["current", "constant"], value_texts=["Value", "Volume"],
                     value_notes={"current": ["Current prices"], "constant": ["Constant prices"]})])
        c = context_for(m, {"Year": ["2025"], "Basis": ["current"]}, spec(requirement("measure", "gross domestic product")))
        self.assertEqual(c.definitions["price_basis"], ["current prices"])
        self.assertEqual((c.unit, c.scale), ("ISK", 1000000))
        self.assertEqual(c.reference, "period_total")

    def test_january_31_is_not_misread_as_january_1(self):
        m = TableMeta(id="POP", title="Population as of 31 January", path="People", unit="persons", unit_source="source", variables=[
            Variable(code="Year", text="Year", values=["2025"], value_texts=["2025"], time=True)])
        c = context_for(m, {"Year": ["2025"]}, spec(requirement("measure", "population")))
        self.assertEqual(c.reference, "point_in_time")


if __name__ == "__main__":
    unittest.main()
