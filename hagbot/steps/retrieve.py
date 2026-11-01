"""Step 2: find the table. Keyword search gives candidates, the model picks one or asks back."""

import re
from typing import Literal

from pydantic import BaseModel, Field

from ..catalog import MIN_PREFIX, STOPWORDS, Catalog, CatalogEntry, _same_word
from ..llm import LLM
from ..hierarchy import code_of
from ..text import asked_bases, base_year, normalize, tokens


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


# Icelandic breakdown words (normalized) and the dimension name the tables use for them
_DIMENSION_IS = {
    "aldri": "age", "aldur": "age", "aldurshopum": "age", "aldursflokkum": "age", "kyni": "sex", "kyn": "sex",
    "sveitarfelogum": "municipality", "sveitarfelagi": "municipality", "sveitarfelag": "municipality",
    "landshlutum": "region", "landshluta": "region", "landsvaedum": "region", "londum": "country", "landi": "country",
    "lond": "country", "londin": "country", "londunum": "country", "landa": "country", "vidskiptalondum": "country",
    "rikisfangi": "citizenship", "uppruna": "origin", "faedingarlandi": "country", "atvinnugrein": "economic",
    "atvinnugreinum": "economic", "starfsstett": "occupation", "starfsstettum": "occupation", "menntun": "education",
    "menntunarstigi": "education", "manudum": "month", "arsfjordungum": "quarter", "tegund": "type", "tegundum": "type",
    "postnumerum": "postcode", "byggdakjornum": "urban", "skolastigi": "school", "fisktegundum": "species",
    "fisktegund": "species", "hjuskaparstodu": "marital", "trufelogum": "religious", "flokkum": "category",
}


def breakdown_words(q: str) -> list[str]:
    """Dimension names a normalized question asks a breakdown by: "by municipality", "eftir aldri" -> age."""
    out = []
    for w in re.findall(r"(?<!\bwere )(?<!\bwas )(?<!\bare )(?<!\bis )(?<!\bmade )\b(?:by|eftir|skipt eftir|sundurlidad eftir)\s+(\w{3,})", q):
        if w in STOPWORDS or w.isdigit():
            continue
        out.append(_DIMENSION_IS.get(w, w))
    return list(dict.fromkeys(out))


def _title_has(entry: CatalogEntry, word: str) -> bool:
    return any(t.startswith(word) for t in tokens(entry.title_en))


def _year_range(entry: CatalogEntry) -> tuple[int, int] | None:
    """Some catalog entries lack time flags even though their titles state the observation years. A period may be
    worded ("Change 2023-2024"): the years in it count, the last one as the end."""
    title_years = [int(y) for y in re.findall(r"\b(?:18|19|20)\d\d\b", entry.title_en)]
    first = re.findall(r"(?<!\d)(?:18|19|20)\d\d(?!\d)", entry.first_period)   # 2025M09 too, so no \b
    last = re.findall(r"(?<!\d)(?:18|19|20)\d\d(?!\d)", entry.last_period)
    if not first and not last and not title_years:
        return None
    start = int(first[0]) if first else min(title_years) if title_years else int(last[-1])
    end = int(last[-1]) if last else max(title_years) if title_years else start
    if not last and re.search(r"\b(?:18|19|20)\d\d\s*[-–]\s*(?:$|by\b)", entry.title_en):
        end = 9999  # an explicitly open-ended title; category validation still checks the actual periods
    return start, end


def newest_base(entry: CatalogEntry) -> int | None:
    """The newest index base a table offers (2021 for "Base 2021, Base 2009, Base 1987"); None without a base."""
    years = [y for v in entry.values if (y := base_year(v))]
    return max(years) if years else None


