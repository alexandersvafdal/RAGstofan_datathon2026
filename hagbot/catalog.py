"""Table catalog and keyword search.

BM25 over three fields: the title/dimension text; the tables' option labels (municipalities, categories),
weighted lower; and, when the catalog has them, a model-written description and everyday search words per table
(scripts/enrich_catalog.py), weighted lower again. Query words match by prefix ("reykjavik" ~ "reykjavikurborg", "atvinnuleysid" ~ "atvinnuleysi"),
words every question shares ("Iceland", "how many") are ignored, and recent tables rank above old ones.
Vector searches run beside it when switched on: bge-m3 (hagbot/vectors.py, USE_VECTORS=1) and e5 (hagbot/e5.py,
USE_E5=1). The searches run in parallel and their rankings are fused by reciprocal rank.
"""

import json
import logging
import math
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pydantic import BaseModel

from .config import ROOT, settings
from .text import tokens
from .vectors import Vectors

STOPWORDS = {
    # English question words and words that fit every table
    "what", "how", "many", "much", "the", "of", "in", "is", "was", "are", "were", "did", "does", "do", "and", "a",
    "an", "to", "from", "for", "by", "on", "at", "it", "its", "there", "this", "that", "with", "as", "be", "has",
    "have", "which", "who", "iceland", "icelandic", "icelands", "statistics", "number", "total", "s", "current",
    "currently", "now", "latest", "more", "than", "less", "fewer", "together", "combined",
    # Icelandic
    "hvad", "hve", "hversu", "hvert", "hver", "hvada", "margir", "margar", "morg", "i", "a", "og", "er", "var",
    "voru", "eru", "um", "fra", "til", "vid", "sem", "thad", "island", "islandi", "islands", "hagstofa", "fjoldi",
    "fleiri", "faerri", "en", "samtals", "morgum",
}
# Question words that name a table's topic differently than its title does. "How many people live in X" is a
# population question, and "live" alone would match "live births".
SYNONYMS = {w: "population" for w in (
    "people", "live", "lives", "living", "persons", "inhabitants", "residents",
    "bua", "byr", "bjuggu", "ibuar", "ibua", "ibuum", "fjolgadi", "fjolgun", "faekkadi",
)} | {w: "consumer price index" for w in ("inflation", "verdbolga", "verdbolgan", "verdbolgu")} \
  | {w: "gross domestic product" for w in ("economic", "economy", "hagvoxtur", "hagvexti", "landsframleidsla")}
# Tables label the sexes "Males"/"Females" (Karlar/Konur in Icelandic tables are matched through the Icelandic titles);
# "men"/"women" literally only appear in odd categories such as "Majority men" on company boards.
SYNONYMS |= {w: "males" for w in ("men", "man", "male", "karlar", "karla", "karl", "karlmenn", "karlmanna")} \
  | {w: "females" for w in ("women", "woman", "female", "konur", "kvenna", "kona", "konum")}
VALUES_WEIGHT = 0.6
ABOUT_WEIGHT = 0.5
PREFIX_WEIGHT = 0.8
MIN_PREFIX = 4
log = logging.getLogger(__name__)
RRF_K = 60  # reciprocal rank fusion: a table's fused score is the sum of 1 / (RRF_K + rank) over the rankings


class CatalogEntry(BaseModel):
    id: str
    path: str  # PX-API folder path, e.g. "Ibuar/mannfjoldi/2_byggdir/sveitarfelog"
    title_en: str
    title_is: str = ""
    dimensions: list[str] = []  # dimension labels, e.g. ["Municipality", "Age", "Year", "Sex"]
    values: list[str] = []  # non-time option labels, e.g. ["Reykjavikurborg", "Kopavogsbaer", ...]
    first_period: str = ""
    last_period: str = ""
    unit: str = ""
    about_en: str = ""
    about_is: str = ""
    keywords_en: list[str] = []
    keywords_is: list[str] = []
    source_summary: str = ""  # source profile hint for discovery; selection still checks current source metadata

    def card(self, question: str = "") -> str:
        """The short description the reranker reads, with the options that match words in the question."""
        freq = "monthly" if "M" in self.last_period else "quarterly" if "Q" in self.last_period else "yearly"
        period = f" [{freq}, {self.first_period}–{self.last_period}]" if self.first_period else ""
        # the Icelandic title too: an Icelandic question's own words ("launavísitala", "byggðakjarnar") are on it
        is_title = f" ({self.title_is})" if self.title_is and self.title_is != self.title_en else ""
        card = f"{self.id}: {self.title_en}{is_title}{period}. Dimensions: {', '.join(self.dimensions)}"
        hits = self.matching_values(question)
        return card + (f". Has options: {', '.join(hits)}" if hits else "") + \
            (f". Source definitions: {self.source_summary[:360]}" if self.source_summary else "")

    def matching_values(self, question: str, limit: int = 4) -> list[str]:
        words = [w for w in tokens(question) if len(w) >= MIN_PREFIX and w not in STOPWORDS and w not in SYNONYMS]
        out = []
        for label in self.values:
            if any(_same_word(t, w) for t in tokens(label) for w in words):
                out.append(label)
                if len(out) == limit:
                    break
        return out


