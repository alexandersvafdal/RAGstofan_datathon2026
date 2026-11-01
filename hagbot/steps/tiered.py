"""Tiered table finder: cheap and explainable first, the model's own judgement last.

  1. A ranked hitlist. Three signals each rank the tables: the keyword dictionary (data/keywords.json maps everyday
     words, English or Icelandic, to Statistics Iceland's term and its usual tables: "verðbólga" -> consumer price
     index -> VIS01000), and the catalog's own searches, each ranking the tables separately: keyword search (BM25)
     and whichever vector searches are switched on (e5 from hagbot/e5.py, bge-m3 from hagbot/vectors.py), all run on
     the question plus the official term. Dictionary tables lead; the rest are ordered by reciprocal-rank fusion of
     the searches times a recency factor; code-side rules (narrow) then drop variants nobody asked for.
  2. The model checks the hitlist from the top, 8 tables at a time: it picks one, or says none of them answers.
  3. If none fits: the model browses, first one of Statistics Iceland's subject areas, then a table inside it.
  4. If every check said "none" (the model can be too strict), it must pick from the keyword hits.

Every tier shares the code-side rules the classic finder uses: ambiguous words ask the user, table variants nobody
asked for are dropped (narrow), and the newest edition of a chosen table is used (freshest sibling).
Set RETRIEVAL=tiered to use it; the classic finder stays the default until this one is measured.
"""

import json
import re
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field

from ..catalog import STOPWORDS, Catalog, CatalogEntry, recency_factor
from ..config import ROOT
from ..llm import LLM
from ..vote import vote_json, voting_on
from ..text import normalize, tokens
from .retrieve import AMBIGUOUS, MONTHS, Retrieved, SYSTEM as PICK_RULES, freshest_sibling, narrow, retrieve

KEYWORDS = ROOT / "data" / "keywords.json"
BATCH = 8   # tables the model sees per check
BATCHES = 2  # the hitlist is checked in at most two batches before falling back
RRF_K = 60  # reciprocal-rank fusion: a list's #1 adds 1/61, its #10 adds 1/70
DICT_BONUS = 1.0  # dictionary tables are hand-picked: they always lead, the searches order the rest


@lru_cache(maxsize=1)
def concepts() -> list[dict]:
    if not KEYWORDS.exists():
        return []
    out = []
    for c in json.loads(KEYWORDS.read_text())["concepts"]:
        out.append({**c, "_terms": [" ".join(tokens(t)) for t in c["terms"] if tokens(t)]})
    return out


def match_concepts(text: str) -> list[dict]:
    """Concepts whose terms occur in the text: one-word terms match a word that starts with them (exactly, for
    terms of three letters or fewer), several-word terms match as a phrase from a word start. Icelandic puts the
    head noun last in a compound ("örorkulífeyrir", "mjólkurkýr", "launakostnaður"), so a term of six letters or
    more also matches inside a longer word."""
    words = tokens(text)
    padded = " " + " ".join(words) + " "
    hits = []
    for c in concepts():
        for t in c["_terms"]:
            if " " in t:
                ok = f" {t}" in padded
            else:
                ok = any(w == t if len(t) <= 3 else w.startswith(t) or (len(t) >= 6 and len(w) >= len(t) + 3 and t in w)
                         for w in words)
            if ok:
                hits.append(c)
                break
    # "presidential election" also matches "election", "building cost index" also matches "prices": the wider
    # concept steps aside for the narrower one it is listed as broader of, so its tables do not crowd the hitlist
    narrower = {b for c in hits for b in c.get("broader", [])}
    return [c for c in hits if c["id"] not in narrower]


# Words that do not narrow a question about an index: when, how it moved, where. "What is the current wage index",
# "how has the CPI changed in the last 12 months" are about the headline index; "public sector", "housing" are not.
_GENERIC = ("current", "latest", "newest", "recent", "now", "today", "value", "level", "figure", "number", "chang",
            "rise", "rose", "risen", "increas", "grow", "grew", "develop", "trend", "since", "over", "year", "month",
            "annual", "last", "past", "iceland", "nuna", "nuverand", "nyjust", "nyjast", "sidast", "sidust", "gildi",
            "stad", "breyt", "haekk", "throun", "thro", "arum", "arin", "manud", "undanfar", "island",
            # "give me the wage index as it is today and also show me how it has moved": asking words, not a breakdown
            "give", "show", "tell", "also", "how", "has", "have", "been", "plot", "chart", "graph", "line", "please",
            "gef", "synd", "syn", "seg", "lika", "hvernig", "hefur", "hafa", "eins", "dag", "sidan", "linurit",
            # pronouns too short for the stopword list: "gefðu mér … hvernig hún hefur þróast"
            "mer", "hun", "hann", "thad", "thess", "mig", "okkur", "its", "it", "me", "us", "this", "that")


