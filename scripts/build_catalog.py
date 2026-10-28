"""Crawl the PX-API table tree into data/catalog.json. Slow on purpose (rate limits); safe to stop and resume.

    python scripts/build_catalog.py --out data/catalog_live.json [--with-is] [--limit 50]
    python scripts/build_catalog.py --out data/catalog_live.json --add-values   # fill option labels into an existing catalog

Once the kit is released, a loader from its cards/ (pairs.csv) replaces this crawl.
"""

import argparse
import json
import sys
import time
from pathlib import Path

import hashlib

import httpx

BASE = "https://px.hagstofa.is"
CACHE = Path(__file__).resolve().parent.parent / ".cache" / "px_meta"
MAX_VALUES_PER_DIM = 120


class Client:
    def __init__(self, interval: float):
        self.http = httpx.Client(timeout=30)
        self.interval, self.last = interval, 0.0

    def get(self, url: str, cache: bool = False):
        cached = CACHE / f"{hashlib.sha1(url.encode()).hexdigest()}.json"
        if cache and cached.exists():
            return json.loads(cached.read_text())
        for attempt in range(6):
            time.sleep(max(0.0, self.interval - (time.monotonic() - self.last)))
            r = self.http.get(url)
            self.last = time.monotonic()
            if r.status_code == 429:
                wait = float(r.headers.get("Retry-After", 10)) * (attempt + 1)
                print(f"  429, waiting {wait:.0f}s", file=sys.stderr)
                time.sleep(wait)
                continue
            r.raise_for_status()
            if cache:
                CACHE.mkdir(parents=True, exist_ok=True)
                cached.write_text(r.text)
            return r.json()
        raise RuntimeError(f"rate limited too long: {url}")


def walk(c: Client, lang: str, path: str = ""):
    url = f"{BASE}/px{lang}/api/v1/{lang}/{path}".rstrip("/")
    for item in c.get(url):
        if "dbid" in item:  # the root lists databases, not folders
            yield from walk(c, lang, item["dbid"])
            continue
        sub = f"{path}/{item['id']}".strip("/")
        if item["type"] == "l":
            yield from walk(c, lang, sub)
        elif item["type"] == "t":
            yield path, item["id"].removesuffix(".px"), item["text"]


def option_labels(variables: list[dict]) -> list[str]:
    """Non-time option labels, so a search for "Reykjavík" finds a table whose title only says "municipality"."""
    out = []
    for v in variables:
        if not v.get("time"):
            out += [t.strip() for t in v["valueTexts"][:MAX_VALUES_PER_DIM] if t.strip().lower() != "total"]
    return list(dict.fromkeys(out))


def entry(c: Client, path: str, table_id: str, title: str) -> dict:
    meta = c.get(f"{BASE}/pxen/api/v1/en/{path}/{table_id}.px", cache=True)
    variables = meta["variables"]
    time_var = next((v for v in variables if v.get("time")), None)
    return {
        "id": table_id,
        "path": path,
        "title_en": meta.get("title", title),
        "title_is": "",
        "dimensions": [v["text"] for v in variables],
        "values": option_labels(variables),
        "first_period": time_var["values"][0] if time_var else "",
        "last_period": time_var["values"][-1] if time_var else "",
        "unit": "",
    }


def add_values(c: Client, out: Path, done: dict):
    todo = [e for e in done.values() if "values" not in e]
    for i, e in enumerate(todo, 1):
        try:
            fresh = entry(c, e["path"], e["id"], e["title_en"])
        except Exception as ex:
            print(f"skip {e['id']}: {ex}", file=sys.stderr)
            continue
        e["values"] = fresh["values"]
        print(f"{i:5d}/{len(todo)} {e['id']} {len(e['values'])} values")
        if i % 50 == 0:
            out.write_text(json.dumps(list(done.values()), ensure_ascii=False, indent=1))
    out.write_text(json.dumps(list(done.values()), ensure_ascii=False, indent=1))
    print(f"wrote {len(done)} tables to {out}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/catalog_live.json")
    ap.add_argument("--interval", type=float, default=1.0)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--with-is", action="store_true", help="also crawl Icelandic titles (doubles the folder calls)")
    ap.add_argument("--add-values", action="store_true", help="add option labels to entries of an existing catalog")
    args = ap.parse_args()

    out = Path(args.out)
    done = {e["id"]: e for e in json.loads(out.read_text())} if out.exists() else {}
    c = Client(args.interval)
    if args.add_values:
        add_values(c, out, done)
        return
    n = 0
    for path, table_id, title in walk(c, "en"):
        if table_id in done:
            continue
        try:
            done[table_id] = entry(c, path, table_id, title)
        except Exception as e:
            print(f"skip {table_id}: {e}", file=sys.stderr)
            continue
        n += 1
        print(f"{len(done):5d} {table_id} {title[:70]}")
        if n % 20 == 0:
            out.write_text(json.dumps(list(done.values()), ensure_ascii=False, indent=1))
        if args.limit and n >= args.limit:
            break

    if args.with_is:
        for path, table_id, title in walk(c, "is"):
            if table_id in done:
                done[table_id]["title_is"] = title
    out.write_text(json.dumps(list(done.values()), ensure_ascii=False, indent=1))
    print(f"wrote {len(done)} tables to {out}")


if __name__ == "__main__":
    main()