def _same_word(label_token: str, word: str) -> bool:
    """A question word names an option: by prefix either way, or by a shared stem of 7+ letters, so "Kópavogur",
    "Kópavogi" and "Hafnarfjörður" find "Kopavogsbaer" and "Hafnarfjardarkaupstadur"."""
    return (label_token.startswith(word) or (word.startswith(label_token) and len(label_token) >= MIN_PREFIX)
            or (len(label_token) >= 7 and len(word) >= 7 and label_token[:7] == word[:7]))


def recency_factor(last_period: str, title: str = "") -> float:
    """Recent tables first. Tables without a period (census, one-off tables) fall back to the last year in the title."""
    m = re.match(r"(\d{4})", last_period or "")
    years = re.findall(r"\b(?:18|19|20)\d\d\b", title)
    if not m and not years:
        return 0.85
    year = int(m.group(1)) if m else int(years[-1])
    return 1.1 if year >= 2025 else 1.0 if year >= 2020 else 0.85 if year >= 2015 else 0.7


class _Field:
    """BM25 statistics for one text field across all entries.

    With presence_only, a term counts once regardless of frequency or document length: option lists are long
    (63 municipalities × 111 ages), and one matching option should count as much in a long list as in a short one.
    """

    def __init__(self, docs: list[list[str]], presence_only: bool = False):
        self.presence_only = presence_only
        self.tf = [Counter(d) for d in docs]
        self.len = [len(d) for d in docs]
        self.avgdl = sum(self.len) / max(len(docs), 1) or 1.0
        df = Counter(t for d in docs for t in set(d))
        n = len(docs)
        self.idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}

    def scores(self, i: int, expansions: list[list[tuple[str, float]]]) -> list[float]:
        """One score per query word: its best-matching term in this document."""
        tf, norm = self.tf[i], 1.2 * (0.25 + 0.75 * self.len[i] / self.avgdl)
        out = []
        for options in expansions:
            best = 0.0
            for term, w in options:
                f = tf.get(term)
                if f:
                    best = max(best, w * self.idf[term] * (1.0 if self.presence_only else f * 2.2 / (f + norm)))
            out.append(best)
        return out