def narrow(entries: list[CatalogEntry], question: str, breakdown_by: str = "") -> list[CatalogEntry]:
    """Code-side table rules the model follows unreliably. Each rule only applies if some candidate survives it.
    `breakdown_by` is the planner's reading of the category the user wants the figures split by ("countries"),
    verified against the question; the "by X" pattern is the fallback."""
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
    # "by municipality", "eftir aldri", "by sector": the breakdown named must be one of the table's dimensions;
    # checked only when one of the top hits has it, so "stays were by foreigners" does not throw the right tables out
    by_words = list(dict.fromkeys([_DIMENSION_IS.get(t, t) for t in tokens(breakdown_by)] + breakdown_words(q)))
    for word in by_words:
        has_dim = lambda e, w=word: any(_same_word(t, w) or t[:5] == w[:5] for d in e.dimensions for t in tokens(d) if len(t) >= 4)
        if any(has_dim(e) for e in entries[:3]):
            keep_if(has_dim)
    # A month asked wants a monthly table, a quarterly one failing that, or a yearly table that reports that month
    # ("beneficiaries in December", "students in autumn"). "1 January" / "Jan 1" is the date yearly population
    # figures refer to, not a month. Monthly tables go first; the rest stay, in their order.
    month_q = re.sub(r"\b1\.? ?(january|januar|jan)\b|\b(january|januar|jan)\.? 1(st)?\b", " ", q)
    if asked_months := set(re.findall(MONTHS, month_q)):
        names_month = lambda e: any(re.search(rf"\b{m}", e.title_en.lower()) for m in asked_months if len(m) > 3)
        keep_if(lambda e: "M" in e.last_period or "Q" in e.last_period or names_month(e))
        entries = sorted(entries, key=lambda e: "M" not in e.last_period)
    # An index is read on its current base: tables that only carry old bases (building cost on 1983 and earlier,
    # CPI "previous bases") answer only a question that names one of those bases
    if asked := asked_bases(question):
        keep_if(lambda e: any(base_year(v) in asked for v in [e.title_en, *e.values]))   # CPI: "base 1988=100" in the title
    else:
        keep_if(lambda e: (b := newest_base(e)) is None or b >= 2000)
    # "Subcategories of …", "the main drivers of …": tables listing coded categories (subindices, industries) first.
    # Reordered, not filtered: the model still judges the topic.
    # Not when the user named the dimension to list ("countries"): that dimension, kept above, decides.
    from .select import _LIST, _TOP
    if (re.search(_LIST, q) or re.search(_TOP, q)) and not by_words:
        entries = sorted(entries, key=lambda e: sum(1 for v in e.values if code_of(v)) < 8)
    # Missing time flags must not discard a relevant dated table in favour of an unrelated one.
    keep_if(lambda e: _year_range(e) is not None)
    asked_years = set(int(y) for y in re.findall(r"\b(?:18|19|20)\d\d\b", question))
    if len(asked_years) == 1:
        year = next(iter(asked_years))
        keep_if(lambda e: (span := _year_range(e)) is not None and span[0] <= year <= span[1])
    return entries


def period_end(period: str) -> float:
    """2026M09 -> 2026.75, 2026Q2 -> 2026.5, 2025 -> 2026.0: when the data ends."""
    m = re.match(r"(\d{4})(?:([MQ])(\d+))?", period or "")
    if not m:
        return 0.0
    year, kind, n = int(m.group(1)), m.group(2), int(m.group(3) or 0)
    return year + (n / 12 if kind == "M" else n / 4 if kind == "Q" else 1.0)


# Indexation tables publish the index a month or two ahead, for adjusting loans and contracts ("Indices for financial
# indexation", "Wage index for mortgage payment adjustment", "by Application Month"). Ending later does not make them
# the headline index, so they are swapped in only when the question is about indexation or loans.
_INDEXATION_TABLE = r"indexation|payment adjustment|application month"
_INDEXATION_ASKED = r"index(ation|ed|ing)|loan|mortgage|financial obligation|verdtrygg|\blan\b|lana\b|lanum|afborgun|greidslujofn"


def freshest_sibling(table: CatalogEntry, entries: list[CatalogEntry], question: str) -> CatalogEntry:
    """With no period asked, swap a yearly/quarterly pick for the same measure published more recently next door
    (e.g. labour survey: 3_arstolur 2025 -> 1_manadartolur 2026M09)."""
    q = normalize(question)
    if re.search(r"\b(?:18|19|20)\d\d\b", question) or re.search(MONTHS, q):
        return table
    indexation_ok = bool(re.search(_INDEXATION_ASKED, q))
    # a question about categories keeps a table that lists them, however much later a total-only neighbour ends
    from .select import _LIST, _TOP
    categories = bool(re.search(_LIST, q) or re.search(_TOP, q))
    def coded_n(e): return sum(1 for v in e.values if code_of(v))
    words = [w for w in tokens(question) if len(w) >= MIN_PREFIX and w not in STOPWORDS]
    def text(e): return tokens(f"{e.title_en} {' '.join(e.values)}")
    needed = [w for w in words if any(t.startswith(w) for t in text(table))]
    parent = table.path.rsplit("/", 1)[0]
    base = newest_base(table) or 0   # never trade the current base for an older one (VIS13001 -> VIS13305)
    better = [e for e in entries
              if e.path.rsplit("/", 1)[0] == parent and period_end(e.last_period) > period_end(table.last_period)
              and (newest_base(e) or 0) >= base
              and (indexation_ok or not re.search(_INDEXATION_TABLE, e.title_en, re.I))
              and not (categories and coded_n(table) >= 8 and coded_n(e) < 8)
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
- A question asking for subcategories, components, a breakdown or the main drivers needs a table that lists those
  categories (subindices, by category, effect or contribution of each), not one that only has the total.
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
