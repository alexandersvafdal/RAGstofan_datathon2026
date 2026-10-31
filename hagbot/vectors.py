"""Vector search over table cards with an embedding model (bge-m3 through Ollama by default), fused with BM25.

A card is the table's titles, dimensions and, when the catalog has them, its description and search words. Vectors are
built once by `python -m scripts.build_vectors` and stored next to the catalog; the question is embedded per request
by the same model. No numpy: 1,857 dot products of 1,024 numbers take a few hundred milliseconds in plain Python.
"""

import json
import math
from array import array
from pathlib import Path

import httpx

from .config import settings


def card_text(e) -> str:
    """Short on purpose: an embedding of a few dozen words says more than one of a few hundred."""
    words = ", ".join([*e.keywords_en, *e.keywords_is])
    return " ".join(x for x in [f"{e.title_en}. {e.title_is}.", f"{', '.join(e.dimensions)}.", e.about_en,
                                f"{words}." if words else ""] if x)


def _unit(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def embed(texts: list[str], client: httpx.Client | None = None) -> list[list[float]]:
    c = client or httpx.Client(timeout=120)
    r = c.post(f"{settings.embed_base_url}/embeddings", json={"model": settings.embed_model, "input": texts},
               headers={"Authorization": f"Bearer {settings.llm_api_key}"})
    r.raise_for_status()
    return [_unit(d["embedding"]) for d in sorted(r.json()["data"], key=lambda d: d["index"])]


class Vectors:
    name = "bge-m3"

    def __init__(self, ids: list[str], flat: array, dim: int):
        self.ids, self.flat, self.dim = ids, flat, dim
        self.client = httpx.Client(timeout=30)

    @staticmethod
    def paths(catalog_path: Path) -> tuple[Path, Path]:
        stem = catalog_path.with_suffix("")
        return Path(f"{stem}.vectors.bin"), Path(f"{stem}.vectors.json")

    @classmethod
    def load(cls, catalog_path: Path) -> "Vectors | None":
        bin_path, meta_path = cls.paths(catalog_path)
        if not (bin_path.exists() and meta_path.exists()):
            return None
        meta = json.loads(meta_path.read_text())
        if meta["model"] != settings.embed_model:
            return None  # vectors from another model cannot be compared with this model's question vectors
        flat = array("f")
        flat.frombytes(bin_path.read_bytes())
        return cls(meta["ids"], flat, meta["dim"])

    @staticmethod
    def save(catalog_path: Path, ids: list[str], vectors: list[list[float]]) -> None:
        bin_path, meta_path = Vectors.paths(catalog_path)
        bin_path.write_bytes(array("f", [x for v in vectors for x in v]).tobytes())
        meta_path.write_text(json.dumps({"model": settings.embed_model, "dim": len(vectors[0]), "ids": ids,
                                         "card": "titles. dimensions. description. search words"}))

    def search(self, query: str, k: int) -> list[tuple[str, float]]:
        try:
            q = embed([query], self.client)[0]
        except httpx.HTTPError:
            return []  # no embedding server: keyword search alone still works
        d, f = self.dim, self.flat
        sims = [(self.ids[i], sum(a * b for a, b in zip(q, f[i * d:(i + 1) * d]))) for i in range(len(self.ids))]
        return sorted(sims, key=lambda x: -x[1])[:k]
