"""The question read once. One model call says which figures answer a question and how to show them; code does the
rest: it checks every choice against the tables' real options, fetches the numbers, computes, and writes the answer.

The model sees a handful of candidate tables (from the keyword dictionary and the search), each laid out as a family:
its measures (dimensions with no total: index or % change, overnight stays or arrivals; one option each) and its
groups (dimensions with a total, the default; options named only when the question names them). It answers with
picks (one per figure asked), a period, an operation and a shape. Anything it names that a table does not have is
dropped, never guessed. When no listed table has the figure, the old pipeline answers instead (None from read()).
"""

import json
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from .catalog import Catalog, CatalogEntry, _same_word
from .config import ROOT
from .llm import LLM
from .sources import TableMeta, Variable
from .steps.select import _options_for_prompt, _resolve_value, _total_of
from .steps.tiered import dictionary_tables, match_concepts
from .text import normalize, tokens

MAX_TABLES = 7
MAX_MEASURE_OPTIONS = 14

Shape = Literal["auto", "number", "line", "bars", "ranking", "stacked", "map", "heatmap", "table"]
Operation = Literal["value", "series", "change", "share", "per_inhabitant", "rank", "sum"]


class Pick(BaseModel):
    table: str = Field(description="T1, T2, … as listed")
    measures: dict[str, str] = Field(default_factory=dict, description="measure dimension -> one option, as listed")
    groups: dict[str, list[str]] = Field(default_factory=dict,
                                         description='group dimension -> options as listed, or ["all"]; leave out for its total')
    label: str = Field("", description="a short name for this figure, in the question's language")


class Reading(BaseModel):
    picks: list[Pick] = Field(default_factory=list)
    period_from: str = ""
    period_to: str = ""
    operation: Operation = "value"
    per: int = Field(0, description="per_inhabitant: 1 per inhabitant, 1000 per 1,000 inhabitants")
    top: int = Field(0, description="rank: how many to show; 0 for the default")
    shape: Shape = "auto"
    shape_from_question: bool = False
    none_fit: bool = False


READER = """You read one question about Iceland's official statistics and say, as one JSON object, which figures
answer it and how to show them. You do no arithmetic and write no answer: code fetches the numbers and computes.

The candidate tables are listed as T1, T2, … with their periods, their measures (dimensions without a total: choose
exactly one option for each) and their groups (dimensions whose default is their total: name options only when the
question asks for them).

- picks: one per figure asked. Two different measures (GDP and overnight stays, inflation and wage growth) are two
  picks, usually from two tables. Groups compared within one table (men and women, three towns) are one pick.
- measures: the original figure (a count, an amount, an index) unless the question asks for a change or a rate.
  Inflation is the annual % change of the consumer price index; "wage growth" the annual change of the wage index.
- groups: the options the question names, written as listed. ["all"] when it asks about each or every one of them,
  "by municipality", "which town", the largest or smallest, or a map. Leave a dimension out to get its total.
- period_from and period_to in the table's own format (2015, 2024M06, 2025Q1). Both empty: the latest period only.
  A trend or development with no start year: period_from "all". "Since 2015": period_from "2015". A change "from
  2020 to 2025": both.
- operation: value (the latest figures), series (over time), change (from one period to another), share (the first
  pick, or the first named group, as a share of the second pick or of the total), per_inhabitant (divided by the
  population; per 1 or 1000), rank (the largest first; top N), sum (groups added together).
- shape: number, line, bars, ranking, stacked (parts of a whole), map (one value per municipality or region),
  heatmap (month against year), table, or auto. shape_from_question is true only when the wording settles it: a
  trend, a map, a comparison, the largest, a share.
- none_fit: true when no listed table has the figure asked. Then picks stays empty.
- Prefer original figures to seasonally adjusted ones, and the table whose latest period is the newest.
- Places: towns and municipalities need a table of municipalities, not of postcodes or districts."""


# ---------- the tables, laid out for the model ----------

_PLACE_DIM = re.compile(r"municipal|sveitarf|region|landshlut|nuts|constituenc|kjordaem|town|localit|byggdakjarn", re.I)
_MAP_NUMBERS: set[str] | None = None


def map_numbers() -> set[str]:
    """The official numbers of the municipalities the map draws (frontend/municipalities.geo.json)."""
    global _MAP_NUMBERS
    if _MAP_NUMBERS is None:
        path = ROOT / "frontend/municipalities.geo.json"
        _MAP_NUMBERS = {f["id"] for f in json.loads(path.read_text())["features"]} if path.exists() else set()
    return _MAP_NUMBERS


def map_codes(var: Variable) -> dict[str, str]:
    """option code -> municipality number, for a dimension of municipalities the map can draw; empty otherwise.
    Matched by number, then by name, so a municipality the map has under another number (after a merger) still
    finds its shape."""
    if var.time or not _PLACE_DIM.search(normalize(var.text)):
        return {}
    numbers = map_numbers()
    names = {}
    path = ROOT / "frontend/municipalities.geo.json"
    if path.exists():
        names = {normalize(f["properties"]["name"]): f["id"] for f in json.loads(path.read_text())["features"]}
    out = {}
    for code, label in zip(var.values, var.value_texts):
        number = names.get(normalize(label)) or (code if code in numbers else None)
        if number:
            out[code] = number
    return out if len(out) >= 5 else {}


