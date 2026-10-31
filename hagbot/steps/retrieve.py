"""Step 2: find the table. Keyword search gives candidates, the model picks one or asks back."""

import re
from typing import Literal

from pydantic import BaseModel, Field

from ..catalog import MIN_PREFIX, STOPWORDS, Catalog, CatalogEntry
from ..llm import LLM
from ..text import normalize, tokens


class Pick(BaseModel):
    table_id: str = Field(description="ID of the best table, copied exactly from the list")
    confidence: Literal["high", "low"] = Field(
        description="low only if two or more tables fit equally well and the question does not say which"
    )
    alternatives: list[str] = Field(default_factory=list, description="IDs of other tables that fit equally well")


class Retrieved(BaseModel):
    table: CatalogEntry
    alternatives: list[CatalogEntry] = []
    ambiguous: bool = False
    candidates: list[str] = []
    via: str = ""  # tiered finder: which tier found the table
    concepts: list[str] = []  # tiered finder: dictionary concepts the question matched
    ranking: list[dict] = []  # tiered finder: the ranked hitlist (id, title, score, signals)
    votes: str = ""  # voting on: how many votes the chosen table got, e.g. "3/3"


# Words with no table of their own, that mean different statistics: ask which one instead of guessing.
# (question pattern, words that already settle it, the tables it could mean)
AMBIGUOUS = [
    (r"\btourist|\bvisitor|ferdam|ferdafolk", r"passeng|airport|flug|farþeg|farthe|accommodation|stay|night|gist|hotel",
     ["SAM02001", "SAM01601"]),
]


# Table variants nobody gets unless they ask: (pattern in the title, words in the question that ask for it)
QUALIFIERS = [
    (r"\btrend\b", r"trend|leitni"),
    (r"seasonally adjusted", r"seasonal|arstidaleidr"),
    (r"projection|forecast", r"projection|forecast|future|spa\b|mannfjoldaspa|thjodhagsspa"),
    (r"average annual", r"average|medal"),
    (r"^census\b|\bcensus\b", r"census|manntal"),
    (r"change of method|overestimation", r"method|overestimat|ofmat"),
    (r"as of (?:18|19)\d\d", r"\b(?:18|19)\d\d\b"),  # old municipality divisions
]
MONTHS = (r"\b(?:january|february|march|april|may|june|july|august|september|october|november|december|"
          r"jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec|"
          r"januar|februar|mars|mai|juni|juli|agust|oktober|desember|monthly|manudi|manadarlega)\b")


def named_places(text: str) -> list[str]:
    """Capitalised words that do not start a sentence: Reykjavík, Kópavogur, Akureyri …"""
    words = re.findall(r"(?<![.?!]\s)(?<!^)\b([A-ZÁÐÉÍÓÚÝÞÆÖ][\wáðéíóúýþæö]+)", text.strip())
    return list(dict.fromkeys(t for w in words for t in tokens(w)
                              if len(t) >= MIN_PREFIX and t not in STOPWORDS and not re.fullmatch(MONTHS, t)))


def _title_has(entry: CatalogEntry, word: str) -> bool:
    return any(t.startswith(word) for t in tokens(entry.title_en))


def narrow(entries: list[CatalogEntry], question: str) -> list[CatalogEntry]:
    """Code-side table rules the model follows unreliably. Each rule only applies if some candidate survives it."""
    def keep_if(pred):
        nonlocal entries
        kept = [e for e in entries if pred(e)]
        entries = kept or entries

    q = normalize(question)
    keep_if(lambda e: not any(re.search(t, e.title_en.lower()) and not re.search(w, q) for t, w in QUALIFIERS))
    # a named place must be in the table's title or options; checked only when one of the top search hits has it,
    # so a capitalised word that is not a place ("American") does not throw out the right tables
    for place in named_places(question):
        has = lambda e: bool(e.matching_values(place)) or _title_has(e, place)
        if any(has(e) for e in entries[:3]):
            keep_if(has)
    # a month asked needs a monthly table; "1 January" is the date yearly population figures refer to, not a month
    if re.search(MONTHS, re.sub(r"\b1\.? ?(january|januar|jan)\b", " ", q)):
        keep_if(lambda e: "M" in e.last_period)
    # tables without a known period cannot answer "latest" or a given year reliably
    keep_if(lambda e: bool(e.last_period))
    return entries


