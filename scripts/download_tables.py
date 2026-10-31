"""Download the data of every table listed in a tables_<timestamp>.json file (made by crawler_v2.py).

For each table the data is downloaded once: in Icelandic if the table has an "is" entry,
otherwise in English. Metadata is saved in every language the table has. Each table is
written to downloads/<name>.json (no subfolders; _2, _3, ... is added when a name is already
used by another table) and every attempt is appended to download_log.jsonl.

Usage:
    python download_tables.py                      # all tables; skips tables already logged as "ok"
    python download_tables.py --limit 10           # only the first 10 tables
    python download_tables.py --start 100 --limit 10
    python download_tables.py --tables my_tables.json --redo   # download again even if already "ok"
"""
import argparse
import json
import os
import time
from datetime import datetime

import requests

BASE = {
    "is": "https://px.hagstofa.is/pxis/api/v1/is",
    "en": "https://px.hagstofa.is/pxen/api/v1/en",
}

TABLES = "data/tables_20261030_224153.json"
OUT_DIR = "downloads"
LOG = "download_log.jsonl"

SLEEP = 2                # PxWeb rate-limits (roughly 30 calls / 10 s); stay well under
RATE_LIMIT_ATTEMPTS = 6  # 429 waits: 60, 120, 240, 480, 960 s (or the Retry-After value)


def retry_after(r):
    value = r.headers.get("Retry-After", "")
    return int(value) if value.isdigit() else None


def request(url, body=None):
    """GET (or POST when `body` is given) with the same 429 / retry logic as crawler_v2.get."""
    for attempt in range(RATE_LIMIT_ATTEMPTS):
        try:
            if body is None:
                r = requests.get(url, timeout=30)
            else:
                r = requests.post(url, json=body, timeout=120)
        except requests.RequestException:
            r = None                        # network error: back off and retry
        if r is not None and r.status_code < 400:
            time.sleep(SLEEP)
            return r.json()
        if r is not None and 400 <= r.status_code < 500 and r.status_code != 429:
            r.raise_for_status()            # other 4xx is a real error (e.g. 403 too many cells): don't retry
        if r is not None and r.status_code == 429:
            wait = retry_after(r) or 60 * 2 ** attempt
            print(f"rate limited on {url}, waiting {wait} s")
        else:
            wait = 5 * (attempt + 1)        # server error or network error
        time.sleep(wait)
    raise RuntimeError(f"gave up on {url}")


def full_query(variables):
    """Select every value of every variable."""
    return {
        "query": [{"code": v["code"], "selection": {"filter": "all", "values": ["*"]}} for v in variables],
        "response": {"format": "json-stat2"},
    }

def flat_path(path):
    """downloads/<name>.json, or <name>_2.json, _3, ... if that name is taken by a different table."""
    os.makedirs(OUT_DIR, exist_ok=True)
    name = os.path.basename(path).removesuffix(".px")
    n = 1
    while True:
        out_path = os.path.join(OUT_DIR, f"{name}.json" if n == 1 else f"{name}_{n}.json")
        if not os.path.exists(out_path):
            return out_path
        with open(out_path, encoding="utf-8") as f:
            if json.load(f).get("table") == path:   # same table (e.g. --redo): overwrite it
                return out_path
        n += 1


def download_table(entry):
    """Return (lang, saved file path) for one table, or raise on failure."""
    path = entry["path"]
    langs = [lang for lang in ("is", "en") if entry.get(lang)]
    if not langs:
        raise RuntimeError("table has neither 'is' nor 'en'")

    # Metadata in every available language (same codes, different text); None if it fails
    meta = {}
    for lang in langs:
        try:
            meta[lang] = request(f"{BASE[lang]}/{path}")
        except (RuntimeError, requests.RequestException):
            meta[lang] = None

    # Data once: Icelandic if available, otherwise English
    errors = []
    for lang in langs:
        variables = entry[lang]["variables"] or (meta[lang] or {}).get("variables")
        if not variables:
            errors.append(f"{lang}: no variables")
            continue
        try:
            data = request(f"{BASE[lang]}/{path}", full_query(variables))
        except (RuntimeError, requests.RequestException) as e:
            errors.append(f"{lang}: {e}")
            continue
        if data and "value" in data:
            break
        errors.append(f"{lang}: response has no 'value'")
    else:
        raise RuntimeError("; ".join(errors))

    out = {
        "table": path,
        "data_language": lang,
        "downloaded": datetime.now().isoformat(timespec="seconds"),
        "data": data,
        "metadata": meta,
    }
    out_path = flat_path(path)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    return lang, out_path, len(data["value"])


def already_ok():
    """Paths whose last log entry is 'ok', so a rerun resumes instead of starting over."""
    status = {}
    if os.path.exists(LOG):
        with open(LOG, encoding="utf-8") as f:
            for line in f:
                rec = json.loads(line)
                status[rec["path"]] = rec["status"]
    return {p for p, s in status.items() if s == "ok"}


def log(record):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tables", default=TABLES, help="table list made by crawler_v2.py")
    parser.add_argument("--start", type=int, default=0, help="index of the first table to download")
    parser.add_argument("--limit", type=int, default=None, help="number of tables to download")
    parser.add_argument("--redo", action="store_true", help="download again tables already logged as ok")
    args = parser.parse_args()

    with open(args.tables, encoding="utf-8") as f:
        tables = json.load(f)
    end = None if args.limit is None else args.start + args.limit
    subset = tables[args.start:end]
    done = set() if args.redo else already_ok()

    print(f"{len(subset)} of {len(tables)} tables selected")
    counts = {"ok": 0, "failed": 0, "skipped": 0}
    for i, entry in enumerate(subset, start=args.start):
        path = entry["path"]
        if path in done:
            counts["skipped"] += 1
            continue
        record = {"index": i, "path": path, "time": datetime.now().isoformat(timespec="seconds")}
        try:
            lang, out_path, cells = download_table(entry)
            record.update(status="ok", lang=lang, file=out_path, cells=cells)
            print(f"[{i}] ok ({lang}, {cells} values) {path}")
        except (RuntimeError, requests.RequestException) as e:
            record.update(status="failed", error=str(e))
            print(f"[{i}] FAILED {path}: {e}")
        log(record)
        counts[record["status"]] += 1
    print(f"done: {counts}  (log: {LOG})")


if __name__ == "__main__":
    main()