def beyond_concept(concept: dict, text: str) -> list[str]:
    """The question's words that are neither the concept's own, generic, a month nor a number: a breakdown or
    another subject. Empty: the question asks for the concept itself and nothing more."""
    own = {t for term in concept["_terms"] for t in term.split() if len(t) >= 3}
    return [w for w in tokens(text) if w not in STOPWORDS and not w.isdigit() and len(w) >= 3
            and not w.startswith(_GENERIC) and not re.fullmatch(MONTHS, w)
            and not any(w.startswith(t) or t.startswith(w) for t in own)]


def headline(hit: CatalogEntry, found: list[dict], catalog: Catalog, text: str) -> CatalogEntry:
    """A concept's first dictionary table (its headline: the CPI, the wage index …) over a neighbour in the same folder
    that the model chose (by sector, for indexation), when the question asks for nothing more than the concept itself.
    Applies to every concept in the dictionary; a question naming a breakdown ("public sector", "á almennum markaði",
    "subcategories") keeps the model's choice."""
    for c in found:
        head = catalog.get(c["tables"][0]) if c["tables"] else None
        if not head or head.id == hit.id or head.path != hit.path:
            continue
        if not beyond_concept(c, text):
            return head
    return hit


def rank(preferred: list[CatalogEntry], searches: dict[str, list[CatalogEntry]]) \
        -> list[tuple[CatalogEntry, float, list[str]]]:
    """Every table any signal found, best first: (table, score, why) with why like ["dictionary", "bm25 #2",
    "e5 #5"]. Score = dictionary bonus + 1/(RRF_K + rank) for each search that found it, times recency."""
    tables: dict[str, CatalogEntry] = {}
    score: dict[str, float] = {}
    why: dict[str, list[str]] = {}
    for e in preferred:
        tables[e.id] = e
        score[e.id] = score.get(e.id, 0.0) + DICT_BONUS
        why.setdefault(e.id, []).append("dictionary")
    for name, hits in searches.items():
        for r, e in enumerate(hits, 1):
            tables[e.id] = e
            score[e.id] = score.get(e.id, 0.0) + 1 / (RRF_K + r)
            why.setdefault(e.id, []).append(f"{name} #{r}")
    out = [(tables[i], score[i] * recency_factor(tables[i].last_period, tables[i].title_en), why[i]) for i in tables]
    return sorted(out, key=lambda x: -x[1])


class Verdict(BaseModel):
    table_id: str = Field(description='ID of the table that answers the question, copied exactly, or "none"')
    # kept: without it the 35B model's one-field answers failed validation on two of the organisers' questions
    reason: str = Field(default="", description="a few words on why, or why none fits")


CHECK = PICK_RULES.replace("You choose which Statistics Iceland table answers a question.",
                           "You check whether one of these Statistics Iceland tables can answer the question.") + """
The tables are numbered in search order; the number is not a vote, the rules above decide.
A table marked "headline table for X" is the standard table for that subject: choose it for a plain question about X,
and a neighbour only when the question asks for a breakdown or a measure it lacks (constant prices, an index, quarters).
If none of the listed tables can answer it (wrong topic, wrong place or period, wrong measure), answer "none"."""


async def _check(llm: LLM, entries: list[CatalogEntry], question: str, original: str, start: int = 0,
                 heads: dict[str, str] | None = None) -> CatalogEntry | None:
    """The model picks from these tables, or rejects them all. They come in hitlist order, numbered; a concept's
    headline table says so on its card."""
    if not entries:
        return None
    by_id = {e.id: e for e in entries}
    heads = heads or {}
    cards = "\n".join(f"#{start + n}. {e.card(f'{question} {original}')}"
                      + (f" — headline table for {heads[e.id]}" if e.id in heads else "")
                      for n, e in enumerate(entries, 1))
    asked = f"User asked: {original}\nThis part: {question}" if original and original != question else f"Question: {question}"
    user = f"{asked}\n\nTables:\n{cards}"
    if voting_on():  # several votes; "none" is a vote too
        v, agreement = await vote_json(llm, CHECK, user, Verdict, key=lambda x: x.table_id.strip().upper())
        _last_votes[0] = agreement
    else:
        v = await llm.complete_json(CHECK, user, Verdict)
    return by_id.get(v.table_id.strip())


_last_votes = [""]  # agreement of the most recent voted check, reported with the table


class Subjects(BaseModel):
    subjects: list[str] = Field(description="up to two subject IDs from the list, best first")


