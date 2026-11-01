import unittest

from hagbot.claims import build_claims, render_claims, validate_claims
from hagbot.outcomes import AnswerFailure
from hagbot.schemas import Fact
from hagbot.steps.compose import compose, verify
from hagbot.steps.ratio import difference_parts

from test_answer_integrity import ScriptedLLM, part


class ClaimTests(unittest.TestCase):
    def test_right_number_with_wrong_label_period_unit_or_source_is_rejected(self):
        p = part()
        original = build_claims([p], "en")[0]
        for update in ({"labels": ["Median income 2025"]}, {"periods": ["2024"]}, {"units": ["ISK"]},
                       {"source_ids": ["OTHER"]}, {"text": "Population in 2024: 30 ISK"}):
            with self.subTest(update=update), self.assertRaises(AnswerFailure):
                validate_claims([original.model_copy(update=update)], [p])
        self.assertFalse(verify("Income in 2025 was 30 ISK.", [p]))
        self.assertTrue(verify(render_claims([original], [p], "en"), [p]))

    def test_no_requested_part_or_category_can_disappear_from_claims(self):
        a, b = part(), part("Unemployment")
        b.facts = [Fact(label="Rate 2025", value=5, unit="%", formatted="5.0%")]
        claims = build_claims([a, b], "en")
        with self.assertRaises(AnswerFailure):
            validate_claims(claims[:1], [a, b])
        a.facts.append(Fact(label="Other group 2025", value=20, unit="persons", formatted="20"))
        with self.assertRaises(AnswerFailure):
            validate_claims(build_claims([a], "en")[:1], [a])

    def test_calculation_claim_binds_both_operands_and_the_result(self):
        combined = difference_parts(part(value=300), part(value=200), "en", operation="pct_change", ordered=True)
        claims = build_claims(list(combined), "en")
        calculation = next(c for c in claims if c.kind == "calculation")
        self.assertEqual(calculation.values, [200, 300, 50])
        self.assertEqual(calculation.units[-1], "%")
        self.assertEqual(calculation.fact_indices, [0, 1, 2])
        combined[0].facts[-1].basis_indices = []
        with self.assertRaises(AnswerFailure):
            build_claims(list(combined), "en")

    def test_series_summary_covers_entire_range_and_preserves_series_labels(self):
        p = part()
        p.facts = [Fact(label=f"{group}, {year}", value=value, unit="persons", formatted=str(value), periods=[str(year)])
                   for group in ("Males", "Females") for year, value in [(2020, 10), (2021, 30), (2022, 20), (2023, 15)]]
        claims = build_claims([p], "en")
        self.assertEqual(len(claims), 2)
        self.assertTrue(all(c.kind == "series" for c in claims))
        self.assertIn("2020", claims[0].text)
        self.assertIn("2023", claims[0].text)
        self.assertIn("highest 30 persons (2021)", claims[0].text)
        with self.assertRaises(AnswerFailure):
            validate_claims(claims[:1], [p])

    def test_snapshot_and_forecast_disclosures_come_from_source(self):
        p = part()
        p.table.title, p.table.source_kind, p.table.snapshot_at = "Population projection", "snapshot", "2026-10-18"
        text = render_claims(build_claims([p], "en"), [p], "en")
        self.assertIn("Published forecast", text)
        self.assertIn("snapshot 2026-10-18", text)


class CompositionTests(unittest.IsolatedAsyncioTestCase):
    async def test_final_prose_needs_no_model_call(self):
        llm = ScriptedLLM()
        text, source = await compose(llm, "Population?", [part()], "en")
        self.assertEqual(llm.calls, 0)
        self.assertEqual(source, "template")
        self.assertIn("30 persons", text)


if __name__ == "__main__":
    unittest.main()
