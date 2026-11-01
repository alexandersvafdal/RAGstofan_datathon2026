"""Where the numbers come from. Every source returns the same TableMeta and tidy Rows.

- FixtureSource: small offline tables in data/fixtures, for building before data access.
- PxApiSource: live Statistics Iceland PX-API (PxWeb v1), throttled and cached on disk.
- LocalPxSource: the organizers' snapshot of every table (data release of 18 October 2026) read from disk; no network,
  no rate limit, and the same numbers the official questions were checked against. Tables missing from the snapshot,
  and categories it does not have yet (a month published after 18 October), go to the live API.
"""

import asyncio
import csv
import hashlib
import itertools
import json
import re
import time
from collections import OrderedDict
from math import prod
from pathlib import Path
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
        if code == "__all__":   # every option summed in code (aggregate_rows): no row of the table carries it
            return "Total"
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
        return _meta_from_api(table_id, d, self.catalog)

    async def query(self, table_id: str, selection: dict[str, list[str]]) -> list[Row]:
        body = {
            "query": [{"code": c, "selection": {"filter": "item", "values": v}} for c, v in selection.items()],
            "response": {"format": "json-stat2"},
        }
        d = await self._request("POST", self._api_url(table_id), body)
        meta = await self.meta(table_id)
        return _rows_from_cells(meta, parse_jsonstat2(d))


def _meta_from_api(table_id: str, d: dict, catalog: Catalog) -> TableMeta:
    """TableMeta from the API's GET answer (the snapshot's metadata.json is the same document)."""
    entry = catalog.get(table_id)
    # some tables leave the time flag off their Month or Year dimension (VIN00001, LAU04000); values that all read
    # as periods (2026M08, 2026Q2, 2025) are a time dimension whatever the flag says
    looks_like_time = lambda vals: bool(vals) and all(re.fullmatch(r"(?:18|19|20)\d\d(?:[MQ]\d{1,2})?", x) for x in vals)
    variables = [
        Variable(code=v["code"], text=v["text"], values=v["values"], value_texts=v["valueTexts"],
                 time=v.get("time", False) or looks_like_time(v["values"]), elimination=v.get("elimination", False))
        for v in d["variables"]
    ]
    return TableMeta(id=table_id, title=d.get("title") or (entry.title_en if entry else table_id),
                     path=entry.path if entry else "", unit=entry.unit if entry else "",
                     variables=variables)


_STATEMENT = re.compile(r'([A-Z][A-Z0-9-]*)((?:\[[a-z]{2}\])?(?:\((?:"[^"]*"|[^)"])*\))?)\s*=')
_CODEPAGES = {"windows-1252": "cp1252", "ansi": "cp1252"}
# A unit that only says the figures are counts says nothing the label does not
_COUNT_UNIT = re.compile(r"^(number|count|persons?|people|fjoldi|einstaklingar|mannfjoldi|tala|-)$")
# What a real unit is made of: money, mass, energy, length, volume, time, a multiplier or a "per"
_UNIT_WORD = re.compile(r"isk|\bkr|kron|tonn|\bkg\b|gram|joule|watt|\bwh\b|hour|klst|\bkm\b|met(re|er)|lit(re|er)|percent|"
                        r"prosent|hlutfall|\bper\b|a hver|thousand|million|billion|thus|millj|milljard|hectare|hektar|"
                        r"\bha\b|head|cubic|rummet|degree|grad|\bdays?\b|dag|\byears?\b|\bar\b|month|manud|week|vik|minute|"
                        r"\bmin\b|second|sek|\bm2\b|\bm3\b|fermet|rummet")


def px_unit(path: Path, lang: str) -> str:
    """The table's unit from the px header (UNITS[en], else UNITS), when it is one unit: "Kilotonnes", "Million ISK".
    Empty for a count ("Number"), a compound unit of several measures ("Million ISK/percent") or a pointer ("See
    unit title"); "%" for a percentage."""
    from .text import normalize
    try:
        with path.open("rb") as f:
            raw = f.read(65536)
    except OSError:
        return ""
    enc = re.search(rb'CODEPAGE="([^"]+)"', raw)
    enc = _CODEPAGES.get(enc.group(1).decode().lower(), enc.group(1).decode()) if enc else "utf-8"
    head = raw.removeprefix(b"\xef\xbb\xbf").decode(enc, errors="replace")
    m = (re.search(rf'(?m)^UNITS\[{lang}\]="([^"]*)"', head) if lang != "is" else None) or re.search(r'(?m)^UNITS="([^"]*)"', head)
    unit = m.group(1).strip() if m else ""
    low = normalize(unit)
    if not unit or "/" in unit or "," in unit or " and " in low or len(unit.split()) > 3 or low.startswith(("see ", "sja ")) \
            or _COUNT_UNIT.match(low):   # several units, or a description ("Indices and annualized rates")
        return ""
    if re.fullmatch(r"(percent(age)?|prosent|hlutfall)( \(%\))?|%", low):
        return "%"
    return unit if _UNIT_WORD.search(low) else ""   # "Indices", "Ratio", "Various": the kind of measure, not a unit


def _px_items(value: str) -> list[str]:
    """Entries of a px list: "a","b",...; a long entry may be split into quoted pieces on several lines."""
    items, pieces = [], []
    for m in re.finditer(r'"([^"]*)"|,', value):
        if m.group(0) == ",":
            items.append("".join(pieces))
            pieces = []
        else:
            pieces.append(m.group(1))
    if pieces:
        items.append("".join(pieces))
    return items