async def _browse(llm: LLM, catalog: Catalog, question: str, original: str) -> tuple[CatalogEntry | None, list[str]]:
    """Last resort: the model chooses a subject area, then a table in it."""
    areas: dict[str, list[CatalogEntry]] = {}
    for e in catalog.entries:
        areas.setdefault("/".join(e.path.split("/")[:2]), []).append(e)
    listing = "\n".join(f"- {a}: e.g. " + "; ".join(x.title_en for x in es[:3]) for a, es in areas.items())
    asked = original or question
    pick = await llm.complete_json("You find where in Statistics Iceland's database a question's answer lives.",
                                   f"Question: {asked}\n\nSubject areas:\n{listing}", Subjects)
    pool = [e for s in pick.subjects[:2] for e in areas.get(s.strip(), [])]
    pool = narrow(pool, f"{question} {original}")
    for i in range(0, min(len(pool), 4 * BATCH), BATCH):  # at most four batches
        if (hit := await _check(llm, pool[i:i + BATCH], question, original)):
            return hit, [e.id for e in pool[:4 * BATCH]]
    return None, [e.id for e in pool[:4 * BATCH]]


async def retrieve_tiered(llm: LLM, catalog: Catalog, question: str, search_query: str,
                          k: int, forced_table: str | None = None, original: str = "") -> Retrieved:
    if forced_table and (entry := catalog.get(forced_table)):
        return Retrieved(table=entry, candidates=[entry.id])
    import re
    _last_votes[0] = ""
    q_user = normalize(original or question)
    for pattern, settled, tables in AMBIGUOUS:
        entries = [e for e in (catalog.get(t) for t in tables) if e]
        if re.search(pattern, q_user) and not re.search(settled, q_user) and len(entries) > 1:
            return Retrieved(table=entries[0], alternatives=entries[1:], ambiguous=True, candidates=[e.id for e in entries])

    text = f"{search_query} {question} {original}"
    found = match_concepts(text)

    # 1. the ranked hitlist: dictionary + each of the catalog's searches (BM25, e5, bge-m3 when on), times recency.
    # A question touching several subjects ("labour costs in construction, fishing and finance") would fill the first
    # batch with every subject's tables; then each subject sends only its headline, and the searches fill the rest.
    with_tables = [c for c in found if c["tables"]]
    per_concept = 1 if len(with_tables) >= 3 else None
    preferred = [e for c in with_tables for t in c["tables"][:per_concept] if (e := catalog.get(t))]
    query = " ".join([text, *(c["search"] for c in found)])
    searches = {name: [e for e in (catalog.get(i) for i in ids) if e] for name, ids in catalog.rankings(query, 4 * k).items()}
    ranked = rank(preferred, searches)
    shortlist = narrow([e for e, _, _ in ranked], f"{question} {original}")
    order = {e.id: (score, why) for e, score, why in ranked}
    hitlist = [{"id": e.id, "title": e.title_en, "score": round(order[e.id][0], 4), "signals": order[e.id][1]}
               for e in shortlist[:BATCHES * BATCH]]

    def done(table: CatalogEntry, pool: list[CatalogEntry], tier: str) -> Retrieved:
        table = freshest_sibling(table, pool, f"{question} {original}")
        return Retrieved(table=table, candidates=[e.id for e in pool], via=tier,
                         concepts=[c["concept"] for c in found], ranking=hitlist, votes=_last_votes[0] if voting_on() else "")

    # 1b. one subject in the question, its headline table on top, and nothing asked beyond the subject itself
    # ("what is the wage index", "hver var verðbólgan í ágúst"): the dictionary decides, no model call. The headline
    # snap after a model check would give the same table anyway.
    topics = [c for c in found if c.get("role", "topic") == "topic"]
    if (len(with_tables) == 1 and topics == with_tables and shortlist and shortlist[0].id == with_tables[0]["tables"][0]
            and not beyond_concept(with_tables[0], question)):
        return done(shortlist[0], shortlist, "dictionary")

    # 2. the model checks the hitlist from the top
    heads = {c["tables"][0]: c["concept"] for c in with_tables if catalog.get(c["tables"][0])}
    for i in range(0, min(len(shortlist), BATCHES * BATCH), BATCH):
        if (hit := await _check(llm, shortlist[i:i + BATCH], question, original, start=i, heads=heads)):
            # the snap reads this part alone: in "GDP in 2025 and how many children were born", the births words
            # are not a breakdown of GDP; and the planner's English part has no Icelandic inflections ("árið")
            hit = headline(hit, found, catalog, question)
            signals = order.get(hit.id, (0, []))[1]
            tier = ("keywords" if any(x == "dictionary" for x in signals) else
                    "keyword search" if any(x.startswith("bm25") for x in signals) else "semantic search")
            return done(hit, shortlist, tier)

    # 3. the model browses the subject areas
    hit, browsed = await _browse(llm, catalog, question, original)
    if hit:
        pool = [e for e in (catalog.get(i) for i in browsed) if e]
        return done(hit, pool, "model browse")

    # 4. safety net: every check said "none" (the model can be too strict), so it must pick from the keyword hits
    if shortlist:
        r = await retrieve(llm, catalog, question, search_query, k, original=original)
        return done(r.table, shortlist, "best keyword hit")
    raise LookupError(f"No table found for “{question}”")
