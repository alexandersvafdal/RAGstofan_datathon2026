"""Search only, no model: is an accepted table among the first k search hits? Seconds per run, so use it to compare
card texts and search settings before a full eval.

    python -m eval.retrieval_eval                               # every question file in eval/
    python -m eval.retrieval_eval --questions eval/questions_official.jsonl --k 5 15
    python -m eval.retrieval_eval --catalog data/catalog_enriched.json --show-misses

A hard question names two tables; it counts only when both are found. The search runs on the question as asked,
not on the planner's rewording, so this is a lower bound on what the pipeline sees.
"""

import argparse
import json
from collections import defaultdict
from pathlib import Path

from hagbot.catalog import Catalog
from hagbot.config import settings
from hagbot.steps.retrieve import narrow

HERE = Path(__file__).parent


def ranks(catalog: Catalog, q: dict, depth: int) -> list[int | None]:
    """For each expected table, its 1-based rank in the narrowed search hits (None if not found)."""
    hits = narrow([e for e, _ in catalog.search(q["question"], 2 * depth)], q["question"])[:depth]
    ids = [e.id for e in hits]
    out = []
    for want in q["expected_tables"]:
        found = [ids.index(t) + 1 for t in want.split("|") if t in ids]
        out.append(min(found) if found else None)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--questions", nargs="*", default=sorted(str(p) for p in HERE.glob("questions*.jsonl")))
    ap.add_argument("--catalog", default=str(settings.catalog_path))
    ap.add_argument("--k", nargs="*", type=int, default=[1, 5, 15])
    ap.add_argument("--show-misses", action="store_true")
    args = ap.parse_args()

    catalog = Catalog.load(Path(args.catalog))
    depth = max(args.k)
    print(f"catalog {args.catalog}: {len(catalog.entries)} tables\n")
    print(f"{'file':28} {'n':>3}  " + "  ".join(f"top{k:<3}" for k in args.k))
    misses = []
    for path in args.questions:
        qs = [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]
        found = defaultdict(int)
        for q in qs:
            r = ranks(catalog, q, depth)
            for k in args.k:
                found[k] += all(x is not None and x <= k for x in r)
            if any(x is None for x in r):
                misses.append((Path(path).stem, q, r))
        print(f"{Path(path).stem:28} {len(qs):>3}  " + "  ".join(f"{found[k]:>3}   " for k in args.k))
    if args.show_misses:
        print(f"\nnot in the first {depth}:")
        for name, q, r in misses:
            lost = [w.split("|")[0] for w, x in zip(q["expected_tables"], r) if x is None]
            print(f"  {name[:12]:12} {q['id'][:30]:30} wants {','.join(lost):10} | {q['question'][:70]}")


if __name__ == "__main__":
    main()
