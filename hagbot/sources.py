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
from typing import Literal, Protocol
from datetime import datetime, timezone

import httpx
from pydantic import BaseModel

from .catalog import Catalog
from .config import settings
from .schemas import Row
from .outcomes import AnswerFailure


class Variable(BaseModel):
    code: str
    text: str
    values: list[str]
    value_texts: list[str]
    time: bool = False
    elimination: bool = False
    notes: list[str] = []
    value_notes: dict[str, list[str]] = {}

    def label_of(self, code: str) -> str:
        return self.value_texts[self.values.index(code)]


class TableMeta(BaseModel):
    id: str
    title: str
    title_source: Literal["source", "catalog", "unknown"] = "source"
    path: str
    unit: str = ""
    unit_source: Literal["source", "catalog", "unknown"] = "unknown"
    description: str = ""
    source: str = ""
    notes: list[str] = []
    updated: str = ""
    source_kind: Literal["unknown", "fixture", "snapshot", "live"] = "unknown"
    retrieved_at: str = ""
    snapshot_at: str = ""
    revision: str = ""
    dataset_scope: dict[str, str] = {}
    variables: list[Variable]

    def var(self, code: str) -> Variable:
        return next(v for v in self.variables if v.code == code)


class SourceError(RuntimeError):
    pass


def _check_metadata(expected: TableMeta, current: TableMeta) -> None:
    if expected.model_dump(exclude={"retrieved_at"}) != current.model_dump(exclude={"retrieved_at"}):
        raise AnswerFailure("unverified", "source_metadata_changed", expected.id)


def _check_size(meta: TableMeta, selection: dict[str, list[str]]) -> None:
    if prod(len(selection.get(v.code, v.values)) for v in meta.variables) > settings.query_cell_limit:
        raise AnswerFailure("clarify", "query_too_large", str(settings.query_cell_limit))


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
        return TableMeta(**{**t["meta"], "unit": t.get("unit", ""), "unit_source": "source", "source_kind": "fixture",
                            "dataset_scope": {"geography": settings.dataset_geography} if settings.dataset_geography else {}})

    async def query(self, table_id: str, selection: dict[str, list[str]]) -> list[Row]:
        meta = await self.meta(table_id)
        _check_size(meta, selection)
        cells = [
            (r["key"], r["value"])
            for r in self._get(table_id)["rows"]
            if all(r["key"].get(code) in vals for code, vals in selection.items())
        ]
        return _rows_from_cells(meta, cells)

    async def query_checked(self, meta: TableMeta, selection: dict[str, list[str]]) -> list[Row]:
        _check_metadata(meta, await self.meta(meta.id))
        return await self.query(meta.id, selection)


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
        ttl = settings.metadata_ttl_s if method == "GET" else settings.data_ttl_s
        if cached.exists() and 0 <= time.time() - cached.stat().st_mtime < ttl:
            try:
                return json.loads(cached.read_text())
            except (ValueError, OSError):
                pass
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
            temporary = cached.with_suffix(f".{id(asyncio.current_task())}.tmp")
            temporary.write_text(json.dumps(data, ensure_ascii=False))
            temporary.replace(cached)
            return data
        raise SourceError("PX-API kept answering 429 (rate limited). Try again later or use DATA_SOURCE=fixtures.")

    async def meta(self, table_id: str) -> TableMeta:
        d = await self._request("GET", self._api_url(table_id))
        meta = _meta_from_api(table_id, d, self.catalog)
        meta.source_kind = "live"
        key = hashlib.sha1(f"GET {self._api_url(table_id)} null".encode()).hexdigest()
        cached = settings.cache_dir / f"{key}.json"
        meta.retrieved_at = datetime.fromtimestamp(cached.stat().st_mtime, timezone.utc).isoformat() if cached.exists() else ""
        from .profiles import enrich_live_metadata
        return enrich_live_metadata(meta, settings.profiles_path, self.lang, settings.metadata_ttl_s)

    async def warm_definitions(self, table_id: str) -> TableMeta:
        """Offline provisioning: a one-cell PX export exposes source definitions.

        The cell is discarded. This is deliberately outside the question path;
        rejected answer candidates never trigger a numeric query for enrichment.
        """
        meta = _meta_from_api(table_id, await self._request("GET", self._api_url(table_id)), self.catalog)
        if any(not v.values for v in meta.variables):
            raise SourceError(f"{table_id}: empty dimension")
        body = {"query": [{"code": v.code, "selection": {"filter": "item", "values": [v.values[-1] if v.time else v.values[0]]}}
                          for v in meta.variables], "response": {"format": "px"}}
        for attempt in range(4):
            async with self._lock:
                delay = settings.px_min_interval_s - (time.monotonic() - self._last)
                if delay > 0:
                    await asyncio.sleep(delay)
                response = await self.client.post(self._api_url(table_id), json=body)
                self._last = time.monotonic()
            if response.status_code == 429:
                await asyncio.sleep(float(response.headers.get("Retry-After", 10)) * (attempt + 1))
                continue
            if response.status_code >= 400:
                raise SourceError(f"PX definitions {response.status_code}: {response.text[:200]}")
            marker = re.search(rb"(?m)^DATA\s*=", response.content)
            if marker is None:
                raise SourceError(f"{table_id}: PX export has no DATA marker")
            _apply_definitions(meta, _px_definitions(response.content[:marker.start()], self.lang, meta))
            meta.source_kind, meta.retrieved_at = "live", datetime.now(timezone.utc).isoformat()
            return meta
        raise SourceError("PX definitions rate limited")

    async def query(self, table_id: str, selection: dict[str, list[str]]) -> list[Row]:
        return await self.query_checked(await self.meta(table_id), selection)

    async def query_checked(self, meta: TableMeta, selection: dict[str, list[str]]) -> list[Row]:
        _check_metadata(meta, await self.meta(meta.id))
        _check_size(meta, selection)
        body = {
            "query": [{"code": c, "selection": {"filter": "item", "values": v}} for c, v in selection.items()],
            "response": {"format": "json-stat2"},
        }
        d = await self._request("POST", self._api_url(meta.id), body)
        for code in d.get("id", []):
            if code not in {v.code for v in meta.variables}:
                raise AnswerFailure("unverified", "source_metadata_changed", meta.id)
            var = meta.var(code)
            labels = d.get("dimension", {}).get(code, {}).get("category", {}).get("label", {})
            if isinstance(labels, dict) and any(c not in var.values or " ".join(str(label).split()) != " ".join(var.label_of(c).split())
                                                for c, label in labels.items()):
                raise AnswerFailure("unverified", "source_metadata_changed", f"{meta.id}: {code}")
        if meta.updated and isinstance(d.get("updated"), str):
            stamps = [re.sub(r"\D", "", s) for s in (meta.updated, d["updated"])]
            length = min(14, *(len(s) for s in stamps))
            if length >= 8 and stamps[0][:length] != stamps[1][:length]:
                raise AnswerFailure("unverified", "source_revision_changed", meta.id)
        return _rows_from_cells(meta, parse_jsonstat2(d))


