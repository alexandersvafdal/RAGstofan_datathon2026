"""Public source adapters are exercised with synthetic files and HTTP responses only."""

import json
import os
import tempfile
import time
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import httpx

from hagbot.catalog import Catalog
from hagbot.config import settings
from hagbot.outcomes import AnswerFailure
from hagbot.profiles import enrich_live_metadata, load_profiles, save_profiles
from hagbot.semantics import profile, validate_compatibility
from hagbot.sources import LocalPxSource, PxApiSource, SourceError, TableMeta, Variable, _px_definitions, _apply_definitions

from test_answer_integrity import CatalogStub, entry
from test_semantic_compatibility import proof, requirement, spec

API = {"title": "Population", "variables": [{"code": "Year", "text": "Year", "values": ["2025"], "valueTexts": ["2025"], "time": True}]}


def metadata():
    return TableMeta(id="TEST001", title="Population", path="People/population", source_kind="live",
                     retrieved_at=datetime.now(timezone.utc).isoformat(), unit="persons", unit_source="source",
                     variables=[Variable(code="Year", text="Year", values=["2025"], value_texts=["2025"], time=True)])


class ProfileTests(unittest.TestCase):
    def test_only_fresh_matching_live_profiles_supply_definitions(self):
        with tempfile.TemporaryDirectory() as folder:
            path, saved = Path(folder) / "profiles.json", metadata()
            saved.notes = ["Population on 1 January"]
            save_profiles(path, {"en:TEST001": saved})
            current = metadata().model_copy(update={"unit": "", "unit_source": "unknown"})
            self.assertEqual(enrich_live_metadata(current, path, "en", 60).notes, saved.notes)
            for kind, date, labels in [("snapshot", saved.retrieved_at, ["2025"]),
                                       ("live", "2000-01-01T00:00:00+00:00", ["2025"]),
                                       ("live", saved.retrieved_at, ["2024", "2025"])]:
                with self.subTest(kind=kind, date=date, labels=labels):
                    other = saved.model_copy(deep=True)
                    other.source_kind, other.retrieved_at = kind, date
                    other.variables[0].values = labels
                    other.variables[0].value_texts = labels
                    save_profiles(path, {"en:TEST001": other})
                    fresh = metadata().model_copy(update={"notes": []})
                    self.assertEqual(enrich_live_metadata(fresh, path, "en", 60).notes, [])
            self.assertEqual(load_profiles(path, "is"), {})

    def test_source_definitions_participate_in_discovery_without_changing_dictionary(self):
        a, b = entry("TABLE1"), entry("TABLE2")
        p = metadata().model_copy(update={"id": "TABLE2", "description": "Household balance sheets and liabilities"})
        catalog = Catalog([a, b], dense=[], profiles={"TABLE2": p})
        try:
            self.assertEqual(catalog.keyword_search("household liabilities")[0][0].id, "TABLE2")
            self.assertIn("Household", b.card())
        finally:
            catalog._pool.shutdown()

    def test_px_notes_remain_attached_to_their_variable_and_value(self):
        m = TableMeta(id="T", title="Income", path="P", variables=[
            Variable(code="Measure", text="Measure", values=["mean", "median"], value_texts=["Mean income", "Median income"])])
        header = (b'VARIABLECODE("Maelikvardi")="Measure";\nNOTE[en]("Maelikvardi")="Before tax";\n'
                  b'VALUENOTE[en]("Maelikvardi","median")="Median includes capital income";\n'
                  b'LASTUPDATED="20261018 09:00";\n')
        _apply_definitions(m, _px_definitions(header, "en", m))
        self.assertEqual(m.var("Measure").notes, ["Before tax"])
        self.assertEqual(m.updated, "20261018 09:00")
        self.assertEqual(profile(m).fields["note-value:Measure:median:0"], "Median includes capital income")
        with self.assertRaises(AnswerFailure) as error:
            validate_compatibility(m, {"Measure": ["mean"]}, spec(requirement("measure", "income")),
                                   proof([("note-value:Measure:median:0", "Median includes capital income")]))
        self.assertEqual(error.exception.code, "invalid_metadata_evidence")


