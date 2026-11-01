"""Grow the development question sets without touching the holdout. Three sources, each graded on the table that
must answer the question, which is known by construction, so no hand-written answer key is needed.

    python -m scripts.build_gen_valset tables --per-prefix 6     # questions written from the catalogue itself
    python -m scripts.build_gen_valset paraphrase                 # the organisers' 60, each reworded three ways
    python -m scripts.build_gen_valset siblings --pairs 60        # near-identical table pairs, one question per side
    python -m scripts.build_gen_valset split                      # halves: gen_dev_*.jsonl to fix against, gen_frozen_*.jsonl to measure

tables:     a stratified sample of the catalogue (so many tables per id prefix: MAN, THJ, SKO, ...). For each table
            the model writes three questions in both languages: one plain lookup of the latest period, one change
            between two periods, one about a category the table has. expected_tables = that table.
paraphrase: each organiser question rewritten shorter, colloquially and with a synonym or a typo. Same expected
            tables and values, so this measures robustness to wording rather than memory of the organisers' template.
siblings:   pairs of tables in the same folder whose titles nearly coincide (base year, monthly vs annual, by sex vs
            total, old vs new series). One question that only A can answer, one that only B can. This is the set
            that targets the sibling confusion behind most of the misses in Icelandic (LAU04001, THJ01103, MAN10001).

Every question carries its source ("tables", "paraphrase", "siblings"), kind, language and the area (id prefix),
so retrieval_eval --by area language kind shows where the search is weak. The holdout files are never read.
"""

import argparse
import asyncio
import json
import random
import re
from collections import defaultdict
from itertools import combinations
from pathlib import Path

from pydantic import BaseModel, Field

from hagbot.catalog import Catalog
from hagbot.config import settings
from hagbot.llm import LLM, LLMError
from hagbot.text import tokens

OUT = Path("eval")
ORGANIZERS = OUT / "organizers.jsonl"
CONCURRENCY = 3


def _llm() -> LLM:
    # short answers, thinking off: the writing is easy, the volume is not
    return LLM().with_options(reasoning_effort="none", temperature=0.7)


def _area(table_id: str) -> str:
    return re.match(r"[A-Za-z]+", table_id).group(0).upper()


