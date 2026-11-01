"""Table finder: one candidate at a time, each confirmed by the model before it is used.

  1. Dictionary. data/keywords.json maps everyday words (English or Icelandic) to Statistics Iceland's subjects and
     their tables ("verðbólga" -> consumer prices -> VIS01000). Of the subjects the question matches, the one whose
     matching word is longest (the most specific) gives its first table. The model answers yes or no: does this
     table hold the figures the question asks for?
  2. Search. Keyword search (BM25) and e5 vectors run in parallel on the question; their rankings are fused, the
     code-side rules (narrow) drop variants nobody asked for, and the model answers yes or no for the top three,
     one at a time.
  3. Every table. The model picks from a list of all 1,857 tables. How much of each table the list shows depends on
     the context the model gets (hagbot/models.py): titles, years and dimensions when they fit; less when not; and
     when even titles alone do not fit, two passes: subject areas first, then every table in the chosen areas.

The chosen table is swapped for a newer edition of itself when one exists (freshest sibling).
"""

import json
from functools import lru_cache

from pydantic import BaseModel, Field

from ..catalog import Catalog, CatalogEntry
from ..config import ROOT
from ..llm import LLM
from ..models import usable_context
from ..text import tokens
from ..vote import vote_json, voting_on
from .retrieve import Retrieved, SYSTEM as PICK_RULES, freshest_sibling, narrow

KEYWORDS = ROOT / "data" / "keywords.json"
SEARCH_CHECKS = 3      # search hits the model checks one at a time
RESERVE = 3000         # tokens kept free in step 3 for the rules, the question and the answer
CHARS_PER_TOKEN = 3.0  # a cautious estimate: Icelandic titles take more tokens than English ones


@lru_cache(maxsize=1)
def concepts() -> list[dict]:
    if not KEYWORDS.exists():
        return []
    out = []
    for c in json.loads(KEYWORDS.read_text())["concepts"]:
        out.append({**c, "_terms": [" ".join(tokens(t)) for t in c["terms"] if tokens(t)]})
    return out


def _matches(term: str, words: list[str], padded: str) -> bool:
    """One-word terms match a word that starts with them (exactly, for terms of three letters or fewer); several-word
    terms match as a phrase from a word start. Icelandic puts the head noun last in a compound ("örorkulífeyrir",
    "launakostnaður"), so a term of six letters or more also matches inside a longer word."""
    if " " in term:
        return f" {term}" in padded
    return any(w == term if len(term) <= 3 else w.startswith(term) or (len(term) >= 6 and len(w) >= len(term) + 3 and term in w)
               for w in words)


def match_concepts(text: str) -> list[dict]:
    """Subjects whose terms occur in the text, each with "_matched": the longest of its terms that matched. A wider
    subject steps aside for a narrower one it is listed as broader of ("election" for "presidential election")."""
    words = tokens(text)
    padded = " " + " ".join(words) + " "
    hits = []
    for c in concepts():
        matched = [t for t in c["_terms"] if _matches(t, words, padded)]
        if matched:
            hits.append({**c, "_matched": max(matched, key=len)})
    narrower = {b for c in hits for b in c.get("broader", [])}
    return [c for c in hits if c["id"] not in narrower]


def dictionary_table(found: list[dict], catalog: Catalog) -> tuple[CatalogEntry | None, dict | None]:
    """Step 1: the first table of the most specific subject matched, the one whose matching word is longest."""
    with_tables = [c for c in found if any(catalog.get(t) for t in c["tables"])]
    if not with_tables:
        return None, None
    best = max(with_tables, key=lambda c: len(c["_matched"]))
    return next(e for t in best["tables"] if (e := catalog.get(t))), best


# ---------- step 1 and 2: does this one table answer? ----------

class Fits(BaseModel):
    answers: bool = Field(description="true if this table holds the figures the question asks for")
    reason: str = Field(default="", description="a few words on why or why not")


FITS = """You check whether one Statistics Iceland table holds the figures needed to answer a question (or the part
of a question given).
Answer true when the table is about the subject asked and has the measure asked for, with the place, category or
breakdown the question names among its dimensions or options.
The table's years are in brackets. When it is on the subject but its years miss the period asked, still answer true:
the user is then told which years exist.
Answer false when it is about another subject or another measure, or lacks a place or breakdown the question names.
- "How many people live in X" means population on 1 January.
- Prefer original figures: a seasonally adjusted, trend or forecast table answers only a question asking for one."""


