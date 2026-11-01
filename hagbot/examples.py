"""Search by example: questions we already know the table for, embedded with the same e5 model as the table cards.
A new question's nearest known questions vote for their tables. The examples are the generated development sets
(eval/gen_dev_*.jsonl, written per table and per sibling pair), never the frozen halves or the holdout, so those
still measure. One more ranked list for the fusion, next to the dictionary, BM25 and e5 on the cards."""

import hashlib
import json
import logging
import time
from pathlib import Path

import numpy as np

from .config import settings

log = logging.getLogger(__name__)


def load_examples(files: list[Path], langs: set[str]) -> list[tuple[str, str, str]]:
    """(id, question, table) for every single-table question in the files, in the languages asked for."""
    out = []
    for f in files:
        if not f.exists():
            log.warning("examples file missing: %s", f)
            continue
        for line in f.read_text().splitlines():
            if not line.strip():
                continue
            q = json.loads(line)
            if q.get("language") in langs and len(q.get("expected_tables", [])) == 1 and "|" not in q["expected_tables"][0]:
                out.append((q["id"], q["question"], q["expected_tables"][0]))
    return out


class ExampleSearch:
    name = "examples"

    def __init__(self, model, files: list[Path], langs: set[str], cache_dir: Path, model_name: str = "",
                 min_sim: float | None = None):
        self.model = model
        self.model_name = model_name or settings.e5_model
        self.min_sim = settings.example_min_sim if min_sim is None else min_sim
        self.skip: set[str] = set()   # example ids left out, for leave-one-out measurement
        pairs = load_examples(files, langs)
        self.ids = [i for i, _, _ in pairs]
        self.questions = [q for _, q, _ in pairs]
        self.tables = [t for _, _, t in pairs]
        self.vectors = self._embed(cache_dir) if pairs else np.zeros((0, 1), dtype=np.float32)
        log.info("search by example: %d questions for %d tables", len(pairs), len(set(self.tables)))

    def _embed(self, cache_dir: Path) -> np.ndarray:
        key = hashlib.sha256("\n".join([self.model_name, *self.questions]).encode()).hexdigest()[:16]
        cache = cache_dir / f"examples.{key}.npy"
        if cache.exists():
            return np.load(cache)
        t0 = time.perf_counter()
        # "query:" on both sides: question-to-question is a symmetric task for e5
        v = self.model.encode(["query: " + q for q in self.questions], batch_size=32, normalize_embeddings=True,
                              show_progress_bar=False, convert_to_numpy=True).astype(np.float32)
        cache_dir.mkdir(parents=True, exist_ok=True)
        np.save(cache, v)
        log.info("embedded %d example questions in %.0fs", len(self.questions), time.perf_counter() - t0)
        return v

    def search(self, query: str, k: int) -> list[tuple[str, float]]:
        if not self.questions:
            return []
        q = self.model.encode("query: " + query, normalize_embeddings=True, convert_to_numpy=True)
        sims = self.vectors @ q
        best: dict[str, float] = {}
        for i in np.argsort(-sims)[: 4 * k]:   # a table scores as its closest example question
            if sims[i] < self.min_sim:         # a question unlike every example votes for nothing
                break
            if self.skip and self.ids[i] in self.skip:
                continue
            t = self.tables[i]
            if t not in best:
                best[t] = float(sims[i])
        return sorted(best.items(), key=lambda x: -x[1])[:k]
