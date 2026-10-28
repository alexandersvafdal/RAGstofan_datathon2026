"""Table catalog and keyword search (BM25 over titles in both languages and dimension names).

Vector search over the kit's bge-m3 index can be added next to `search` and the two score lists fused.
"""

import json
import math
from collections import Counter
from pathlib import Path

from pydantic import BaseModel

from .text import tokens


class CatalogEntry(BaseModel):
    id: str
    path: str  # PX-API folder path, e.g. "Ibuar/mannfjoldi/2_byggdir/sveitarfelog"
    title_en: str
    title_is: str = ""
    dimensions: list[str] = []  # dimension labels, e.g. ["Municipality", "Age", "Year", "Sex"]
    first_period: str = ""
    last_period: str = ""
    unit: str = ""

    def card(self) -> str:
        """The short description the reranker and selector read."""
        period = f" [{self.first_period}–{self.last_period}]" if self.first_period else ""
        return f"{self.id}: {self.title_en}{period}. Dimensions: {', '.join(self.dimensions)}"


class Catalog:
    def __init__(self, entries: list[CatalogEntry]):
        self.entries = entries
        self._by_id = {e.id: e for e in entries}
        self._docs = [tokens(f"{e.title_en} {e.title_is} {' '.join(e.dimensions)} {e.path}") for e in entries]
        self._df = Counter(t for d in self._docs for t in set(d))
        self._avgdl = sum(map(len, self._docs)) / max(len(self._docs), 1)

    @classmethod
    def load(cls, path: Path) -> "Catalog":
        return cls([CatalogEntry(**e) for e in json.loads(path.read_text())])

    def get(self, table_id: str) -> CatalogEntry | None:
        return self._by_id.get(table_id)

    def search(self, query: str, k: int = 8) -> list[tuple[CatalogEntry, float]]:
        q = tokens(query)
        n = len(self._docs)
        scores = []
        for entry, doc in zip(self.entries, self._docs):
            tf = Counter(doc)
            s = 0.0
            for t in q:
                if t not in tf:
                    continue
                idf = math.log(1 + (n - self._df[t] + 0.5) / (self._df[t] + 0.5))
                s += idf * tf[t] * 2.2 / (tf[t] + 1.2 * (0.25 + 0.75 * len(doc) / self._avgdl))
            if s > 0:
                scores.append((entry, s))
        return sorted(scores, key=lambda x: -x[1])[:k]
