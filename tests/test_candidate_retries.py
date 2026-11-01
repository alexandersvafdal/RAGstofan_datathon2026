"""A rejected source can lead to another table, never to a weaker question or an unchecked answer."""

import asyncio
import json
import unittest
from dataclasses import replace
from datetime import date
from unittest.mock import AsyncMock, patch

from hagbot.config import Settings, settings
from hagbot.outcomes import AnswerFailure
from hagbot.pipeline import Pipeline
from hagbot.schemas import AskRequest, Row
from hagbot.semantics import fingerprint
from hagbot.sources import SourceError
from hagbot.specification import Compatibility, RequirementCheck
from hagbot.steps.compose import Written
from hagbot.steps.plan import Plan, SubQuestion
from hagbot.steps.retrieve import Pick, Retrieved, RetrievalCache, retrieve
from hagbot.steps.select import resolve
from hagbot.steps.tiered import Subjects, Verdict, _browse, retrieve_tiered

from test_answer_integrity import CatalogStub, ScriptedLLM, entry, selection
from test_semantic_compatibility import proof, requirement, spec, table


class MultiSource:
    def __init__(self, count=4):
        self.tables = {f"TABLE{i}": table().model_copy(update={"id": f"TABLE{i}"}) for i in range(1, count + 1)}
        self.meta_calls, self.queries = [], []
        self.errors = {}

    async def meta(self, table_id):
        self.meta_calls.append(table_id)
        if table_id in self.errors:
            raise self.errors[table_id]
        return self.tables[table_id]

    async def query(self, table_id, selected):
        self.queries.append((table_id, {k: list(v) for k, v in selected.items()}))
        meta = self.tables[table_id]
        return [Row(key={"Year": "2025", "Measure": code}, labels={"Year": "2025", "Measure": meta.var("Measure").label_of(code)},
                    value=40 if code == "median" else 90) for code in selected["Measure"]]


def catalog(count=4):
    entries = [entry(f"TABLE{i}") for i in range(1, count + 1)]
    for e in entries:
        e.title_en, e.path = "Mean and median income", "People/income"
    return CatalogStub(*entries)


def specification():
    return spec(requirement("measure", "income", quote="tekna"), requirement("statistic", "median", quote="Miðgildi"),
                requirement("period", "2025"))


def planned():
    return Plan(sub_questions=[SubQuestion(question="Typical income in 2025?", search_query="income", specification=specification())])


def selected(metric="median", **fields):
    fields.setdefault("compatibility", proof([("title", "income")],
        [("value:Measure:median", "Median income")] if metric == "median" else [("title", "median income")],
        [("value:Year:2025", "2025")]))
    return selection(selections={"Year": ["2025"], "Measure": [metric]}, operation="value", **fields)


def pick(mode, table_id):
    return Verdict(table_id=table_id) if mode == "tiered" else Pick(table_id=table_id, confidence="high")