class Catalog:
    def __init__(self, entries: list[CatalogEntry], dense: list | None = None, profiles: dict | None = None):
        """dense: vector searches, each with .name and .search(query, k) -> [(table_id, score)]."""
        self.entries = entries
        self.dense = dense or []
        self._pool = ThreadPoolExecutor(max_workers=1 + len(self.dense))
        self._by_id = {e.id: e for e in entries}
        self._main = _Field([tokens(f"{e.title_en} {e.title_is} {' '.join(e.dimensions)} {e.path}") for e in entries])
        self._values = _Field([tokens(" ".join(e.values)) for e in entries], presence_only=True)
        self._about = _Field([tokens(" ".join([e.about_en, e.about_is, *e.keywords_en, *e.keywords_is])) for e in entries],
                             presence_only=True)
        from .profiles import discovery_text
        texts = {e.id: discovery_text(profiles[e.id]) for e in entries if profiles and e.id in profiles
                 and profiles[e.id].id == e.id and profiles[e.id].path == e.path}
        self._source = _Field([tokens(texts.get(e.id, "")) for e in entries], presence_only=True)
        for e in entries:
            e.source_summary = texts.get(e.id, "")
        self._vocab = set(self._main.idf) | set(self._values.idf) | set(self._about.idf) | set(self._source.idf)
        self._recency = [recency_factor(e.last_period, e.title_en) for e in entries]

    @classmethod
    def load(cls, path: Path) -> "Catalog":
        entries = [CatalogEntry(**e) for e in json.loads(path.read_text())]
        dense = []
        if settings.use_vectors and (v := Vectors.load(path)):
            dense.append(v)
        if settings.use_e5:
            try:
                from .e5 import E5Search
                e5 = E5Search(entries, settings.tables_meta_path, settings.cache_dir)
                dense.append(e5)
                if settings.use_examples:   # the same model, over questions whose table we know
                    from .examples import ExampleSearch
                    files = [ROOT / f.strip() for f in settings.example_files.split(",") if f.strip()]
                    dense.append(ExampleSearch(e5.model, files, set(settings.example_langs.split(",")), settings.cache_dir))
            except ImportError as err:
                log.warning("e5 search off: %s. Install requirements-e5.txt or set USE_E5=0", err)
        from .profiles import load_profiles
        return cls(entries, dense, load_profiles(settings.profiles_path))

    def get(self, table_id: str) -> CatalogEntry | None:
        return self._by_id.get(table_id)

    def _expand(self, word: str) -> list[tuple[str, float]]:
        out = [(word, 1.0)] if word in self._vocab else []
        if len(word) >= MIN_PREFIX:
            out += [(v, PREFIX_WEIGHT) for v in self._vocab
                    if v != word and len(v) >= MIN_PREFIX and (v.startswith(word) or word.startswith(v))]
        return out

    def rankings(self, query: str, depth: int) -> dict[str, list[str]]:
        """Each search's own ranked table ids, the searches run in parallel."""
        jobs = {"bm25": self._pool.submit(lambda: [e.id for e, _ in self.keyword_search(query, depth)])}
        for d in self.dense:
            jobs[d.name] = self._pool.submit(lambda d=d: [t for t, _ in d.search(query, depth)])
        return {name: job.result() for name, job in jobs.items()}

    def search(self, query: str, k: int = 8, only: str | None = None) -> list[tuple[CatalogEntry, float]]:
        """The first k tables of the fused ranking, or of one search's ranking ("bm25", "e5", "bge-m3")."""
        ranked = self.rankings(query, 4 * k)
        if only:
            return [(self._by_id[t], 1.0 / r) for r, t in enumerate(ranked.get(only, []), 1) if t in self._by_id][:k]
        if not any(ranked[d.name] for d in self.dense):
            return self.keyword_search(query, k)  # no vector search, or its server is down
        fused: dict[str, float] = {}
        for ranking in ranked.values():
            for rank, table_id in enumerate(ranking, 1):
                fused[table_id] = fused.get(table_id, 0.0) + 1 / (RRF_K + rank)
        index = {e.id: i for i, e in enumerate(self.entries)}
        scored = [(self.entries[index[t]], s * self._recency[index[t]]) for t, s in fused.items() if t in index]
        return sorted(scored, key=lambda x: -x[1])[:k]

    def keyword_search(self, query: str, k: int = 8) -> list[tuple[CatalogEntry, float]]:
        words = list(dict.fromkeys(w for t in tokens(query) if t not in STOPWORDS for w in SYNONYMS.get(t, t).split()))
        expansions = [x for x in (self._expand(w) for w in words) if x]
        if not expansions:
            return []
        scored = []
        for i, entry in enumerate(self.entries):
            main, vals = self._main.scores(i, expansions), self._values.scores(i, expansions)
            about = self._about.scores(i, expansions)
            source = self._source.scores(i, expansions)
            s = sum(main) + VALUES_WEIGHT * sum(vals) + ABOUT_WEIGHT * sum(about) + 0.6 * sum(source)
            if s > 0:
                # tables matching every query word (in any field) beat tables matching one word strongly
                coverage = sum(1 for m, v, a, p in zip(main, vals, about, source) if m or v or a or p) / len(expansions)
                scored.append((entry, s * coverage ** 2 * self._recency[i]))
        return sorted(scored, key=lambda x: -x[1])[:k]
