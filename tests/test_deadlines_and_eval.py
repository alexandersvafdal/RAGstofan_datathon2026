import asyncio
import json
import threading
import time
import unittest
from dataclasses import replace
from unittest.mock import patch

import httpx

from eval.integrity_eval import grade, summarize
from hagbot.config import settings
from hagbot.pipeline import Pipeline
from hagbot.schemas import Answer, AskRequest, Step
from hagbot.steps.plan import Plan

from test_answer_integrity import CatalogStub, ScriptedLLM, SourceStub, SubQuestion, part


class DeadlineTests(unittest.IsolatedAsyncioTestCase):
    async def test_deadline_cancels_work_and_never_returns_partial_results(self):
        pipeline = Pipeline(llm=ScriptedLLM(), catalog=CatalogStub(), source=SourceStub())
        cancelled, events = [], []
        async def slow(request, callback):
            callback(Step(name="plan", ms=1, detail="Population"))
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.append(True)
        with patch("hagbot.pipeline.settings", replace(settings, answer_timeout_s=.01)), patch.object(pipeline, "_answer", new=slow):
            answer = await pipeline.answer(AskRequest(question="Population?"), on_step=events.append)
        self.assertEqual(answer.status, "error")
        self.assertEqual(answer.issues[0].code, "answer_timeout")
        self.assertEqual(answer.parts + answer.sources + answer.claims, [])
        self.assertTrue(cancelled)
        self.assertEqual([s.name for s in events], ["plan", "error"])

    async def test_blocking_search_does_not_block_request_deadline(self):
        gate = threading.Event()
        catalog = CatalogStub()
        def blocked_search(query, depth):
            gate.wait(2)
            return {"bm25": ["TEST001"]}
        catalog.rankings = blocked_search
        llm = ScriptedLLM(Plan(sub_questions=[SubQuestion(question="Population?", search_query="population")]))
        pipeline = Pipeline(llm=llm, catalog=catalog, source=SourceStub())
        started = time.monotonic()
        try:
            with patch("hagbot.pipeline.settings", replace(settings, answer_timeout_s=.03)):
                answer = await pipeline.answer(AskRequest(question="Population?"))
            self.assertEqual(answer.issues[0].code, "answer_timeout")
            self.assertLess(time.monotonic() - started, 1)
        finally:
            gate.set()

    async def test_stream_finishes_with_explicit_timeout_outcome(self):
        from hagbot import api
        pipeline = Pipeline(llm=ScriptedLLM(), catalog=CatalogStub(), source=SourceStub())
        async def slow(*args):
            await asyncio.Event().wait()
        with patch.object(api, "pipeline", pipeline), patch.object(api, "_log_question"), \
             patch.object(pipeline, "_answer", new=slow), patch("hagbot.pipeline.settings", replace(settings, answer_timeout_s=.01)):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app), base_url="http://test") as client:
                response = await client.post("/api/ask/stream", json={"question": "Population?"})
        events = [json.loads(line) for line in response.text.splitlines()]
        self.assertEqual(events[-1]["type"], "answer")
        self.assertEqual(events[-1]["answer"]["issues"][0]["code"], "answer_timeout")

    async def test_final_failure_drops_already_prepared_claims(self):
        from unittest.mock import AsyncMock
        pipeline = Pipeline(llm=ScriptedLLM(Plan(sub_questions=[SubQuestion(question="Population?", search_query="population")])),
                            catalog=CatalogStub(), source=SourceStub())
        with patch.object(pipeline, "_answer_part", new=AsyncMock(return_value=part())), \
             patch("hagbot.pipeline.comparison_chart", side_effect=RuntimeError("synthetic chart error")):
            answer = await pipeline.answer(AskRequest(question="Population?"))
        self.assertEqual(answer.status, "error")
        self.assertEqual(answer.parts + answer.claims + answer.sources, [])

    async def test_explicit_unavailable_period_is_not_replaced_or_called_out_of_scope(self):
        from test_semantic_compatibility import spec, requirement
        plan = Plan(sub_questions=[SubQuestion(question="Population in 2099?", search_query="population",
                                              specification=spec(requirement("measure", "population", quote="Population"),
                                                                 requirement("period", "2099")))])
        llm = ScriptedLLM(plan)
        answer = await Pipeline(llm=llm, catalog=CatalogStub(), source=SourceStub()).answer(
            AskRequest(question="Population in 2099?", table_id="TEST001"))
        self.assertEqual(answer.status, "unavailable")
        self.assertEqual(answer.issues[0].code, "requested_period_unavailable")
        self.assertEqual(llm.calls, 1)

    async def test_catalog_unit_hint_never_certifies_a_numeric_answer(self):
        from test_answer_integrity import selection, meta
        m = meta(categories=False)
        m.unit_source = "catalog"
        source = SourceStub(table_meta=m)
        llm = ScriptedLLM(Plan(sub_questions=[SubQuestion(question="Population?", search_query="population")]),
                          selection(selections={"Year": ["2025"]}, operation="value"))
        answer = await Pipeline(llm=llm, catalog=CatalogStub(), source=source).answer(
            AskRequest(question="Population?", table_id="TEST001"))
        self.assertEqual(answer.status, "unverified")
        self.assertEqual(answer.issues[0].code, "unknown_unit")
        self.assertEqual(source.queries, [])


