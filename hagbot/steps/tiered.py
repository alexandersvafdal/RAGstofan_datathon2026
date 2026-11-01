"""Table finder: three methods in turn, each suggesting one table that the model confirms or rejects.

  1. Dictionary. data/keywords.json maps everyday words (English or Icelandic) to Statistics Iceland's subjects and
     their tables ("verðbólga" -> consumer prices -> VIS01000). Of the subjects the question matches, the one whose
     matching word is longest (the most specific) gives its first table.
  2. Search. Keyword search (BM25) and e5 vectors run in parallel on the question; their rankings are fused and the
     code-side rules (narrow) drop variants nobody asked for. The top table is the suggestion.
  3. Every table. The model names the three best tables from a list of all 1,857. How much of each table the list
     shows depends on the context the model gets (hagbot/models.py): titles, years and dimensions when they fit;
     less when not; and when even titles alone do not fit, two passes: subject areas first, then the tables in them.

Each method's table gets a yes or no from the model: does it hold the figures the question asks for? The first yes
answers. When all three say no, the user picks from each method's top three.

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
OPTIONS = 3            # each method's tables offered to the user when every method's suggestion was rejected
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


def dictionary_tables(found: list[dict], catalog: Catalog) -> tuple[list[CatalogEntry], dict | None]:
    """Step 1, best first: the tables of the most specific subject matched (the one whose matching word is longest),
    then the first table of each other subject matched. The first is the suggestion."""
    with_tables = sorted((c for c in found if any(catalog.get(t) for t in c["tables"])),
                         key=lambda c: -len(c["_matched"]))
    if not with_tables:
        return [], None
    best, *others = with_tables
    ids = [*best["tables"], *(c["tables"][0] for c in others)]
    return [e for t in dict.fromkeys(ids) if (e := catalog.get(t))], best


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
The reason says in a few words what the table has and what it lacks for this question ("totals per sector, nothing
per inhabitant"), in the language of the question: the user reads it when no table is found.
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


async def fits(llm: LLM, e: CatalogEntry, question: str, original: str) -> tuple[bool, str]:
    """Does this one table answer? The model's yes or no, and its reason (what the table has and lacks)."""
    asked = f"User asked: {original}\nThis part: {question}" if original and original != question else f"Question: {question}"
    v = await llm.complete_json(FITS, f"{asked}\n\nTable:\n{describe(e, f'{question} {original}')}", Fits)
    return v.answers, v.reason.strip()


# ---------- step 3: the model picks from every table ----------

class Pick(BaseModel):
    table_ids: list[str] = Field(description="IDs of the three tables that best answer the question, best first, "
                                             "copied exactly from the list")


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


async def _pick(llm: LLM, entries: list[CatalogEntry], text: str, kind: str, asked: str) -> list[CatalogEntry]:
    # the list goes first and the question last, so the server can reuse the list's work from one question to the next
    system = f"{PICK_RULES.replace('Copy the table ID exactly.', 'Copy the table IDs exactly.')}\n\nTables ({kind}):\n{text}"
    pick = await llm.complete_json(system, asked, Pick)
    by_id = {e.id: e for e in entries}
    return [by_id[t] for t in dict.fromkeys(t.strip() for t in pick.table_ids) if t in by_id][:OPTIONS]


async def pick_from_all(llm: LLM, catalog: Catalog, question: str, original: str, breakdown_by: str = "") \
        -> tuple[list[CatalogEntry], str]:
    """Step 3: the model's three best tables, best first (fewer if it named IDs not on the list), and how they were
    listed, for the step log."""
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
        return [], "subject areas: none chosen"
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

    async def confirmed(e: CatalogEntry, signal: str, score: float) -> bool:
        """The model's yes or no on one method's suggestion."""
        ok, reason = await fits(llm, e, question, original)
        checked.append({"id": e.id, "title": e.title_en, "score": score, "signals": [signal, "yes" if ok else "no"],
                        "reason": reason})
        return ok

    # 1. dictionary: the first table of the most specific subject named
    dict_top, subject = dictionary_tables(found, catalog)
    if dict_top and await confirmed(dict_top[0], f"dictionary: {subject['concept']}", 1.0):
        return done(dict_top[0], dict_top, "keywords")

    # 2. search: keyword search and e5 fused, narrowed by rule; its top table
    hits = catalog.search(text, 2 * k)
    shortlist = narrow([e for e, _ in hits], f"{question} {original}", breakdown_by)[:k]
    score = {e.id: s for e, s in hits}
    search_top = shortlist[:OPTIONS]
    # a method's suggestion is its best table the model has not already turned down
    untried = lambda es: next((e for e in es if e.id not in {c["id"] for c in checked}), None)
    if (s := untried(shortlist)) and await confirmed(s, f"search #{shortlist.index(s) + 1}", round(score.get(s.id, 0.0), 4)):
        return done(s, [*shortlist, *dict_top], "search")

    # 3. every table: the model's best three, listed to fit its context
    all_top, how = await pick_from_all(llm, catalog, question, original, breakdown_by)
    if (a := untried(all_top)) and await confirmed(a, how, 1.0):
        return done(a, [*all_top, *shortlist, *dict_top], "all tables")

    # 4. every suggestion was rejected: the user picks from each method's top three
    options = list({e.id: e for e in [*dict_top[:OPTIONS], *search_top, *all_top]}.values())
    if not options:
        raise LookupError(f"No table found for “{question}”")
    return Retrieved(table=options[0], alternatives=options[1:], ambiguous=True, candidates=[e.id for e in options],
                     via="user choice", concepts=[c["concept"] for c in found], ranking=checked)


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
