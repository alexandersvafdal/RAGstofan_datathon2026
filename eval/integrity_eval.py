"""Repeat labeled development questions against a running server; measure only after implementation.

python -m eval.integrity_eval --questions eval/dev_core.jsonl --endpoint http://HOST:8002 --repeat 3 --output /tmp/integrity.json
python -m eval.integrity_eval --summarize /tmp/integrity.json

Frozen/holdout paths require --measurement. This runner never changes prompts,
dictionary entries or code. Table-only labels are not counted as answer accuracy.
"""

import argparse
import asyncio
import json
import math
import re
import time
from collections import Counter
from pathlib import Path

import httpx

from hagbot.schemas import Answer


def grade(question: dict, answer: Answer) -> dict:
    expected = question.get("expected_status", "answered")
    expected = {expected} if isinstance(expected, str) else set(expected)
    answered = answer.status == "answered"
    tables = {s.id for s in answer.sources}
    table_ok = all(any(t in tables for t in wanted.split("|")) for wanted in question.get("expected_tables", []))
    facts = [f for p in answer.parts for f in p.facts]
    tol = question.get("tolerance", 0.005)
    close = lambda actual, wanted: abs(actual - wanted) <= max(abs(wanted) * tol, 1e-9)
    value_ok = all(any(close(f.value, value) for f in facts) for value in question.get("expected_values", []))
    bound_ok = all(any(close(f.value, wanted["value"]) and (not wanted.get("unit") or f.unit == wanted["unit"])
                       and (not wanted.get("label_contains") or wanted["label_contains"].casefold() in f.label.casefold())
                       and (not wanted.get("period") or wanted["period"] in (f.periods or re.findall(r"\b\d{4}(?:M\d{2}|Q[1-4])?\b", f.label)))
                       for f in facts) for wanted in question.get("expected_facts", []))
    calculation_ok = ("expected_result" not in question or any(
        p.computation and close(p.computation.result, question["expected_result"])
        and (not question.get("expected_operation") or p.computation.op == question["expected_operation"])
        for p in answer.parts))
    status_ok = answer.status in expected
    labeled_values = bool(question.get("expected_values") or question.get("expected_facts") or "expected_result" in question)
    graded_answer = "answered" not in expected or bool(question.get("expected_tables") and labeled_values)
    correct = status_ok and table_ok and value_ok and bound_ok and calculation_ok if graded_answer else None
    return {"status_ok": status_ok, "table_ok": table_ok, "value_ok": value_ok, "bound_ok": bound_ok,
            "calculation_ok": calculation_ok, "correct": correct,
            "false_refusal": expected == {"answered"} and answer.status in {"clarify", "unavailable", "out_of_scope", "unverified"},
            "wrong_answer": answered and (not status_ok or not table_ok or not value_ok or not bound_ok or not calculation_ok),
            "graded_answer": graded_answer, "expected_answered": expected == {"answered"}}


def percentile(values, fraction):
    if not values:
        return None
    values = sorted(values)
    return values[max(0, math.ceil(len(values) * fraction) - 1)]


def summarize(rows: list[dict]) -> dict:
    answered = [r for r in rows if r["status"] == "answered"]
    graded = [r for r in rows if r["correct"] is not None]
    graded_answers = [r for r in answered if r["graded_answer"]]
    answerable = [r for r in rows if r["expected_answered"]]
    ratio = lambda a, b: a / b if b else None
    return {"requests": len(rows), "outcomes": dict(Counter(r["status"] for r in rows)),
            "graded": len(graded), "correct": sum(r["correct"] for r in graded),
            "answer_precision": ratio(sum(r["correct"] for r in graded_answers), len(graded_answers)),
            "answerable_coverage": ratio(sum(r["status"] == "answered" for r in answerable), len(answerable)),
            "false_refusals": sum(r["false_refusal"] for r in rows),
            "false_refusal_rate": ratio(sum(r["false_refusal"] for r in rows), len(answerable)),
            "known_wrong_answers": sum(r["wrong_answer"] for r in rows),
            "technical_errors": sum(r["status"] == "error" for r in rows),
            "issue_codes": dict(Counter(code for r in rows for code in r.get("issue_codes", []))),
            "latency_p50_s": percentile([r["seconds"] for r in rows], .5),
            "latency_p95_s": percentile([r["seconds"] for r in rows], .95),
            "answered_latency_p95_s": percentile([r["seconds"] for r in answered], .95),
            "under_30s": sum(r["seconds"] < 30 for r in rows),
            "mean_model_calls": ratio(sum(r["llm_calls"] for r in rows), len(rows)),
            "retries": sum(r["retries"] for r in rows)}


async def run(args, questions):
    results = []
    async with httpx.AsyncClient(base_url=args.endpoint.rstrip("/"), timeout=args.client_timeout) as client:
        response = await client.get("/api/health")
        response.raise_for_status()
        health = response.json()
        for repetition in range(args.repeat):
            for q in questions:
                started = time.monotonic()
                request = {"question": q["question"], "language": q.get("language"),
                           "table_id": q.get("table_id"), "model": args.model, "reasoning": args.reasoning}
                try:
                    response = await client.post("/api/ask", json=request)
                    response.raise_for_status()
                    answer = Answer.model_validate(response.json())
                except (httpx.HTTPError, ValueError) as error:
                    answer = Answer(question=q["question"], language=q.get("language") or "en", status="error", text=str(error))
                result = {"id": q["id"], "repeat": repetition + 1, "language": q.get("language"), **grade(q, answer),
                          "status": answer.status, "seconds": round(time.monotonic() - started, 3),
                          "llm_calls": answer.llm_calls, "issue_codes": [i.code for i in answer.issues],
                          "retries": sum(s.name == "retry" for s in answer.steps), "answer": answer.model_dump()}
                results.append(result)
                # Keep partial runs reviewable, including failures and latency.
                save(args.output, health, results)
                print(f"{q['id']} [{repetition + 1}/{args.repeat}] {answer.status} {result['seconds']:.1f}s correct={result['correct']}")
    return health, results


def save(path, health, results):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({"server": health, "summary": summarize(results), "results": results}, ensure_ascii=False, indent=2))
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--questions")
    parser.add_argument("--endpoint")
    parser.add_argument("--output", default="/tmp/integrity-eval.json")
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--model")
    parser.add_argument("--reasoning", choices=["none", "low", "medium", "high"], default="none")
    parser.add_argument("--client-timeout", type=float, default=45)
    parser.add_argument("--measurement", action="store_true")
    parser.add_argument("--summarize")
    args = parser.parse_args()
    if args.summarize:
        print(json.dumps(summarize(json.loads(Path(args.summarize).read_text())["results"]), indent=2))
        return
    if not args.questions or not args.endpoint or args.repeat < 1:
        parser.error("--questions, --endpoint and a positive --repeat are required")
    if any(word in Path(args.questions).name.lower() for word in ("holdout", "frozen")) and not args.measurement:
        parser.error("frozen/holdout sets require --measurement and must never guide fixes")
    questions = [json.loads(line) for line in Path(args.questions).read_text().splitlines() if line.strip()]
    if not questions or len({q["id"] for q in questions}) != len(questions):
        parser.error("questions must be nonempty and have unique ids")
    _, results = asyncio.run(run(args, questions))
    print(json.dumps(summarize(results), indent=2))


if __name__ == "__main__":
    main()
