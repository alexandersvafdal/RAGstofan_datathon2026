"""Search only, no model: is an accepted table among the first k search hits? Seconds per run, so use it to compare
card texts and search settings before a full eval.

    python -m eval.retrieval_eval                               # dev sets + organizers (never the holdout)
    python -m eval.retrieval_eval --questions eval/organizers.jsonl --k 5 15
    python -m eval.retrieval_eval --catalog data/catalog_enriched.json --show-misses
    USE_E5=1 python -m eval.retrieval_eval --rankers bm25 e5 fused   # each search alone, then fused

A hard question names two tables; it counts only when both are found. The search runs on the question as asked,
not on the planner's rewording, so this is a lower bound on what the pipeline sees.
"""

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path

from hagbot.catalog import Catalog
from hagbot.config import settings
from hagbot.steps.retrieve import narrow

HERE = Path(__file__).parent


def ranks(catalog: Catalog, q: dict, depth: int, ranker: str = "fused") -> list[int | None]:
    """For each expected table, its 1-based rank in the narrowed search hits (None if not found)."""
    only = None if ranker == "fused" else ranker
    hits = narrow([e for e, _ in catalog.search(q["question"], 2 * depth, only)], q["question"])[:depth]
    ids = [e.id for e in hits]
    out = []
    for want in q["expected_tables"]:
        found = [ids.index(t) + 1 for t in want.split("|") if t in ids]
        out.append(min(found) if found else None)
    return out


def _field(q: dict, name: str) -> str:
    if name == "area" and "area" not in q:
        return re.match(r"[A-Za-z]+", q["expected_tables"][0]).group(0).upper() if q["expected_tables"] else "-"
    return str(q.get(name, "-"))


def main() -> None:
    ap = argparse.ArgumentParser()
    # the holdout is left out unless named: tuning against it would spoil it
    ap.add_argument("--questions", nargs="*", default=[str(HERE / f) for f in ("dev_core.jsonl", "dev_topics.jsonl", "organizers.jsonl")])
    ap.add_argument("--catalog", default=str(settings.catalog_path))
    ap.add_argument("--k", nargs="*", type=int, default=[1, 5, 15])
    ap.add_argument("--show-misses", action="store_true")
    ap.add_argument("--by", nargs="*", default=[],
                    help="question fields to break the totals down by, e.g. language area kind source "
                         "(area = the expected table's id prefix when the question has none)")
    ap.add_argument("--rankers", nargs="*", default=["fused"],
                    help="fused, or one search alone: bm25, e5 (USE_E5=1), bge-m3 (USE_VECTORS=1)")
    args = ap.parse_args()

    catalog = Catalog.load(Path(args.catalog))
    depth = max(args.k)
    print(f"catalog {args.catalog}: {len(catalog.entries)} tables, searches: "
          f"{', '.join(['bm25', *(d.name for d in catalog.dense)])}\n")
    print(f"{'ranker':8} {'file':28} {'n':>3}  " + "  ".join(f"top{k:<3}" for k in args.k))
    misses = []
    groups: dict[tuple, dict] = defaultdict(lambda: {"n": 0, **{k: 0 for k in args.k}})
    for ranker in args.rankers:
        total, n = defaultdict(int), 0
        for path in args.questions:
            qs = [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]
            found = defaultdict(int)
            for q in qs:
                r = ranks(catalog, q, depth, ranker)
                key = tuple(_field(q, f) for f in args.by)
                groups[key]["n"] += 1
                for k in args.k:
                    hit = all(x is not None and x <= k for x in r)
                    found[k] += hit
                    groups[key][k] += hit
                if any(x is None for x in r):
                    misses.append((ranker, Path(path).stem, q, r))
            n += len(qs)
            for k in args.k:
                total[k] += found[k]
            print(f"{ranker:8} {Path(path).stem:28} {len(qs):>3}  " + "  ".join(f"{found[k]:>3}   " for k in args.k))
        print(f"{ranker:8} {'ALL':28} {n:>3}  " + "  ".join(f"{total[k]:>3}   " for k in args.k) + "\n")
    if args.by:
        # the weak spots: the lowest top-k share first
        print(f"{' / '.join(args.by):36} {'n':>4}  " + "  ".join(f"top{k:<3}" for k in args.k))
        for key, g in sorted(groups.items(), key=lambda kv: (kv[1][args.k[-1]] / kv[1]["n"], -kv[1]["n"])):
            print(f"{' / '.join(key):36} {g['n']:>4}  " + "  ".join(f"{100 * g[k] / g['n']:4.0f}% " for k in args.k))
        print()
    if args.show_misses:
        print(f"\nnot in the first {depth}:")
        for ranker, name, q, r in misses:
            lost = [w.split("|")[0] for w, x in zip(q["expected_tables"], r) if x is None]
            print(f"  {ranker:6} {name[:12]:12} {q['id'][:30]:30} wants {','.join(lost):10} | {q['question'][:70]}")


if __name__ == "__main__":
    main()
