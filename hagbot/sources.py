"""Where the numbers come from. Every source returns the same TableMeta and tidy Rows.

- FixtureSource: small offline tables in data/fixtures, for building before data access.
- PxApiSource: live Statistics Iceland PX-API (PxWeb v1), throttled and cached on disk.
A LocalPxSource reading the kit's data.px files slots in here once the kit is released.
"""

import asyncio
import hashlib
import itertools
import json
import time
from typing import Protocol

import httpx
from pydantic import BaseModel

from .catalog import Catalog
from .config import settings
from .schemas import Row


class Variable(BaseModel):
    code: str
    text: str
    values: list[str]
    value_texts: list[str]
    time: bool = False
    elimination: bool = False

    def label_of(self, code: str) -> str:
        return self.value_texts[self.values.index(code)]


class TableMeta(BaseModel):
    id: str
    title: str
    path: str
    unit: str = ""
    variables: list[Variable]

    def var(self, code: str) -> Variable:
        return next(v for v in self.variables if v.code == code)


class SourceError(RuntimeError):
    pass


class DataSource(Protocol):
    async def meta(self, table_id: str) -> TableMeta: ...
    async def query(self, table_id: str, selection: dict[str, list[str]]) -> list[Row]: ...


def table_url(path: str, table_id: str) -> str:
    db, _, rest = path.partition("/")
    folder = f"{db}__{rest.replace('/', '__')}" if rest else db
    return f"{settings.px_base_url}/pxen/pxweb/en/{db}/{folder}/{table_id}.px"


def _rows_from_cells(meta: TableMeta, cells: list[tuple[dict[str, str], float | None]]) -> list[Row]:
    rows = []
    for key, value in cells:
        labels = {meta.var(c).text: meta.var(c).label_of(v) for c, v in key.items()}
        rows.append(Row(key=key, labels=labels, value=value))
    return rows


class FixtureSource:
    def __init__(self):
        self._tables: dict[str, dict] = {}
        for f in settings.fixtures_dir.glob("*.json"):
            t = json.loads(f.read_text())
            self._tables[t["meta"]["id"]] = t

    def _get(self, table_id: str) -> dict:
        if table_id not in self._tables:
            raise SourceError(f"No fixture data for table {table_id}")
        return self._tables[table_id]

    async def meta(self, table_id: str) -> TableMeta:
        t = self._get(table_id)
        return TableMeta(**t["meta"], unit=t.get("unit", ""))

    async def query(self, table_id: str, selection: dict[str, list[str]]) -> list[Row]:
        meta = await self.meta(table_id)
        cells = [
            (r["key"], r["value"])
            for r in self._get(table_id)["rows"]
            if all(r["key"].get(code) in vals for code, vals in selection.items())
        ]
        return _rows_from_cells(meta, cells)


class PxApiSource:
    def __init__(self, catalog: Catalog, lang: str = "en"):
        self.catalog = catalog
        self.lang = lang
        self.client = httpx.AsyncClient(timeout=30)
        self._lock = asyncio.Lock()
        self._last = 0.0
        settings.cache_dir.mkdir(parents=True, exist_ok=True)

    def _api_url(self, table_id: str) -> str:
        entry = self.catalog.get(table_id)
        if not entry:
            raise SourceError(f"Table {table_id} is not in the catalog")
        return f"{settings.px_base_url}/px{self.lang}/api/v1/{self.lang}/{entry.path}/{table_id}.px"

    async def _request(self, method: str, url: str, body: dict | None = None) -> dict:
        key = hashlib.sha1(f"{method} {url} {json.dumps(body, sort_keys=True)}".encode()).hexdigest()
        cached = settings.cache_dir / f"{key}.json"
        if cached.exists():
            return json.loads(cached.read_text())
        for attempt in range(4):
            async with self._lock:  # one request at a time, spaced out
                wait = settings.px_min_interval_s - (time.monotonic() - self._last)
                if wait > 0:
                    await asyncio.sleep(wait)
                r = await self.client.request(method, url, json=body)
                self._last = time.monotonic()
            if r.status_code == 429:
                await asyncio.sleep(float(r.headers.get("Retry-After", 10)) * (attempt + 1))
                continue
            if r.status_code >= 400:
                raise SourceError(f"PX-API {r.status_code}: {r.text[:200]}")
            data = r.json()
            cached.write_text(json.dumps(data, ensure_ascii=False))
            return data
        raise SourceError("PX-API kept answering 429 (rate limited). Try again later or use DATA_SOURCE=fixtures.")

    async def meta(self, table_id: str) -> TableMeta:
        d = await self._request("GET", self._api_url(table_id))
        entry = self.catalog.get(table_id)
        variables = [
            Variable(code=v["code"], text=v["text"], values=v["values"], value_texts=v["valueTexts"],
                     time=v.get("time", False), elimination=v.get("elimination", False))
            for v in d["variables"]
        ]
        return TableMeta(id=table_id, title=d["title"], path=entry.path, unit=entry.unit, variables=variables)

    async def query(self, table_id: str, selection: dict[str, list[str]]) -> list[Row]:
        body = {
            "query": [{"code": c, "selection": {"filter": "item", "values": v}} for c, v in selection.items()],
            "response": {"format": "json-stat2"},
        }
        d = await self._request("POST", self._api_url(table_id), body)
        meta = await self.meta(table_id)
        return _rows_from_cells(meta, parse_jsonstat2(d))


def parse_jsonstat2(d: dict) -> list[tuple[dict[str, str], float | None]]:
    """Flatten a json-stat2 dataset into (key, value) cells, row-major over d['id']."""
    ids, dims = d["id"], d["dimension"]
    codes_per_dim = []
    for dim_id in ids:
        index = dims[dim_id]["category"]["index"]
        codes_per_dim.append(index if isinstance(index, list) else sorted(index, key=index.get))
    values = d["value"]
    out = []
    for i, combo in enumerate(itertools.product(*codes_per_dim)):
        v = values.get(str(i)) if isinstance(values, dict) else values[i]
        out.append((dict(zip(ids, combo)), v))
    return out


def make_source(catalog: Catalog) -> DataSource:
    if settings.data_source == "pxapi":
        return PxApiSource(catalog)
    return FixtureSource()