def period_end(period: str) -> float:
    """2026M09 -> 2026.75, 2026Q2 -> 2026.5, 2025 -> 2026.0: when the data ends."""
    m = re.match(r"(\d{4})(?:([MQ])(\d+))?", period or "")
    if not m:
        return 0.0
    year, kind, n = int(m.group(1)), m.group(2), int(m.group(3) or 0)
    return year + (n / 12 if kind == "M" else n / 4 if kind == "Q" else 1.0)


def freshest_sibling(table: CatalogEntry, entries: list[CatalogEntry], question: str) -> CatalogEntry:
    """With no period asked, swap a yearly/quarterly pick for the same measure published more recently next door
    (e.g. labour survey: 3_arstolur 2025 -> 1_manadartolur 2026M09)."""
    if re.search(r"\b(?:18|19|20)\d\d\b", question) or re.search(MONTHS, normalize(question)):
        return table
    words = [w for w in tokens(question) if len(w) >= MIN_PREFIX and w not in STOPWORDS]
    def text(e): return tokens(f"{e.title_en} {' '.join(e.values)}")
    needed = [w for w in words if any(t.startswith(w) for t in text(table))]
    parent = table.path.rsplit("/", 1)[0]
    better = [e for e in entries
              if e.path.rsplit("/", 1)[0] == parent and period_end(e.last_period) > period_end(table.last_period)
              and all(any(t.startswith(w) for t in text(e)) for w in needed)]
    return max(better, key=lambda e: period_end(e.last_period)) if better else table


SYSTEM = """You choose which Statistics Iceland table answers a question.
Rules:
- "How many people live in X" means population on 1 January, current municipality division, unless the question says otherwise.
- Prefer the table whose period covers the year asked about; with no year, prefer the table whose data ends latest,
  and monthly over quarterly over yearly tables.
- "Population" means the registered population on 1 January, not average annual population or projections.
- Prefer the most direct table over one that needs extra breakdowns.
- Prefer original (unadjusted) figures unless the question asks for seasonally adjusted or trend figures.
- Prefer monthly tables when a month is asked, yearly tables when only a year is asked.
- A place or category the question names should be among the table's options ("Has options").
Copy the table ID exactly."""


async def retrieve(llm: LLM, catalog: Catalog, question: str, search_query: str, k: int,
                   forced_table: str | None = None, original: str = "") -> Retrieved:
    if forced_table and (entry := catalog.get(forced_table)):
        return Retrieved(table=entry, candidates=[entry.id])
    q = normalize(original or question)  # the user's own words: the planner may already have guessed ("passengers")
    for pattern, settled, tables in AMBIGUOUS:
        entries = [e for e in (catalog.get(t) for t in tables) if e]
        if re.search(pattern, q) and not re.search(settled, q) and len(entries) > 1:
            return Retrieved(table=entries[0], alternatives=entries[1:], ambiguous=True, candidates=[e.id for e in entries])
    # the original wording matches Icelandic titles even when the planner mistranslates it
    hits = catalog.search(f"{search_query} {question} {original}", 2 * k)
    if not hits:
        raise LookupError(f"No table matches “{question}”")
    entries = narrow([e for e, _ in hits], f"{question} {original}")[:k]
    if len(entries) == 1:
        return Retrieved(table=entries[0], candidates=[entries[0].id])

    cards = "\n".join(f"- {e.card(f'{question} {original}')}" for e in entries)
    asked = f"User asked: {original}\nThis part: {question}" if original and original != question else f"Question: {question}"
    pick = await llm.complete_json(SYSTEM, f"{asked}\n\nTables:\n{cards}", Pick)
    by_id = {e.id: e for e in entries}
    table = by_id.get(pick.table_id.strip(), entries[0])  # an invented ID falls back to the top search hit
    table = freshest_sibling(table, entries, f"{question} {original}")
    alts = [by_id[a] for a in pick.alternatives if a in by_id and a != table.id]
    return Retrieved(
        table=table,
        alternatives=alts,
        ambiguous=pick.confidence == "low" and bool(alts),
        candidates=list(by_id),
    )