def describe(e: CatalogEntry, question: str, options: int = 30) -> str:
    """One table for a yes/no check: its titles, years, dimensions and a sample of its options."""
    text = e.card(question)
    if e.title_is and e.title_is not in text:
        text = text.replace(e.title_en, f"{e.title_en} ({e.title_is})", 1)
    if e.values:
        sample = list(dict.fromkeys([*e.matching_values(question, limit=10), *e.values[:options]]))
        text += f". Options include: {', '.join(sample)}" + (" …" if len(e.values) > len(sample) else "")
    return text


async def fits(llm: LLM, e: CatalogEntry, question: str, original: str) -> bool:
    asked = f"User asked: {original}\nThis part: {question}" if original and original != question else f"Question: {question}"
    v = await llm.complete_json(FITS, f"{asked}\n\nTable:\n{describe(e, f'{question} {original}')}", Fits)
    return v.answers


# ---------- step 3: the model picks from every table ----------

class Pick(BaseModel):
    table_id: str = Field(description="ID of the table that best answers the question, copied exactly from the list")


class Subjects(BaseModel):
    subjects: list[str] = Field(description="up to two subject area IDs from the list, best first")


def _years(e: CatalogEntry) -> str:
    return f" [{e.first_period}–{e.last_period}]" if e.first_period else ""


# Ways to write one table in the list, from the most to the least said about it
FORMATS = {
    "titles, years, dimensions": lambda e: f"{e.id}: {e.title_en} | {e.title_is}{_years(e)} | {', '.join(e.dimensions)}",
    "titles, years": lambda e: f"{e.id}: {e.title_en} | {e.title_is}{_years(e)}",
    "English titles, years": lambda e: f"{e.id}: {e.title_en}{_years(e)}",
}


def listing(entries: list[CatalogEntry], budget: int) -> tuple[str, str] | None:
    """The fullest way of listing these tables that fits in `budget` tokens: (format name, text), or None."""
    for name, line in FORMATS.items():
        text = "\n".join(line(e) for e in entries)
        if len(text) / CHARS_PER_TOKEN <= budget:
            return name, text
    return None


async def _pick(llm: LLM, entries: list[CatalogEntry], text: str, kind: str, asked: str) -> CatalogEntry | None:
    # the list goes first and the question last, so the server can reuse the list's work from one question to the next
    system = f"{PICK_RULES}\n\nTables ({kind}):\n{text}"
    pick = await llm.complete_json(system, asked, Pick)
    return {e.id: e for e in entries}.get(pick.table_id.strip())


async def pick_from_all(llm: LLM, catalog: Catalog, question: str, original: str, breakdown_by: str = "") \
        -> tuple[CatalogEntry | None, str]:
    """Step 3. Returns the table (or None) and how it was listed, for the step log."""
    budget = usable_context(llm.model) - RESERVE
    asked = f"User asked: {original}\nThis part: {question}" if original and original != question else f"Question: {question}"
    if (whole := listing(catalog.entries, budget)):
        return await _pick(llm, catalog.entries, whole[1], whole[0], asked), f"all tables ({whole[0]})"

    # too many for this model's context: subject areas first, then every table in the chosen areas
    areas: dict[str, list[CatalogEntry]] = {}
    for e in catalog.entries:
        areas.setdefault("/".join(e.path.split("/")[:2]), []).append(e)
    menu = "\n".join(f"- {a} ({len(es)} tables): e.g. " + "; ".join(x.title_en for x in es[:3]) for a, es in areas.items())
    chosen = await llm.complete_json("You find where in Statistics Iceland's database a question's answer lives.",
                                     f"{asked}\n\nSubject areas:\n{menu}", Subjects)
    pool = [e for s in chosen.subjects[:2] for e in areas.get(s.strip(), [])]
    pool = narrow(pool, f"{question} {original}", breakdown_by)
    while pool and not (part := listing(pool, budget)):
        pool = pool[: int(len(pool) * 0.8)]   # still too long: keep the first tables of the areas
    if not pool:
        return None, "subject areas: none chosen"
    return await _pick(llm, pool, part[1], part[0], asked), f"subject areas {', '.join(chosen.subjects[:2])} ({part[0]})"


# ---------- the finder ----------