def _meta_from_api(table_id: str, d: dict, catalog: Catalog) -> TableMeta:
    """TableMeta from the API's GET answer (the snapshot's metadata.json is the same document)."""
    entry = catalog.get(table_id)
    # some tables leave the time flag off their Month or Year dimension (VIN00001, LAU04000); values that all read
    # as periods (2026M08, 2026Q2, 2025) are a time dimension whatever the flag says
    looks_like_time = lambda vals: bool(vals) and all(re.fullmatch(r"(?:18|19|20)\d\d(?:[MQ]\d{1,2})?", x) for x in vals)
    variables = [
        Variable(code=v["code"], text=v["text"], values=v["values"], value_texts=v["valueTexts"],
                 time=v.get("time", False) or looks_like_time(v["values"]), elimination=v.get("elimination", False),
                 notes=_notes(v.get("notes", v.get("note", []))),
                 value_notes={c: _notes(n) for c, n in v.get("valueNotes", {}).items() if c in v["values"]}
                 if isinstance(v.get("valueNotes"), dict) else {})
        for v in d["variables"]
    ]
    unit = d.get("unit") if isinstance(d.get("unit"), str) else ""
    notes = _notes(d.get("notes", d.get("note", [])))
    return TableMeta(id=table_id, title=d.get("title") or (entry.title_en if entry else table_id),
                     title_source="source" if d.get("title") else "catalog" if entry else "unknown",
                     path=entry.path if entry else "", unit=unit or (entry.unit if entry else ""),
                     unit_source="source" if unit else "catalog" if entry and entry.unit else "unknown",
                     description=d.get("description") if isinstance(d.get("description"), str) else "",
                     source=d.get("source") if isinstance(d.get("source"), str) else "",
                     dataset_scope={"geography": settings.dataset_geography} if settings.dataset_geography else {},
                     notes=notes, updated=d.get("updated") if isinstance(d.get("updated"), str) else "",
                     variables=variables)