class CandidateRetryTests(unittest.IsolatedAsyncioTestCase):
    async def answer(self, mode, replies, *, count=4, limit=3, forced=None, source=None):
        source, cards = source or MultiSource(count), catalog(count)
        llm = ScriptedLLM(*replies)
        pipeline = Pipeline(llm=llm, source=source, catalog=cards)
        config = replace(settings, retrieve_max_attempts=limit, retrieve_top_k=15)
        with patch("hagbot.pipeline.settings", config), patch("hagbot.steps.tiered.voting_on", return_value=False), \
             patch("hagbot.steps.select.voting_on", return_value=False):
            answer = await pipeline.answer(AskRequest(question="Miðgildi tekna árið 2025?", language="en", table_id=forced))
        return answer, source, cards, llm

    async def test_second_table_must_pass_the_same_requirements_before_fetch(self):
        for mode in ("tiered",):
            with self.subTest(mode=mode):
                answer, source, cards, llm = await self.answer(mode, [planned(), pick(mode, "TABLE1"), selected("mean"),
                    pick(mode, "TABLE2"), selected(), Written(text="Median income in 2025: 40 ISK.")])
                self.assertEqual(answer.status, "answered")
                self.assertEqual(source.meta_calls, ["TABLE1", "TABLE2"])
                self.assertEqual([table_id for table_id, _ in source.queries], ["TABLE2"])
                self.assertEqual(len(cards.queries), 1)  # no duplicate BM25 or vector/embedding search
                self.assertEqual(llm.calls, 6)
                self.assertNotIn("TABLE1:", llm.prompts[3][1])
                self.assertIn(specification().model_dump_json(), llm.prompts[2][1])
                self.assertIn(specification().model_dump_json(), llm.prompts[4][1])
                attempts = answer.parts[0].attempts
                self.assertEqual([(a.table_id, a.status) for a in attempts], [("TABLE1", "rejected"), ("TABLE2", "verified")])
                self.assertEqual(attempts[0].code, "incompatible_definition")
                self.assertEqual({a.question_sha256 for a in attempts}, {fingerprint(specification().model_dump())})
                self.assertEqual(attempts[1].metadata_sha256, answer.parts[0].verifications[0].metadata_sha256)
                self.assertTrue(any(step.name == "retry" for step in answer.steps))

    async def test_named_month_can_use_another_table_without_substituting_a_period(self):
        s = spec(requirement("measure", "income", quote="tekna"), requirement("statistic", "median", quote="Miðgildi"),
                 requirement("period", "2025M09", quote="september 2025"))
        plan = Plan(sub_questions=[SubQuestion(question="Typical income in September 2025?", search_query="income", specification=s)])
        source, cards = MultiSource(2), catalog(2)
        for table_id, period in (("TABLE1", "2025M08"), ("TABLE2", "2025M09")):
            source.tables[table_id].var("Year").values = [period]
            source.tables[table_id].var("Year").value_texts = [period]

        async def query(table_id, selected):
            source.queries.append((table_id, selected))
            return [Row(key={"Year": "2025M09", "Measure": "median"},
                        labels={"Year": "2025M09", "Measure": "Median income"}, value=40)]

        source.query = query
        picked = selection(selections={"Year": ["2025M09"], "Measure": ["median"]}, operation="value", compatibility=proof(
            [("title", "income")], [("value:Measure:median", "Median income")], [("value:Year:2025M09", "2025M09")]))
        llm = ScriptedLLM(plan, Verdict(table_id="TABLE1"), Verdict(table_id="TABLE2"), picked,
                          Written(text="Median income in 2025M09: 40 ISK."))
        with patch("hagbot.steps.tiered.voting_on", return_value=False), patch("hagbot.steps.select.voting_on", return_value=False):
            answer = await Pipeline(llm=llm, catalog=cards, source=source).answer(
                AskRequest(question="Miðgildi tekna í september 2025?", language="is"))
        self.assertEqual(answer.status, "answered")
        self.assertEqual([table_id for table_id, _ in source.queries], ["TABLE2"])
        self.assertEqual(answer.parts[0].selection["Year"], ["2025M09"])
        self.assertEqual(answer.parts[0].attempts[0].code, "requested_period_unavailable")
        self.assertEqual(llm.calls, 5)

    async def test_candidate_limit_is_an_abstention_not_a_best_effort_answer(self):
        for mode in ("tiered",):
            with self.subTest(mode=mode):
                replies = [planned()]
                for i in range(1, 4):
                    replies.extend([pick(mode, f"TABLE{i}"), selected("mean")])
                answer, source, cards, llm = await self.answer(mode, replies)
                self.assertEqual(answer.status, "unverified")
                self.assertEqual(answer.issues[0].code, "candidate_limit_reached")
                self.assertEqual([a.table_id for a in answer.issues[0].attempts], ["TABLE1", "TABLE2", "TABLE3"])
                self.assertEqual(source.meta_calls, ["TABLE1", "TABLE2", "TABLE3"])
                self.assertEqual(source.queries, [])
                self.assertEqual(answer.parts, [])
                self.assertEqual(answer.sources, [])
                self.assertEqual(len(cards.queries), 1)
                self.assertEqual(llm.calls, 7)

    async def test_empty_remaining_pool_keeps_rejections_without_forcing_a_choice(self):
        for mode in ("tiered",):
            with self.subTest(mode=mode):
                answer, source, _, llm = await self.answer(mode, [planned(), pick(mode, "TABLE1"), selected("mean")], count=1)
                self.assertEqual(answer.status, "unverified")
                self.assertEqual(answer.issues[0].code, "no_compatible_candidate")
                self.assertEqual(len(answer.issues[0].attempts), 1)
                self.assertEqual(source.queries, [])
                self.assertEqual(llm.calls, 3)

    async def test_explicit_none_on_retry_does_not_trigger_a_forced_pick(self):
        for mode in ("tiered",):
            with self.subTest(mode=mode):
                replies = [planned(), pick(mode, "TABLE1"), selected("mean"), pick(mode, "none")]
                if mode == "tiered":
                    replies.append(Subjects(subjects=[]))
                answer, source, _, _ = await self.answer(mode, replies, count=2)
                self.assertEqual(answer.status, "unverified")
                self.assertEqual(answer.issues[0].code, "no_compatible_candidate")
                self.assertEqual(source.meta_calls, ["TABLE1"])
                self.assertEqual(source.queries, [])

    async def test_forced_table_is_never_replaced(self):
        for mode in ("tiered",):
            with self.subTest(mode=mode):
                answer, source, _, llm = await self.answer(mode, [planned(), selected("mean")], forced="TABLE1")
                self.assertEqual(answer.status, "unavailable")
                self.assertEqual(answer.issues[0].code, "incompatible_definition")
                self.assertEqual(source.meta_calls, ["TABLE1"])
                self.assertEqual(llm.calls, 2)

    async def test_bad_model_codes_stop_without_trying_other_tables(self):
        answer, source, _, llm = await self.answer("tiered", [planned(), pick("tiered", "TABLE1"), selected("INVENTED")])
        self.assertEqual(answer.status, "unverified")
        self.assertEqual(answer.issues[0].code, "invalid_selection")
        self.assertEqual(source.meta_calls, ["TABLE1"])
        self.assertEqual(source.queries, [])
        self.assertEqual(llm.calls, 3)

    async def test_ambiguity_stops_without_trying_other_tables(self):
        missing_measure = selection(selections={"Year": ["2025"]}, operation="value")
        answer, source, _, _ = await self.answer("tiered", [planned(), pick("tiered", "TABLE1"), missing_measure])
        self.assertEqual(answer.status, "clarify")
        self.assertEqual(answer.issues[0].code, "missing_filter")
        self.assertEqual(source.meta_calls, ["TABLE1"])
        self.assertEqual(source.queries, [])

    async def test_unknown_metadata_can_lead_to_a_better_documented_table(self):
        unknown = selected(compatibility=Compatibility(checks=[
            RequirementCheck(requirement=i, verdict="unknown") for i in range(3)]))
        answer, source, _, _ = await self.answer("tiered", [planned(), pick("tiered", "TABLE1"), unknown,
            pick("tiered", "TABLE2"), selected(), Written(text="Median income in 2025: 40 ISK.")])
        self.assertEqual(answer.status, "answered")
        self.assertEqual(answer.parts[0].attempts[0].code, "unknown_compatibility")
        self.assertEqual([table_id for table_id, _ in source.queries], ["TABLE2"])

    async def test_technical_failure_does_not_become_a_search_for_different_data(self):
        source = MultiSource()
        source.errors["TABLE1"] = SourceError("service unavailable")
        answer, source, _, llm = await self.answer("tiered", [planned(), pick("tiered", "TABLE1")], source=source)
        self.assertEqual(answer.status, "error")
        self.assertEqual(answer.issues[0].attempts[0].status, "error")
        self.assertEqual(source.meta_calls, ["TABLE1"])
        self.assertEqual(source.queries, [])
        self.assertEqual(llm.calls, 2)

    async def test_wrong_metadata_id_stops_immediately(self):
        source = MultiSource()
        source.tables["TABLE1"] = source.tables["TABLE2"]
        answer, source, _, _ = await self.answer("tiered", [planned(), pick("tiered", "TABLE1")], source=source)
        self.assertEqual(answer.status, "unverified")
        self.assertEqual(answer.issues[0].code, "mismatched_table_metadata")
        self.assertEqual(source.meta_calls, ["TABLE1"])
        self.assertEqual(source.queries, [])

    async def test_a_retriever_cannot_repeat_a_rejected_candidate(self):
        for mode, name in (("tiered", "retrieve_tiered"),):
            with self.subTest(mode=mode):
                repeated = Retrieved(table=catalog().get("TABLE1"), candidates=["TABLE1", "TABLE2"])
                with patch(f"hagbot.pipeline.{name}", new=AsyncMock(return_value=repeated)) as retriever:
                    answer, source, _, _ = await self.answer(mode, [planned(), selected("mean")])
                self.assertEqual(answer.status, "unverified")
                self.assertEqual(answer.issues[0].code, "repeated_candidate")
                self.assertEqual(source.meta_calls, ["TABLE1"])
                self.assertEqual(retriever.call_args_list[0].kwargs["excluded"], frozenset())
                self.assertEqual(retriever.call_args_list[1].kwargs["excluded"], frozenset({"TABLE1"}))

    async def test_sub_questions_have_independent_exclusions_and_caches(self):
        source, cards = MultiSource(), catalog()
        pipeline = Pipeline(llm=ScriptedLLM(), source=source, catalog=cards)
        specs = {name: spec(requirement("measure", "income"), requirement("statistic", statistic))
                 for name, statistic in (("Typical income part one?", "median"), ("Typical income part two?", "mean"))}
        calls = {name: [] for name in specs}

        async def choose(llm, catalog, question, query, k, forced, original, *, excluded, cache):
            calls[question].append((excluded, cache))
            table_id = "TABLE2" if "TABLE1" in excluded else "TABLE1"
            return Retrieved(table=cards.get(table_id), candidates=list(cards.by_id))

        async def select_query(llm, meta, question, original, *, filter_question, specification):
            metric = "median" if meta.id == "TABLE2" else "mean"
            wanted = specification.requirements[1].value
            evidence = proof([("title", "income")], [("title", f"{wanted} income")] if wanted == "median" else [("value:Measure:mean", "Mean income")])
            return resolve(meta, selected(metric, compatibility=evidence), question, specification=specification)

        with patch("hagbot.pipeline.retrieve_tiered", side_effect=choose), patch("hagbot.pipeline.select", side_effect=select_query), \
             patch("hagbot.pipeline.settings", replace(settings, retrieve_max_attempts=3)):
            results = await asyncio.gather(*(pipeline._verified_query(
                SubQuestion(question=name, search_query="income", specification=s), "Mean and median income?", "en", None, [], False)
                for name, s in specs.items()))
        self.assertEqual([r[0].table.id for r in results], ["TABLE2", "TABLE1"])
        first, second = list(calls.values())
        self.assertEqual([excluded for excluded, _ in first], [frozenset(), frozenset({"TABLE1"})])
        self.assertEqual([excluded for excluded, _ in second], [frozenset()])
        self.assertIs(first[0][1], first[1][1])
        self.assertIsNot(first[0][1], second[0][1])

    async def test_freshness_note_does_not_recommend_a_rejected_table(self):
        cards, source = catalog(2), MultiSource(2)
        cards.get("TABLE1").last_period = "2026"
        pipeline = Pipeline(llm=ScriptedLLM(), catalog=cards, source=source)
        with patch("hagbot.pipeline.date") as clock:
            clock.today.return_value = date(2026, 11, 1)
            note = pipeline._freshness_note(source.tables["TABLE2"], {"Year": ["2025"]}, ["TABLE1", "TABLE2"],
                                            "Income?", "en", excluded={"TABLE1"})
        self.assertNotIn("TABLE1", note)

    async def test_json_and_stream_preserve_attempts_and_nonfatal_retry_events(self):
        import httpx
        from hagbot import api
        cases = [("/api/ask", "unverified", selected("mean")), ("/api/ask/stream", "answered", selected())]
        for path, expected_status, second_selection in cases:
            with self.subTest(path=path):
                replies = [planned(), pick("tiered", "TABLE1"), selected("mean"), pick("tiered", "TABLE2"), second_selection]
                if expected_status == "answered":
                    replies.append(Written(text="Median income in 2025: 40 ISK."))
                source, cards = MultiSource(), catalog()
                pipeline = Pipeline(llm=ScriptedLLM(*replies), catalog=cards, source=source)
                with patch.object(api, "pipeline", pipeline), patch.object(api, "_log_question"), \
                     patch("hagbot.pipeline.settings", replace(settings, retrieve_max_attempts=2)), \
                     patch("hagbot.steps.select.voting_on", return_value=False):
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app), base_url="http://test") as client:
                        response = await client.post(path, json={"question": "Miðgildi tekna árið 2025?", "language": "en"})
                self.assertEqual(response.status_code, 200)
                if path.endswith("stream"):
                    events = [json.loads(line) for line in response.text.splitlines()]
                    self.assertTrue(any(e.get("name") == "retry" for e in events))
                    self.assertFalse(any(e.get("name") == "error" for e in events))
                    payload = events[-1]["answer"]
                    attempts = payload["parts"][0]["attempts"]
                else:
                    payload = response.json()
                    attempts = payload["issues"][0]["attempts"]
                self.assertEqual(payload["status"], expected_status)
                self.assertEqual([a["table_id"] for a in attempts], ["TABLE1", "TABLE2"])