async def retrieve_tiered(llm: LLM, catalog: Catalog, question: str, search_query: str,
                          k: int, forced_table: str | None = None, original: str = "", breakdown_by: str = "") -> Retrieved:
    if forced_table and (entry := catalog.get(forced_table)):
        return Retrieved(table=entry, candidates=[entry.id])
    text = f"{search_query} {question} {original}"
    found = match_concepts(f"{question} {original}")   # the user's words and the planner's part, not its search words
    checked: list[dict] = []   # every table the model was asked about, for the answer's "how it was found" list

    def done(table: CatalogEntry, pool: list[CatalogEntry], tier: str) -> Retrieved:
        table = freshest_sibling(table, pool, f"{question} {original}")
        return Retrieved(table=table, candidates=list(dict.fromkeys(e.id for e in [table, *pool])), via=tier,
                         concepts=[c["concept"] for c in found], ranking=checked)

    # 1. the dictionary's table for the most specific subject named
    dict_table, subject = dictionary_table(found, catalog)
    subject_tables = [e for t in (subject["tables"] if subject else []) if (e := catalog.get(t))]
    if dict_table:
        ok = await fits(llm, dict_table, question, original)
        checked.append({"id": dict_table.id, "title": dict_table.title_en, "score": 1.0 if ok else 0.0,
                        "signals": [f"dictionary: {subject['concept']}", "yes" if ok else "no"]})
        if ok:
            return done(dict_table, subject_tables, "keywords")

    # 2. keyword search and e5, fused; the top three, one at a time
    hits = catalog.search(text, 2 * k)
    shortlist = narrow([e for e, _ in hits], f"{question} {original}", breakdown_by)[:k]
    score = {e.id: s for e, s in hits}
    for e in [e for e in shortlist if not dict_table or e.id != dict_table.id][:SEARCH_CHECKS]:
        ok = await fits(llm, e, question, original)
        checked.append({"id": e.id, "title": e.title_en, "score": round(score.get(e.id, 0.0), 4),
                        "signals": [f"search #{shortlist.index(e) + 1}", "yes" if ok else "no"]})
        if ok:
            return done(e, [*shortlist, *subject_tables], "search")

    # 3. the model picks from every table, listed to fit its context
    hit, how = await pick_from_all(llm, catalog, question, original, breakdown_by)
    if hit:
        checked.append({"id": hit.id, "title": hit.title_en, "score": 1.0, "signals": [how, "picked"]})
        return done(hit, [*shortlist, *subject_tables], "all tables")
    if shortlist:   # the model's pick was not on the list: the best search hit
        return done(shortlist[0], [*shortlist, *subject_tables], "best search hit")
    raise LookupError(f"No table found for “{question}”")


# ---------- a choice among a few tables (the pipeline's neighbour-table switch uses it) ----------

class Verdict(BaseModel):
    table_id: str = Field(description='ID of the table that answers the question, copied exactly, or "none"')
    # kept: without it the 35B model's one-field answers failed validation on two of the organisers' questions
    reason: str = Field(default="", description="a few words on why, or why none fits")


CHECK = PICK_RULES.replace("You choose which Statistics Iceland table answers a question.",
                           "You check whether one of these Statistics Iceland tables can answer the question.") + """
The tables are numbered; the number is not a vote, the rules above decide.
A table on the right subject whose years (in brackets) miss the period asked is still the answer when no listed table on
that subject covers that period: the user is then told which years exist. A table on another subject never answers,
whatever its years.
If none of the listed tables is on the subject asked (wrong topic, wrong place, wrong measure), answer "none"."""


async def _check(llm: LLM, entries: list[CatalogEntry], question: str, original: str, start: int = 0) -> CatalogEntry | None:
    """The model picks one of these tables, or says none answers."""
    if not entries:
        return None
    by_id = {e.id: e for e in entries}
    cards = "\n".join(f"#{start + n}. {e.card(f'{question} {original}')}" for n, e in enumerate(entries, 1))
    asked = f"User asked: {original}\nThis part: {question}" if original and original != question else f"Question: {question}"
    user = f"{asked}\n\nTables:\n{cards}"
    if voting_on():  # several votes; "none" is a vote too
        v, _ = await vote_json(llm, CHECK, user, Verdict, key=lambda x: x.table_id.strip().upper())
    else:
        v = await llm.complete_json(CHECK, user, Verdict)
    return by_id.get(v.table_id.strip())