class PxTable:
    """One data.px read into memory: its numbers, and for each dimension (in file order) its code and category codes.

    The numbers run row-major over STUB then HEADING dimensions, last dimension fastest. The file names dimensions by
    their Icelandic labels; VARIABLECODE, the other language's names and CODES tie them to the API's codes."""

    MISSING = {"..", ".", "...", "....", ":"}

    def __init__(self, path: Path, meta: TableMeta):
        raw = path.read_bytes()
        m = re.search(rb"(?m)^DATA=\s*", raw)
        if not m:
            raise SourceError(f"{path}: no DATA section")
        head = raw[: m.start()].removeprefix(b"\xef\xbb\xbf")
        enc = re.search(rb'CODEPAGE="([^"]+)"', head)
        enc = _CODEPAGES.get(enc.group(1).decode().lower(), enc.group(1).decode()) if enc else "utf-8"
        header = head.decode(enc, errors="replace")
        st: dict[str, str] = {}
        pos = 0
        while (sm := _STATEMENT.search(header, pos)):
            end, quoted = sm.end(), False
            while end < len(header) and (quoted or header[end] != ";"):
                quoted ^= header[end] == '"'
                end += 1
            st.setdefault(sm.group(1) + sm.group(2), header[sm.end():end].strip())
            pos = end + 1
        names = _px_items(st.get("STUB", "")) + _px_items(st.get("HEADING", ""))
        other = [[] for _ in names]
        for lang in {k[4:8] for k in st if k.startswith("STUB[")}:
            tr = _px_items(st.get(f"STUB{lang}", "")) + _px_items(st.get(f"HEADING{lang}", ""))
            if len(tr) == len(names):
                for i, n in enumerate(tr):
                    other[i].append(n)
        self.layout: list[tuple[str, list[str]]] = []
        for i, name in enumerate(names):
            declared = _px_items(st.get(f'VARIABLECODE("{name}")', ""))
            codes = _px_items(st.get(f'CODES("{name}")', ""))
            clues = (lambda v: bool(declared) and v.code == declared[0],
                     lambda v: v.text in {name, *other[i]} or v.code in {name, *other[i]},
                     lambda v: bool(codes) and v.values == codes)
            var = None
            for clue in clues:
                fits = [v for v in meta.variables if clue(v)]
                if len(fits) == 1:
                    var = fits[0]
                    break
            if var is None:
                raise SourceError(f"{path}: cannot match dimension {name!r} to the table description")
            self.layout.append((var.code, codes or var.values))
        tokens = raw[m.end():].decode("ascii", errors="replace").replace(";", " ").split()
        if len(tokens) != prod(len(c) for _, c in self.layout):
            raise SourceError(f"{path}: {len(tokens)} numbers, expected {prod(len(c) for _, c in self.layout)}")
        self.tokens = tokens

    def value(self, key: dict[str, str]) -> float | None:
        i = 0
        for code, cats in self.layout:
            i = i * len(cats) + cats.index(key[code])
        t = self.tokens[i].strip('"')
        return 0.0 if t == "-" else None if t in self.MISSING else float(t)


class LocalPxSource:
    """The snapshot on disk, with the live API behind it for anything the snapshot lacks."""

    def __init__(self, catalog: Catalog, root: Path | None = None, lang: str = "en", keep: int = 16):
        self.catalog, self.root, self.lang = catalog, root or settings.local_data_dir, lang
        self.live = PxApiSource(catalog, lang)
        self._folders: dict[str, Path] = {}  # "<API path>/<table id>" -> folder holding metadata.json and data.px
        with (self.root / "catalogue.csv").open(encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                if r["language"] == lang:
                    key = r["api_url"].split(f"/api/v1/{lang}/", 1)[1].removesuffix(".px")
                    self._folders[key] = self.root / r["folder"]
        self._tables: OrderedDict[str, PxTable] = OrderedDict()  # the last few tables read, newest last
        self.keep = keep

    def _folder(self, table_id: str) -> Path | None:
        entry = self.catalog.get(table_id)
        return self._folders.get(f"{entry.path}/{table_id}") if entry else None

    async def meta(self, table_id: str) -> TableMeta:
        folder = self._folder(table_id)
        if not folder:
            return await self.live.meta(table_id)
        meta = _meta_from_api(table_id, json.loads((folder / "metadata.json").read_text(encoding="utf-8-sig")),
                              self.catalog)
        if not meta.unit:   # the catalog carries no unit; the px file's UNITS does
            meta.unit = await asyncio.to_thread(px_unit, folder / "data.px", self.lang)
        return meta

    def _table(self, table_id: str, meta: TableMeta) -> PxTable:
        if table_id in self._tables:
            self._tables.move_to_end(table_id)
        else:
            self._tables[table_id] = PxTable(self._folder(table_id) / "data.px", meta)
            if len(self._tables) > self.keep:
                self._tables.popitem(last=False)
        return self._tables[table_id]

    async def query(self, table_id: str, selection: dict[str, list[str]]) -> list[Row]:
        if not self._folder(table_id):
            return await self.live.query(table_id, selection)
        meta = await self.meta(table_id)
        if any(c not in meta.var(code).values for code, vals in selection.items() for c in vals):
            return await self.live.query(table_id, selection)  # e.g. a month published after the snapshot
        table = await asyncio.to_thread(self._table, table_id, meta)
        codes = [v.code for v in meta.variables]
        sel = [selection.get(c) or meta.var(c).values for c in codes]
        cells = [(dict(zip(codes, combo)), table.value(dict(zip(codes, combo)))) for combo in itertools.product(*sel)]
        return _rows_from_cells(meta, cells)


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
    if settings.data_source == "local":
        return LocalPxSource(catalog)
    if settings.data_source == "pxapi":
        return PxApiSource(catalog)
    return FixtureSource()