def _write(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    print(f"{len(rows)} questions -> {path}")


def _read(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


async def _gather(coros, label: str):
    """Run coroutines a few at a time, dropping the ones the model fails."""
    sem = asyncio.Semaphore(CONCURRENCY)
    done = 0

    async def one(c):
        nonlocal done
        async with sem:
            try:
                r = await c
            except LLMError as e:
                print(f"  skipped: {str(e)[:80]}")
                r = None
            done += 1
            if done % 10 == 0:
                print(f"  {label}: {done}", flush=True)
            return r

    return [r for r in await asyncio.gather(*(one(c) for c in coros)) if r is not None]


# ---------------------------------------------------------------- tables

class Pair(BaseModel):
    en: str = Field(description="the question in English")
    is_: str = Field(alias="is", description="the same question in Icelandic")


class TableQuestions(BaseModel):
    lookup: Pair = Field(description="one figure from the latest period")
    change: Pair = Field(description="the change between two periods, in units or percent")
    category: Pair = Field(description="about one of the table's options, or which option is largest")


TABLES_SYSTEM = """You write test questions for a statistics chatbot. You get one table from Statistics Iceland. Write
three questions a journalist, a pupil or an official might ask that THIS table answers, each in English and in
natural Icelandic (not a word-for-word translation). Name the period the way a person would ("in 2024",
"in September 2026", "in the second quarter of 2025"), use plain words for the subject (never the table's id or
its exact title), and for the category question ask for the figure of one option named in the list, or which
option is largest, in a period. Every question asks for a number; never ask about the table itself (what it
shows, how often it is published). Keep each under 25 words."""


def _describe(e) -> str:
    freq = "monthly" if "M" in e.last_period else "quarterly" if "Q" in e.last_period else "yearly"
    opts = ", ".join(e.values[:14]) + (" ..." if len(e.values) > 14 else "")
    return (f"Table {e.id}\nTitle (en): {e.title_en}\nTitle (is): {e.title_is}\nDimensions: {', '.join(e.dimensions)}\n"
            f"Periods: {freq}, {e.first_period} to {e.last_period}\nUnit: {e.unit or 'count'}\nOptions: {opts}")


async def _table_questions(llm: LLM, e) -> list[dict]:
    r = await llm.complete_json(TABLES_SYSTEM, _describe(e), TableQuestions)
    level = {"lookup": "easy", "change": "medium", "category": "medium"}
    out = []
    for kind in ("lookup", "change", "category"):
        pair = getattr(r, kind)
        for lang, q in (("en", pair.en), ("is", pair.is_)):
            out.append({"id": f"gen-{e.id}-{kind}-{lang}", "level": level[kind], "language": lang, "question": q,
                        "expected_tables": [e.id], "expected_values": [], "source": "tables", "kind": kind,
                        "area": _area(e.id)})
    return out


def sample_tables(catalog: Catalog, per_prefix: int, seed: int) -> list:
    """So many tables per id prefix, the freshest half of each prefix preferred: stale one-off tables answer few questions."""
    rng = random.Random(seed)
    by = defaultdict(list)
    for e in catalog.entries:
        if e.last_period and e.title_en:
            by[_area(e.id)].append(e)
    picked = []
    for area, es in sorted(by.items()):
        es = sorted(es, key=lambda e: e.last_period, reverse=True)
        pool = es[: max(per_prefix, len(es) // 2)]
        picked += rng.sample(pool, min(per_prefix, len(pool)))
    return picked


async def cmd_tables(args) -> None:
    catalog = Catalog.load(Path(args.catalog))
    es = sample_tables(catalog, args.per_prefix, args.seed)
    print(f"{len(es)} tables from {len({_area(e.id) for e in es})} prefixes")
    llm = _llm()
    try:
        rows = await _gather([_table_questions(llm, e) for e in es], "tables")
    finally:
        await llm.aclose()
    _write(OUT / "gen_tables.jsonl", [q for qs in rows for q in qs])


# ---------------------------------------------------------------- paraphrase

class Paraphrases(BaseModel):
    shorter: str = Field(description="the same question in as few words as possible")
    colloquial: str = Field(description="as someone would type it in a chat, informal, maybe without a question mark")
    varied: str = Field(description="with a synonym for the main term and one small typo, meaning unchanged")


PARA_SYSTEM = """Rewrite the question three ways in the SAME language it is written in. The meaning, the place, the
period and the measure must not change at all: a reader must get exactly the same number. Do not add or drop a
period or a place."""


async def _paraphrase(llm: LLM, q: dict) -> list[dict]:
    r = await llm.complete_json(PARA_SYSTEM, q["question"], Paraphrases)
    return [{**q, "id": f"{q['id']}-{style}", "question": getattr(r, style), "source": "paraphrase", "kind": style,
             "area": _area(q["expected_tables"][0].split("|")[0]), "original": q["question"]}
            for style in ("shorter", "colloquial", "varied")]


async def cmd_paraphrase(args) -> None:
    qs = _read(ORGANIZERS)
    llm = _llm()
    try:
        rows = await _gather([_paraphrase(llm, q) for q in qs], "paraphrase")
    finally:
        await llm.aclose()
    _write(OUT / "gen_paraphrase.jsonl", [q for qs in rows for q in qs])


# ---------------------------------------------------------------- siblings

def _title_key(title: str) -> set[str]:
    return {t for t in tokens(title) if not t.isdigit()}


def sibling_pairs(catalog: Catalog, limit: int, seed: int) -> list[tuple]:
    """Pairs in one folder whose titles share most words but are not the same table: the ones search mixes up."""
    by_folder = defaultdict(list)
    for e in catalog.entries:
        if e.title_en and e.last_period:
            by_folder[e.path.rsplit("/", 1)[0]].append(e)
    pairs = []
    for es in by_folder.values():
        for a, b in combinations(es, 2):
            ka, kb = _title_key(a.title_en), _title_key(b.title_en)
            if not ka or not kb or a.title_en == b.title_en:
                continue
            j = len(ka & kb) / len(ka | kb)
            if j >= 0.5:
                pairs.append((j, a, b))
    rng = random.Random(seed)
    rng.shuffle(pairs)
    # spread over areas: round-robin through the prefixes
    by_area = defaultdict(list)
    for p in pairs:
        by_area[_area(p[1].id)].append(p)
    out = []
    while len(out) < limit and any(by_area.values()):
        for area in sorted(by_area):
            if by_area[area] and len(out) < limit:
                out.append(by_area[area].pop())
    return out


class SiblingQuestions(BaseModel):
    only_a: Pair = Field(description="a question only table A answers, not B")
    only_b: Pair = Field(description="a question only table B answers, not A")
    difference: str = Field(description="in a few words, what separates A from B")


SIB_SYSTEM = """Two tables from Statistics Iceland look alike. Find what separates them (the base year, monthly vs
yearly, by sex or age vs totals, a region, an older vs a newer series, a different measure) and write one question
that ONLY table A can answer and one that ONLY table B can answer, each in English and natural Icelandic. The
question must make the difference explicit in plain words (name the base year, the month, the breakdown...) without
quoting the table id or its exact title. Under 25 words each."""


async def _sibling_questions(llm: LLM, j: float, a, b) -> list[dict]:
    r = await llm.complete_json(SIB_SYSTEM, "TABLE A\n" + _describe(a) + "\n\nTABLE B\n" + _describe(b), SiblingQuestions)
    out = []
    for side, e, pair in (("a", a, r.only_a), ("b", b, r.only_b)):
        for lang, q in (("en", pair.en), ("is", pair.is_)):
            out.append({"id": f"sib-{a.id}-{b.id}-{side}-{lang}", "level": "medium", "language": lang, "question": q,
                        "expected_tables": [e.id], "expected_values": [], "source": "siblings", "kind": f"only_{side}",
                        "area": _area(e.id), "pair": f"{a.id}|{b.id}", "difference": r.difference})
    return out


async def cmd_siblings(args) -> None:
    catalog = Catalog.load(Path(args.catalog))
    pairs = sibling_pairs(catalog, args.pairs, args.seed)
    print(f"{len(pairs)} pairs, e.g. " + "; ".join(f"{a.id}/{b.id}" for _, a, b in pairs[:5]))
    llm = _llm()
    try:
        rows = await _gather([_sibling_questions(llm, *p) for p in pairs], "siblings")
    finally:
        await llm.aclose()
    _write(OUT / "gen_siblings.jsonl", [q for qs in rows for q in qs])


# ---------------------------------------------------------------- split

def cmd_split(args) -> None:
    """Half to fix against, half to measure on. Questions about one table (or one organiser question, or one pair)
    stay in the same half, so the frozen half never holds a table the dev half taught us."""
    rng = random.Random(args.seed)
    for name in ("tables", "paraphrase", "siblings"):
        path = OUT / f"gen_{name}.jsonl"
        if not path.exists():
            continue
        groups = defaultdict(list)
        for q in _read(path):
            groups[q.get("pair") or q.get("original") or q["expected_tables"][0]].append(q)
        keys = sorted(groups)
        rng.shuffle(keys)
        half = len(keys) // 2
        _write(OUT / f"gen_dev_{name}.jsonl", [q for k in keys[:half] for q in groups[k]])
        _write(OUT / f"gen_frozen_{name}.jsonl", [q for k in keys[half:] for q in groups[k]])


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("tables", "paraphrase", "siblings", "split"):
        p = sub.add_parser(name)
        p.add_argument("--catalog", default=str(settings.catalog_path))
        p.add_argument("--seed", type=int, default=2026)
        if name == "tables":
            p.add_argument("--per-prefix", type=int, default=6)
        if name == "siblings":
            p.add_argument("--pairs", type=int, default=60)
    args = ap.parse_args()
    fn = {"tables": cmd_tables, "paraphrase": cmd_paraphrase, "siblings": cmd_siblings, "split": cmd_split}[args.cmd]
    r = fn(args)
    if asyncio.iscoroutine(r):
        asyncio.run(r)


if __name__ == "__main__":
    main()
