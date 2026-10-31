"""Vector search over table descriptions with intfloat/multilingual-e5-large, run in-process with sentence-transformers.

Same idea as hagbot/vectors.py (bge-m3 through an HTTP embedding server): embed every table's description once,
embed the question per request, rank tables by cosine similarity. Differences: e5 expects "query: " / "passage: "
prefixes, reads at most 512 tokens, and runs here rather than behind Ollama, which does not serve it.

A table's description joins the catalog (English titles, dimensions, option labels, periods) with the PX metadata
dump in TABLES_META_PATH (Icelandic titles and dimension names). Two layouts, set by E5_CARD:
  single  one passage per table, with the first option labels
  multi   one passage for titles and dimensions, plus one per chunk of option labels; a table scores as its best
          passage, so a long country list can match "Swedes" without blurring the title's meaning
Vectors are cached beside the catalog, keyed by a hash of model and passages, so a changed description rebuilds them.
"""

import hashlib
import json
import logging
import time
from pathlib import Path

import numpy as np

from .config import settings

log = logging.getLogger(__name__)
SINGLE_VALUES = 40  # option labels in a single passage; the rest would be cut at 512 tokens anyway
CHUNK_VALUES = 30  # option labels per extra passage in the multi layout


def load_meta(path: Path) -> dict[str, dict]:
    """Table id -> {"is": {...}, "en": {...}} from the PX metadata dump. A table listed under two folders is kept once."""
    if not path.exists():
        return {}
    out: dict[str, dict] = {}
    for x in json.loads(path.read_text()):
        out.setdefault(x["path"].rsplit("/", 1)[-1].removesuffix(".px"), x)
    return out


def _frequency(period: str) -> str:
    return "monthly" if "M" in period else "quarterly" if "Q" in period else "yearly"


def passages(entry, meta: dict | None, layout: str) -> list[str]:
    is_, en = (meta or {}).get("is", {}), (meta or {}).get("en", {})
    title_is = (entry.title_is or is_.get("title", "")).strip()
    title_en = (entry.title_en or en.get("title", "")).strip()
    dims_is = [v["text"] for v in is_.get("variables", [])]
    dims_en = entry.dimensions or [v["text"] for v in en.get("variables", [])]
    head = [f"{title_is}." if title_is else "", f"{title_en}." if title_en and title_en != title_is else ""]
    if dims_is:
        head.append(f"Breytur: {', '.join(dims_is)}.")
    if dims_en:
        head.append(f"Dimensions: {', '.join(dims_en)}.")
    if entry.first_period:
        head.append(f"{_frequency(entry.last_period).capitalize()} data, {entry.first_period}–{entry.last_period}.")
    if entry.unit:
        head.append(f"Unit: {entry.unit}.")
    card = " ".join(x for x in head if x)
    values = list(dict.fromkeys(v.strip() for v in entry.values if v.strip()))
    if layout == "single":
        return [card + (f" Options: {', '.join(values[:SINGLE_VALUES])}." if values else "")]
    short = title_en or title_is
    return [card] + [f"{short}. Options: {', '.join(values[i:i + CHUNK_VALUES])}."
                     for i in range(0, len(values), CHUNK_VALUES)]


class E5Search:
    name = "e5"

    def __init__(self, entries, meta_path: Path, cache_dir: Path, model_name: str = "", layout: str = ""):
        from sentence_transformers import SentenceTransformer  # heavy import, only when e5 is switched on

        self.model_name = model_name or settings.e5_model
        self.layout = layout or settings.e5_card
        meta = load_meta(meta_path)
        self.ids, texts = [], []
        for e in entries:
            for p in passages(e, meta.get(e.id), self.layout):
                self.ids.append(e.id)
                texts.append(p)
        self.owner = np.array([0] + [i for i in range(1, len(self.ids)) if self.ids[i] != self.ids[i - 1]])
        self.table_ids = [self.ids[i] for i in self.owner]
        self.model = SentenceTransformer(self.model_name, device=settings.e5_device or None)
        self.vectors = self._embed_corpus(texts, cache_dir)

    def _embed_corpus(self, texts: list[str], cache_dir: Path) -> np.ndarray:
        key = hashlib.sha256("\n".join([self.model_name, *texts]).encode()).hexdigest()[:16]
        cache = cache_dir / f"e5.{self.layout}.{key}.npy"
        if cache.exists():
            return np.load(cache)
        log.info("embedding %d passages with %s (once; cached in %s)", len(texts), self.model_name, cache)
        t0 = time.perf_counter()
        v = self.model.encode(["passage: " + t for t in texts], batch_size=32, normalize_embeddings=True,
                              show_progress_bar=True, convert_to_numpy=True).astype(np.float32)
        cache_dir.mkdir(parents=True, exist_ok=True)
        np.save(cache, v)
        log.info("embedded in %.0fs", time.perf_counter() - t0)
        return v

    def search(self, query: str, k: int) -> list[tuple[str, float]]:
        q = self.model.encode("query: " + query, normalize_embeddings=True, convert_to_numpy=True)
        sims = self.vectors @ q
        best = np.maximum.reduceat(sims, self.owner)  # a table scores as its best passage
        top = np.argsort(-best)[:k]
        return [(self.table_ids[i], float(best[i])) for i in top]


if __name__ == "__main__":
    # python -m hagbot.e5 "how many Swedes came to Iceland?" -k 10    (builds the cache on first run)
    import argparse

    from .catalog import CatalogEntry

    ap = argparse.ArgumentParser(description="Top tables for a question by e5 similarity alone")
    ap.add_argument("query", nargs="+")
    ap.add_argument("-k", type=int, default=10)
    args = ap.parse_args()
    entries = [CatalogEntry(**x) for x in json.loads(settings.catalog_path.read_text())]
    by_id = {e.id: e for e in entries}
    search = E5Search(entries, settings.tables_meta_path, settings.cache_dir)
    for rank, (table_id, score) in enumerate(search.search(" ".join(args.query), args.k), 1):
        print(f"{rank:>2}. [{score:.4f}] {table_id:10} {by_id[table_id].title_en}")