class Family:
    """A candidate table as the model sees it: measures to choose among, groups with their totals, its periods."""

    def __init__(self, key: str, entry: CatalogEntry, meta: TableMeta):
        self.key, self.entry, self.meta = key, entry, meta
        self.time = next((v for v in meta.variables if v.time), None)
        self.measures = [v for v in meta.variables if not v.time and _total_of(v) is None and len(v.values) > 1]
        self.groups = [v for v in meta.variables if not v.time and _total_of(v) is not None]
        self.fixed = [v for v in meta.variables if not v.time and len(v.values) == 1]

    def card(self, question: str) -> str:
        e = self.entry
        freq = "monthly" if "M" in e.last_period else "quarterly" if "Q" in e.last_period else "yearly"
        lines = [f"{self.key} · {self.meta.id} · {e.title_en}" + (f" ({e.title_is})" if e.title_is else "")
                 + (f" · {freq} {self.time.values[0]}–{self.time.values[-1]}" if self.time else "")
                 + (f" · unit {self.meta.unit}" if self.meta.unit else "")]
        for v in self.measures:
            opts = [t for _, t in _options_for_prompt(v, question)] if len(v.values) > MAX_MEASURE_OPTIONS else v.value_texts
            lines.append(f"   measure {v.text}: " + " | ".join(o.strip() for o in opts[:MAX_MEASURE_OPTIONS]))
        for v in self.groups:
            total = v.label_of(_total_of(v))
            named = [t.strip() for c, t in _options_for_prompt(v, question) if c != _total_of(v)][:12]
            extra = " [map]" if map_codes(v) else ""
            lines.append(f"   group {v.text}{extra} ({len(v.values)} options, default {total.strip()})"
                         + (": " + " | ".join(named) if named else ""))
        return "\n".join(lines)


async def candidates(catalog: Catalog, source, question: str) -> list[Family]:
    """The dictionary's tables for the subjects named, then the search's, without repeats."""
    found = match_concepts(question)
    dict_top, _ = dictionary_tables(found, catalog)
    ids = list(dict.fromkeys([e.id for e in dict_top[:3]] + [e.id for e, _ in catalog.search(question, MAX_TABLES)]))
    out = []
    for table_id in ids:
        entry = catalog.get(table_id)
        try:
            meta = await source.meta(table_id)
        except Exception:
            continue
        out.append(Family(f"T{len(out) + 1}", entry, meta))
        if len(out) == MAX_TABLES:
            break
    return out


async def read(llm: LLM, families: list[Family], question: str, display: str | None = None) -> Reading:
    listing = "\n".join(f.card(question) for f in families)
    hint = f"\nThe user asked to see it as: {display}." if display else ""
    return await llm.complete_json(READER, f"Question: {question}{hint}\n\nTables:\n{listing}", Reading)


# ---------- the model's choices checked against the tables ----------

def option(var: Variable, said: str) -> str | None:
    """The option the model named: exactly, or by its words ("Kópavogur" finds "Kopavogsbaer")."""
    said = said.strip()
    if not said:
        return None
    if code := _resolve_value(var, said):
        return code
    words = [w for w in tokens(said) if len(w) >= 3]
    best, score = None, 0
    for code, label in zip(var.values, var.value_texts):
        lt = tokens(label)
        hit = sum(1 for w in words if any(_same_word(t, w) for t in lt))
        if hit and hit == len(words) and (score == 0 or len(lt) < score):   # every word found; the shortest label wins
            best, score = code, len(lt)
    return best


def dim(meta: TableMeta, name: str) -> Variable | None:
    n = normalize(name)
    return next((v for v in meta.variables if normalize(v.text) == n or normalize(v.code) == n), None)


def periods(time: Variable | None, start: str, end: str, operation: str) -> list[str]:
    """The table's periods from start to end, in its own codes. "all": every period; nothing: the latest only."""
    if time is None:
        return []
    values = time.values
    start, end = start.strip(), end.strip()
    if start.lower() == "all":
        return list(values)
    if not start and not end:
        return [values[-1]]
    if start and not end and operation not in ("series", "change"):
        end = start   # "in 2025": that period (every month of it in a monthly table), not 2025 onwards
    lo = next((v for v in values if v >= start), None) if start else values[0]
    hi = next((v for v in reversed(values) if v[: len(end)] <= end), None) if end else values[-1]
    if lo is None:
        return [values[-1]]
    hi = hi or values[-1]
    picked = [v for v in values if lo <= v <= hi]
    if operation == "change" and len(picked) > 2:
        return [picked[0], picked[-1]]
    return picked or [values[-1]]