class ApiFreshnessTests(unittest.IsolatedAsyncioTestCase):
    async def test_metadata_cache_expires_and_failed_refresh_never_serves_stale(self):
        with tempfile.TemporaryDirectory() as folder:
            config = replace(settings, cache_dir=Path(folder), profiles_path=Path(folder) / "profiles.json", metadata_ttl_s=60, px_min_interval_s=0)
            calls = []
            def handle(request):
                calls.append(request)
                return httpx.Response(200, json=API) if len(calls) == 1 else httpx.Response(503, text="offline")
            with patch("hagbot.sources.settings", config):
                source = PxApiSource(CatalogStub())
                await source.client.aclose()
                source.client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
                try:
                    first = await source.meta("TEST001")
                    second = await source.meta("TEST001")
                    self.assertEqual(len(calls), 1)
                    self.assertEqual(first.retrieved_at, second.retrieved_at)
                    cache = next(Path(folder).glob("*.json"))
                    os.utime(cache, (time.time() - 120, time.time() - 120))
                    with self.assertRaises(SourceError):
                        await source.meta("TEST001")
                    self.assertEqual(len(calls), 2)
                finally:
                    await source.client.aclose()

    async def test_data_cache_has_its_own_expiry(self):
        with tempfile.TemporaryDirectory() as folder:
            config = replace(settings, cache_dir=Path(folder), data_ttl_s=1, px_min_interval_s=0)
            calls = []
            def handle(request):
                calls.append(request)
                return httpx.Response(200, json={"value": [len(calls)]})
            with patch("hagbot.sources.settings", config):
                source = PxApiSource(CatalogStub())
                await source.client.aclose()
                source.client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
                try:
                    url = source._api_url("TEST001")
                    await source._request("POST", url, {"query": []})
                    await source._request("POST", url, {"query": []})
                    self.assertEqual(len(calls), 1)
                    cache = next(Path(folder).glob("*.json"))
                    os.utime(cache, (time.time() - 10, time.time() - 10))
                    self.assertEqual((await source._request("POST", url, {"query": []}))["value"], [2])
                finally:
                    await source.client.aclose()

    async def test_definition_warmer_uses_one_cell_and_keeps_full_schema(self):
        with tempfile.TemporaryDirectory() as folder:
            config = replace(settings, cache_dir=Path(folder), px_min_interval_s=0)
            posts = []
            def handle(request):
                if request.method == "GET":
                    return httpx.Response(200, json=API)
                posts.append(json.loads(request.content))
                return httpx.Response(200, content=b'UNITS[en]="Persons";\nNOTE[en]="On 1 January";\nDATA=1;\n')
            with patch("hagbot.sources.settings", config):
                source = PxApiSource(CatalogStub())
                await source.client.aclose()
                source.client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
                try:
                    m = await source.warm_definitions("TEST001")
                    self.assertEqual(posts[0]["response"]["format"], "px")
                    self.assertTrue(all(len(q["selection"]["values"]) == 1 for q in posts[0]["query"]))
                    self.assertEqual(m.unit, "Persons")
                    self.assertEqual(m.notes, ["On 1 January"])
                finally:
                    await source.client.aclose()

    async def test_source_revision_change_prevents_answering(self):
        from unittest.mock import AsyncMock
        with tempfile.TemporaryDirectory() as folder, patch("hagbot.sources.settings", replace(settings, cache_dir=Path(folder))):
            source = PxApiSource(CatalogStub())
            old, new = metadata(), metadata()
            old.updated, new.updated = "2026-10-01", "2026-11-01"
            try:
                with patch.object(source, "meta", new=AsyncMock(return_value=new)), patch.object(source, "_request", new_callable=AsyncMock) as fetch:
                    with self.assertRaises(AnswerFailure) as error:
                        await source.query_checked(old, {"Year": ["2025"]})
                    self.assertEqual(error.exception.code, "source_metadata_changed")
                    fetch.assert_not_called()
            finally:
                await source.client.aclose()

    async def test_data_response_cannot_rename_a_code_after_metadata_validation(self):
        from unittest.mock import AsyncMock
        with tempfile.TemporaryDirectory() as folder, patch("hagbot.sources.settings", replace(settings, cache_dir=Path(folder))):
            source = PxApiSource(CatalogStub())
            meta = metadata()
            payload = {"id": ["Year"], "dimension": {"Year": {"category": {"index": ["2025"], "label": {"2025": "2024"}}}}, "value": [10]}
            try:
                with patch.object(source, "meta", new=AsyncMock(return_value=meta)), patch.object(source, "_request", new=AsyncMock(return_value=payload)):
                    with self.assertRaises(AnswerFailure) as error:
                        await source.query_checked(meta, {"Year": ["2025"]})
                    self.assertEqual(error.exception.code, "source_metadata_changed")
            finally:
                await source.client.aclose()

    async def test_snapshot_revision_invalidates_cached_metadata_before_data_access(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            (path / "metadata.json").write_text(json.dumps(API))
            (path / "data.px").write_text('UNITS="Persons";\nDATA=1;\n')
            source = LocalPxSource.__new__(LocalPxSource)
            source.catalog, source.lang, source.policy, source._metadata = CatalogStub(), "en", "snapshot", {}
            source._folder = lambda _: path
            original = await source.meta("TEST001")
            (path / "metadata.json").write_text(json.dumps({**API, "title": "Revised population estimates"}))
            with self.assertRaises(AnswerFailure) as error:
                await source.query_checked(original, {"Year": ["2025"]})
            self.assertEqual(error.exception.code, "source_metadata_changed")

    async def test_live_policy_never_mixes_live_metadata_with_snapshot_numbers(self):
        from unittest.mock import AsyncMock
        source = LocalPxSource.__new__(LocalPxSource)
        source.policy = "live"
        source.live = AsyncMock()
        source.live.meta.return_value = metadata()
        source.live.query_checked.return_value = []
        source._folder = lambda _: self.fail("live policy must not inspect snapshot folders")
        meta = await source.meta("TEST001")
        await source.query_checked(meta, {"Year": ["2025"]})
        source.live.query_checked.assert_awaited_once()

    async def test_oversized_query_stops_before_requesting_numeric_cells(self):
        from unittest.mock import AsyncMock
        with tempfile.TemporaryDirectory() as folder, patch("hagbot.sources.settings", replace(settings, cache_dir=Path(folder), query_cell_limit=1)):
            source = PxApiSource(CatalogStub())
            meta = metadata()
            meta.variables[0].values = meta.variables[0].value_texts = ["2024", "2025"]
            try:
                with patch.object(source, "meta", new=AsyncMock(return_value=meta)), patch.object(source, "_request", new_callable=AsyncMock) as fetch:
                    with self.assertRaises(AnswerFailure) as error:
                        await source.query_checked(meta, {"Year": ["2024", "2025"]})
                    self.assertEqual(error.exception.code, "query_too_large")
                    fetch.assert_not_called()
            finally:
                await source.client.aclose()


if __name__ == "__main__":
    unittest.main()