class EvaluationTests(unittest.TestCase):
    def answered(self):
        p = part()
        return Answer(question="Population?", language="en", status="answered", text="", parts=[p], sources=[p.table])

    def test_status_coverage_accuracy_and_latency_are_separate_metrics(self):
        case = {"expected_tables": ["TEST001"], "expected_values": [30]}
        correct = grade(case, self.answered())
        rejected = grade(case, Answer(question="Population?", language="en", status="unverified", text=""))
        wrong = self.answered()
        wrong.parts[0].facts[0].value = 99
        incorrect = grade(case, wrong)
        rows = [{**score, "status": status, "seconds": seconds, "llm_calls": calls, "retries": retries}
                for score, status, seconds, calls, retries in [(correct, "answered", 10, 3, 0),
                                                             (rejected, "unverified", 29, 6, 2),
                                                             (incorrect, "answered", 20, 4, 1)]]
        summary = summarize(rows)
        self.assertEqual(summary["correct"], 1)
        self.assertEqual(summary["known_wrong_answers"], 1)
        self.assertEqual(summary["false_refusals"], 1)
        self.assertEqual(summary["answer_precision"], .5)
        self.assertEqual(summary["latency_p95_s"], 29)

    def test_matching_numbers_with_wrong_unit_period_or_calculation_do_not_pass(self):
        case = {"expected_tables": ["TEST001"], "expected_facts": [{"value": 30, "unit": "ISK", "period": "2024"}]}
        self.assertFalse(grade(case, self.answered())["correct"])
        self.assertFalse(grade({"expected_tables": ["TEST001"], "expected_result": 30}, self.answered())["correct"])

    def test_table_only_labels_do_not_claim_answer_accuracy(self):
        self.assertIsNone(grade({"expected_tables": ["TEST001"]}, self.answered())["correct"])
        self.assertTrue(grade({"expected_status": "clarify"}, self.answered())["wrong_answer"])

    def test_mcp_preserves_bound_claims_and_comparison_context(self):
        from hagbot.claims import build_claims
        from mcp_server.adapter import _answer
        answer = self.answered()
        answer.claims = build_claims(answer.parts, "en")
        adapted = _answer(answer.model_dump())
        self.assertEqual(adapted["claims"][0]["values"], [30])
        self.assertEqual(adapted["parts"][0]["context"]["periods"], ["2025"])
        self.assertTrue(adapted["numbers_checked"])
        answer.status = "unverified"
        rejected = _answer(answer.model_dump())
        self.assertEqual(rejected["claims"], [])
        self.assertNotIn("parts", rejected)
        self.assertEqual(rejected["sources"], [])
        self.assertFalse(rejected["numbers_checked"])


if __name__ == "__main__":
    unittest.main()
