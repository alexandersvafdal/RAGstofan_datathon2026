"""Score the pipeline on a question set: right table, right numbers, latency. Run after every change.

    python -m eval.run_eval                        # all questions
    python -m eval.run_eval --level hard --language is
    python -m eval.run_eval --baseline eval/results/<earlier>.json   # before/after per question

Question format (one JSON object per line):
  expected_tables: every entry must be among the answer's sources; "A|B" accepts either.
  expected_values: every value must appear in the facts or the text (relative tolerance, default 0.5%).
"""

import argparse
import asyncio
import json
import re
import time
from collections import defaultdict
from pathlib import Path

from hagbot.pipeline import Pipeline
from hagbot.schemas import Answer, AskRequest
from hagbot.text import numbers_in

HERE = Path(__file__).parent


def score(q: dict, a: Answer) -> dict:
    got_tables = {s.id for s in a.sources}
    table_ok = all(any(t in got_tables for t in want.split("|")) for want in q["expected_tables"])
    tol = q.get("tolerance", 0.005)
    numbers = [f.value for p in a.parts for f in p.facts] + numbers_in(a.text)
    def found(v: float) -> bool:
        return any(abs(n - v) <= max(abs(v) * tol, 1e-9) for n in numbers)
    values_ok = a.status == "answered" and all(found(v) for v in q["expected_values"])
    # was the right table among the search candidates the model chose from? (search vs. model failure)
    offered = set()
    for st in a.steps:
        m = re.match(r"\S+ from \[(.*?)\]", st.detail) if st.name == "retrieve" else None
        if m:
            offered |= set(m.group(1).split(", "))
    offered_ok = all(any(t in offered for t in want.split("|")) for want in q["expected_tables"])
    return {"table_ok": table_ok, "offered_ok": offered_ok, "answer_ok": values_ok}


async def run(questions: list[dict]) -> list[dict]:
    pipe = Pipeline()
    out = []
    try:
        for q in questions:  # sequential: a local model serves one request at a time
            t0 = time.perf_counter()
            a = await pipe.answer(AskRequest(question=q["question"], language=q.get("language")))
            s = score(q, a)
            out.append({**q, **s, "status": a.status, "seconds": round(time.perf_counter() - t0, 2),
                        "llm_calls": a.llm_calls, "tables": [x.id for x in a.sources], "text": a.text,
                        "steps": [st.model_dump() for st in a.steps]})
            mark = "✓" if s["answer_ok"] else ("~" if s["table_ok"] else "✗")
            print(f"{mark} [{q['level']:6} {q.get('language', '?')}] {out[-1]['seconds']:5.1f}s "
                  f"{q['id']:16} tables={out[-1]['tables']} | {a.text[:90]}")
    finally:
        await pipe.llm.aclose()
    return out


def report(results: list[dict]):
    cells = defaultdict(lambda: [0, 0])
    for r in results:
        for lvl in (r["level"], "all"):
            for lng in (r.get("language", "?"), "together"):
                cells[(lvl, lng)][0] += r["answer_ok"]
                cells[(lvl, lng)][1] += 1
    langs = ["en", "is", "together"]
    print(f"\n{'level':8}" + "".join(f"{x:>12}" for x in langs))
    for lvl in ("easy", "medium", "hard", "all"):
        row = [f"{cells[(lvl, x)][0]} of {cells[(lvl, x)][1]}" if cells[(lvl, x)][1] else "-" for x in langs]
        print(f"{lvl:8}" + "".join(f"{c:>12}" for c in row))
    n = len(results)
    secs = sorted(r["seconds"] for r in results)
    print(f"\nright table offered: {sum(r.get('offered_ok', False) for r in results)}/{n}   "
          f"right table: {sum(r['table_ok'] for r in results)}/{n}   right answer: "
          f"{sum(r['answer_ok'] for r in results)}/{n}   median {secs[n // 2]:.1f}s   max {secs[-1]:.1f}s")


def compare(results: list[dict], baseline_path: Path):
    ids = {r["id"] for r in results}
    base = {r["id"]: r for r in json.loads(baseline_path.read_text())["results"] if r["id"] in ids}
    print(f"\nvs {baseline_path.name}:")
    for r in results:
        b = base.get(r["id"])
        if b and b["answer_ok"] != r["answer_ok"]:
            print(f"  {'FIXED ' if r['answer_ok'] else 'BROKE '} {r['id']}")
    before = sum(b["answer_ok"] for b in base.values())
    print(f"  {before} → {sum(r['answer_ok'] for r in results)} right")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--questions", default=str(HERE / "questions.jsonl"))
    ap.add_argument("--level")
    ap.add_argument("--language")
    ap.add_argument("--ids", help="comma-separated question ids")
    ap.add_argument("--baseline")
    ap.add_argument("--label", default="", help="short name saved with the results, e.g. 'bm25+rerank'")
    args = ap.parse_args()

    qs = [json.loads(line) for line in Path(args.questions).read_text().splitlines() if line.strip()]
    if args.level:
        qs = [q for q in qs if q["level"] == args.level]
    if args.language:
        qs = [q for q in qs if q.get("language") == args.language]
    if args.ids:
        keep = set(args.ids.split(","))
        qs = [q for q in qs if q["id"] in keep]

    results = asyncio.run(run(qs))
    report(results)
    out = HERE / "results" / f"{time.strftime('%Y%m%d-%H%M%S')}{'-' + args.label if args.label else ''}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({"label": args.label, "results": results}, ensure_ascii=False, indent=1))
    print(f"saved {out}")
    if args.baseline:
        compare(results, Path(args.baseline))


if __name__ == "__main__":
    main()
