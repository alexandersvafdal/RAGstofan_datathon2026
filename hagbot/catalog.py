"""Table catalog and keyword search.

BM25 over two fields: the title/dimension text, and the tables' option labels (municipalities, categories),
weighted lower. Query words match by prefix ("reykjavik" ~ "reykjavikurborg", "atvinnuleysid" ~ "atvinnuleysi"),
words every question shares ("Iceland", "how many") are ignored, and recent tables rank above old ones.
Vector search over the kit's bge-m3 index can be added next to `search` and the score lists fused.
"""

import json
import math
import re
from collections import Counter
from pathlib import Path

from pydantic import BaseModel

from .text import tokens

STOPWORDS = {
    # English question words and words that fit every table
    "what", "how", "many", "much", "the", "of", "in", "is", "was", "are", "were", "did", "does", "do", "and", "a",
    "an", "to", "from", "for", "by", "on", "at", "it", "its", "there", "this", "that", "with", "as", "be", "has",
    "have", "which", "who", "iceland", "icelandic", "icelands", "statistics", "number", "total", "s", "current",
    "currently", "now", "latest",
    # Icelandic
    "hvad", "hve", "hversu", "hvert", "hver", "hvada", "margir", "margar", "morg", "i", "a", "og", "er", "var",
    "voru", "eru", "um", "fra", "til", "vid", "sem", "thad", "island", "islandi", "islands", "hagstofa", "fjoldi",
}
# Question words that name a table's topic differently than its title does. "How many people live in X" is a
# population question, and "live" alone would match "live births".
SYNONYMS = {w: "population" for w in (
    "people", "live", "lives", "living", "persons", "inhabitants", "residents",
    "bua", "byr", "bjuggu", "ibuar", "ibua", "ibuum", "fjolgadi", "fjolgun", "faekkadi",
)} | {w: "consumer price index" for w in ("inflation", "verdbolga", "verdbolgan", "verdbolgu")} \
  | {w: "gross domestic product" for w in ("economic", "economy", "hagvoxtur", "hagvexti", "landsframleidsla")}
VALUES_WEIGHT = 0.6
PREFIX_WEIGHT = 0.8
MIN_PREFIX = 4


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

    def card(self, question: str = "") -> str:
        """The short description the reranker reads, with the options that match words in the question."""
        freq = "monthly" if "M" in self.last_period else "quarterly" if "Q" in self.last_period else "yearly"
        period = f" [{freq}, {self.first_period}–{self.last_period}]" if self.first_period else ""
        card = f"{self.id}: {self.title_en}{period}. Dimensions: {', '.join(self.dimensions)}"
        hits = self.matching_values(question)
        return card + (f". Has options: {', '.join(hits)}" if hits else "")

    def matching_values(self, question: str, limit: int = 4) -> list[str]:
        words = [w for w in tokens(question) if len(w) >= MIN_PREFIX and w not in STOPWORDS and w not in SYNONYMS]
        out = []
        for label in self.values:
            if any(t.startswith(w) or (w.startswith(t) and len(t) >= MIN_PREFIX) for t in tokens(label) for w in words):
                out.append(label)
                if len(out) == limit:
                    break
        return out


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
    def __init__(self, entries: list[CatalogEntry]):
        self.entries = entries
        self._by_id = {e.id: e for e in entries}
        self._main = _Field([tokens(f"{e.title_en} {e.title_is} {' '.join(e.dimensions)} {e.path}") for e in entries])
        self._values = _Field([tokens(" ".join(e.values)) for e in entries], presence_only=True)
        self._vocab = set(self._main.idf) | set(self._values.idf)
        self._recency = [recency_factor(e.last_period, e.title_en) for e in entries]

    @classmethod
    def load(cls, path: Path) -> "Catalog":
        return cls([CatalogEntry(**e) for e in json.loads(path.read_text())])

    def get(self, table_id: str) -> CatalogEntry | None:
        return self._by_id.get(table_id)

    def _expand(self, word: str) -> list[tuple[str, float]]:
        out = [(word, 1.0)] if word in self._vocab else []
        if len(word) >= MIN_PREFIX:
            out += [(v, PREFIX_WEIGHT) for v in self._vocab
                    if v != word and len(v) >= MIN_PREFIX and (v.startswith(word) or word.startswith(v))]
        return out

    def search(self, query: str, k: int = 8) -> list[tuple[CatalogEntry, float]]:
        words = list(dict.fromkeys(w for t in tokens(query) if t not in STOPWORDS for w in SYNONYMS.get(t, t).split()))
        expansions = [x for x in (self._expand(w) for w in words) if x]
        if not expansions:
            return []
        scored = []
        for i, entry in enumerate(self.entries):
            main, vals = self._main.scores(i, expansions), self._values.scores(i, expansions)
            s = sum(main) + VALUES_WEIGHT * sum(vals)
            if s > 0:
                # tables matching every query word (in title or options) beat tables matching one word strongly
                coverage = sum(1 for m, v in zip(main, vals) if m or v) / len(expansions)
                scored.append((entry, s * coverage ** 2 * self._recency[i]))
        return sorted(scored, key=lambda x: -x[1])[:k]