def _notes(value) -> list[str]:
    return [value] if isinstance(value, str) else [n for n in value if isinstance(n, str)] if isinstance(value, list) else []


_STATEMENT = re.compile(r'([A-Z][A-Z0-9-]*)((?:\[[a-z]{2}\])?(?:\((?:"[^"]*"|[^)"])*\))?)\s*=')
_CODEPAGES = {"windows-1252": "cp1252", "ansi": "cp1252"}


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


def _px_statements(head: bytes) -> dict[str, str]:
    head = head.removeprefix(b"\xef\xbb\xbf")
    enc = re.search(rb'CODEPAGE="([^"]+)"', head)
    enc = _CODEPAGES.get(enc.group(1).decode().lower(), enc.group(1).decode()) if enc else "utf-8"
    header, statements, pos = head.decode(enc, errors="replace"), {}, 0
    while (match := _STATEMENT.search(header, pos)):
        end, quoted = match.end(), False
        while end < len(header) and (quoted or header[end] != ";"):
            quoted ^= header[end] == '"'
            end += 1
        statements.setdefault(match.group(1) + match.group(2), header[match.end():end].strip())
        pos = end + 1
    return statements


def _px_metadata(path: Path, lang: str, meta: TableMeta | None = None) -> dict:
    """Read source definitions without loading the (potentially huge) numeric DATA section."""
    chunks = bytearray()
    with path.open("rb") as stream:
        while chunk := stream.read(65536):
            previous = len(chunks)
            chunks.extend(chunk)
            if marker := re.search(rb"(?m)^DATA\s*=", chunks[max(0, previous - 16):]):
                del chunks[max(0, previous - 16) + marker.start():]
                break
    return _px_definitions(bytes(chunks), lang, meta)


def _px_definitions(header: bytes, lang: str, meta: TableMeta | None = None) -> dict:
    statements = _px_statements(header)
    def text(key):
        # English snapshots also contain Icelandic: use an explicit translation
        # before the default language, never join definitions across languages.
        raw = statements.get(f"{key}[{lang}]", statements.get(key, ""))
        return " ".join(_px_items(raw))
    result = {"unit": text("UNITS"), "source": text("SOURCE"), "description": text("CONTENTS"), "note": text("NOTE")}
    if updated := text("LASTUPDATED"):
        result["updated"] = updated
    if meta is None:
        return result
    names = {}
    for var in meta.variables:
        names[var.code] = names[var.text] = var
    for key, raw in statements.items():
        if key.startswith("VARIABLECODE("):
            args, codes = _px_items(key[key.index("(") + 1:-1]), _px_items(raw)
            if args and codes and codes[0] in names:
                names[args[0]] = names[codes[0]]
    scoped = {}
    for key, raw in statements.items():
        match = re.fullmatch(r'(NOTE|VALUENOTE)(?:\[([a-z]{2})\])?\((.*)\)', key)
        if not match or match[2] not in (None, lang):
            continue
        args = _px_items(match[3])
        if not args or args[0] not in names:
            continue
        var = names[args[0]]
        value = None
        if match[1] == "VALUENOTE":
            if len(args) != 2:
                continue
            labels = _px_items(statements.get(f'VALUES[{lang}]("{args[0]}")',
                                             statements.get(f'VALUES("{args[0]}")', "")))
            codes = _px_items(statements.get(f'CODES("{args[0]}")', ""))
            candidates = {c for c, label in zip(var.values, var.value_texts) if args[1] in (c, label)}
            candidates |= {c for c, label in zip(codes, labels) if args[1] == label and c in var.values}
            if len(candidates) != 1:
                continue
            value = candidates.pop()
        identity = (var.code, value)
        if identity not in scoped or match[2] == lang:
            scoped[identity] = _px_items(raw)
    result["variable_notes"] = {code: notes for (code, value), notes in scoped.items() if value is None}
    result["value_notes"] = {}
    for (code, value), notes in scoped.items():
        if value is not None:
            result["value_notes"].setdefault(code, {})[value] = notes
    return result