class ExclusionTests(unittest.IsolatedAsyncioTestCase):
    async def test_forced_excluded_table_is_not_returned(self):
        for retriever in (retrieve, retrieve_tiered):
            with self.subTest(retriever=retriever.__name__):
                with self.assertRaises(AnswerFailure) as error:
                    await retriever(ScriptedLLM(), catalog(), "Income?", "income", 15, "TABLE1", excluded={"TABLE1"})
                self.assertEqual(error.exception.code, "repeated_candidate")

    async def test_dictionary_headline_cannot_restore_a_rejected_table(self):
        cards = CatalogStub(entry("TABLE1"), entry("TABLE2"))
        concept = {"id": "population", "concept": "Population", "tables": ["TABLE1", "TABLE2"],
                   "search": "population", "_terms": ["population"]}
        llm = ScriptedLLM(Verdict(table_id="TABLE2"))
        with patch("hagbot.steps.tiered.match_concepts", return_value=[concept]), \
             patch("hagbot.steps.tiered.voting_on", return_value=False):
            result = await retrieve_tiered(llm, cards, "Population?", "population", 15, excluded={"TABLE1"}, cache=RetrievalCache())
        self.assertEqual(result.table.id, "TABLE2")
        self.assertNotIn("TABLE1", result.candidates)
        self.assertEqual(llm.calls, 1)

    async def test_subject_browse_excludes_rejected_candidates(self):
        llm = ScriptedLLM(Subjects(subjects=["People/income"]), Verdict(table_id="TABLE2"))
        with patch("hagbot.steps.tiered.voting_on", return_value=False):
            found, pool = await _browse(llm, catalog(2), "Income?", "", excluded={"TABLE1"})
        self.assertEqual(found.id, "TABLE2")
        self.assertEqual(pool, ["TABLE2"])
        self.assertNotIn("TABLE1:", llm.prompts[-1][1])

    async def test_cache_is_not_shared_between_requests(self):
        cards = catalog()
        for _ in range(2):
            cache = RetrievalCache()
            await retrieve(ScriptedLLM(Pick(table_id="TABLE1", confidence="high")), cards, "Income?", "income", 15, cache=cache)
        self.assertEqual(len(cards.queries), 2)


class RetryConfigurationTests(unittest.TestCase):
    def test_attempt_limit_is_bounded(self):
        for limit in (0, -1, 6):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                Settings(retrieve_max_attempts=limit)
        self.assertEqual(Settings(retrieve_max_attempts=1).retrieve_max_attempts, 1)


if __name__ == "__main__":
    unittest.main()
