"""Record real answers from a running server for the static demo (frontend/demo/, served by GitHub Pages).

  python scripts/record_demo.py [--server http://127.0.0.1:8000] [--followups]

Asks every demo question in frontend/demo.js (RAGSTOFAN_POOL) (English and Icelandic) through /api/ask/stream, keeps each streamed
line with the time it arrived, so the demo replays the progress at the speed it really ran. A question that comes back
asking how to show it is asked again with each choice. With --followups, the suggested next questions of the English
answers are asked too, so clicking one in the demo still gets a real answer. Only the answers and the dashboard are
saved: nothing about the server, its address or the model's host.
"""

import argparse
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "frontend/demo"


def post(server: str, path: str, body: dict, timeout=300):
    req = urllib.request.Request(server + path, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    return urllib.request.urlopen(req, timeout=timeout)


def key(question: str, table_id=None, display=None) -> str:
    """The demo looks answers up by this: the question in lower case without its end punctuation, plus any choice."""
    q = re.sub(r"\s+", " ", question.strip().lower()).rstrip("?.! ")
    return "|".join([q, table_id or "", display or ""])


def ask(server: str, question: str, language=None, table_id=None, display=None) -> dict:
    body = {"question": question, "language": language, "table_id": table_id, "display": display, "history": []}
    t0 = time.monotonic()
    lines, answer = [], None
    with post(server, "/api/ask/stream", body) as r:
        for raw in r:
            raw = raw.strip()
            if not raw:
                continue
            msg = json.loads(raw)
            lines.append({"at": round((time.monotonic() - t0) * 1000), **msg})
            if msg["type"] == "answer":
                answer = msg["answer"]
    if answer is None:
        raise RuntimeError("the stream ended without an answer")
    return {"question": question, "table_id": table_id, "display": display, "lines": lines}


def pool() -> list[tuple[str, str]]:
    src = (ROOT / "frontend/demo.js").read_text()
    start = src.index("RAGSTOFAN_POOL = [")
    block = src[start:src.index("];", start)]
    out = []
    for topic, en, is_ in re.findall(r'\["(\w+)", "([^"]+)", "([^"]+)"\]', block):
        out += [(en, "en"), (is_, "is")]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default="http://127.0.0.1:8000")
    ap.add_argument("--followups", action="store_true")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "answers.json"
    store = json.loads(path.read_text()) if path.exists() else {}
    suggest_path = OUT / "suggest.json"
    suggestions = json.loads(suggest_path.read_text()) if suggest_path.exists() else {}

    def record(question, language=None, table_id=None, display=None):
        k = key(question, table_id, display)
        if k in store:
            return store[k]
        try:
            rec = ask(args.server, question, language, table_id, display)
        except Exception as e:
            print(f"  failed: {question}: {e}", file=sys.stderr)
            return None
        store[k] = rec
        a = rec["lines"][-1]["answer"]
        charts = ",".join((p["chart"].get("view") or p["chart"]["type"]) + (f"x{len(p['chart']['data'])}" if p["chart"].get("data") else "")
                          for p in a.get("parts", []) if p.get("chart")) or "-"
        print(f"{a['status']:9} {a['total_ms'] / 1000:5.1f}s  {charts:14} {question}" + (f"  [{display}]" if display else ""))
        if a["status"] == "clarify" and a.get("clarification"):
            for o in a["clarification"]["options"]:
                if o.get("display"):
                    record(question, a["language"], None, o["display"])
        if a["status"] == "answered":
            try:
                with post(args.server, "/api/suggest", a, timeout=120) as r:
                    suggestions[k] = json.load(r)
            except Exception as e:
                print(f"  no suggestions: {e}", file=sys.stderr)
        path.write_text(json.dumps(store, ensure_ascii=False))
        suggest_path.write_text(json.dumps(suggestions, ensure_ascii=False))
        return rec

    with urllib.request.urlopen(args.server + "/api/indicators", timeout=120) as r:
        (OUT / "indicators.json").write_text(json.dumps(json.load(r), ensure_ascii=False))

    for question, language in pool():
        record(question)
    if args.followups:
        for k in [k for k in list(store) if k in suggestions]:
            if store[k]["lines"][-1]["answer"]["language"] != "en":
                continue
            for s in suggestions[k].get("questions", []):
                record(s["question"])
    print(f"{len(store)} answers, {len(suggestions)} suggestion lists -> {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