def _apply_definitions(meta: TableMeta, extra: dict) -> TableMeta:
    if extra.get("unit"):
        meta.unit, meta.unit_source = extra["unit"], "source"
    meta.source = meta.source or extra.get("source", "")
    meta.description = meta.description or extra.get("description", "")
    meta.updated = meta.updated or extra.get("updated", "")
    if extra.get("note") and extra["note"] not in meta.notes:
        meta.notes.append(extra["note"])
    for var in meta.variables:
        var.notes = list(dict.fromkeys([*var.notes, *extra.get("variable_notes", {}).get(var.code, [])]))
        for code, notes in extra.get("value_notes", {}).get(var.code, {}).items():
            var.value_notes[code] = list(dict.fromkeys([*var.value_notes.get(code, []), *notes]))
    return meta


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
        st = _px_statements(raw[:m.start()])
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

    def __init__(self, catalog: Catalog, root: Path | None = None, lang: str = "en", keep: int = 16,
                 freshness_policy: str | None = None):
        self.catalog, self.root, self.lang = catalog, root or settings.local_data_dir, lang
        self.policy = freshness_policy or settings.freshness_policy
        self.live = PxApiSource(catalog, lang)
        self._folders: dict[str, Path] = {}  # "<API path>/<table id>" -> folder holding metadata.json and data.px
        with (self.root / "catalogue.csv").open(encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                if r["language"] == lang:
                    key = r["api_url"].split(f"/api/v1/{lang}/", 1)[1].removesuffix(".px")
                    self._folders[key] = self.root / r["folder"]
        self._tables: OrderedDict[str, PxTable] = OrderedDict()  # the last few tables read, newest last
        self.keep = keep
        self._metadata: dict[str, TableMeta] = {}
        self._metadata_stamps: dict[str, str] = {}

    def _folder(self, table_id: str) -> Path | None:
        entry = self.catalog.get(table_id)
        return self._folders.get(f"{entry.path}/{table_id}") if entry else None

    async def meta(self, table_id: str) -> TableMeta:
        if getattr(self, "policy", settings.freshness_policy) == "live":
            return await self.live.meta(table_id)
        folder = self._folder(table_id)
        if not folder:
            return await self.live.meta(table_id)
        stamp = ":".join(f"{p.stat().st_mtime_ns}:{p.stat().st_size}" for p in (folder / "metadata.json", folder / "data.px"))
        if table_id not in self._metadata or getattr(self, "_metadata_stamps", {}).get(table_id) != stamp:
            meta = _meta_from_api(table_id, json.loads((folder / "metadata.json").read_text(encoding="utf-8-sig")),
                                  self.catalog)
            extra = await asyncio.to_thread(_px_metadata, folder / "data.px", self.lang, meta)
            _apply_definitions(meta, extra)
            meta.source_kind, meta.snapshot_at = "snapshot", settings.snapshot_at
            meta.revision = stamp
            self._metadata[table_id] = meta
            if not hasattr(self, "_metadata_stamps"):
                self._metadata_stamps = {}
            self._metadata_stamps[table_id] = stamp
            getattr(self, "_tables", {}).pop(table_id, None)
        return self._metadata[table_id].model_copy(deep=True)

    def _table(self, table_id: str, meta: TableMeta) -> PxTable:
        if table_id in self._tables:
            self._tables.move_to_end(table_id)
        else:
            self._tables[table_id] = PxTable(self._folder(table_id) / "data.px", meta)
            if len(self._tables) > self.keep:
                self._tables.popitem(last=False)
        return self._tables[table_id]

    async def query(self, table_id: str, selection: dict[str, list[str]]) -> list[Row]:
        if getattr(self, "policy", settings.freshness_policy) == "live":
            return await self.live.query(table_id, selection)
        if not self._folder(table_id):
            return await self.live.query(table_id, selection)
        meta = await self.meta(table_id)
        _check_size(meta, selection)
        if any(c not in meta.var(code).values for code, vals in selection.items() for c in vals):
            return await self.live.query(table_id, selection)  # e.g. a month published after the snapshot
        table = await asyncio.to_thread(self._table, table_id, meta)
        codes = [v.code for v in meta.variables]
        sel = [selection.get(c, meta.var(c).values) for c in codes]
        cells = [(dict(zip(codes, combo)), table.value(dict(zip(codes, combo)))) for combo in itertools.product(*sel)]
        return _rows_from_cells(meta, cells)

    async def query_checked(self, meta: TableMeta, selection: dict[str, list[str]]) -> list[Row]:
        if getattr(self, "policy", settings.freshness_policy) == "live" or not self._folder(meta.id):
            return await self.live.query_checked(meta, selection)
        _check_metadata(meta, await self.meta(meta.id))
        rows = await self.query(meta.id, selection)
        _check_metadata(meta, await self.meta(meta.id))
        return rows


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
