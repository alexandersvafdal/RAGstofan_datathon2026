"""Bring the tables that changed since the snapshot up to date, in the snapshot's own format, by full path.

Statistics Iceland lists when each table was last updated (Nidia's table list, data/tables_<stamp>.json). Every table
updated after the snapshot is fetched again from the PX-API: metadata.json (GET) and data.px (POST, all values, PX
format), for each language the snapshot holds, into the table's own folder. Same-named tables in different folders
are separate entries, so each is refreshed where it is. A new file only replaces the old one after the app's own
reader has loaded it; anything that fails keeps the snapshot's copy. Back up data/release first.

    python -m scripts.refresh_tables --dry-run --only VIN00001     # fetch and check, change nothing
    python -m scripts.refresh_tables                               # every table updated since 2026-10-18
"""

import argparse
import csv
import json
import os
import time
from datetime import datetime
from pathlib import Path

import httpx

from hagbot.catalog import Catalog
from hagbot.config import settings
from hagbot.sources import PxTable, _meta_from_api

ROOT = settings.local_data_dir
SLEEP = 1.0   # the PX-API allows about 30 calls in 10 s; stay well under


def call(client: httpx.Client, method: str, url: str, **kw) -> httpx.Response:
    for attempt in range(5):
        try:
            r = client.request(method, url, **kw)
        except httpx.HTTPError:
            r = None
        if r is not None and r.status_code < 400:
            time.sleep(SLEEP)
            return r
        if r is not None and r.status_code not in (429, 500, 502, 503, 504):
            r.raise_for_status()   # too many cells and the like: not worth retrying
        time.sleep(30 * (attempt + 1) if r is not None and r.status_code == 429 else 5 * (attempt + 1))
    raise RuntimeError(f"gave up on {url}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tables", default=max(Path("data").glob("tables_*.json")))
    ap.add_argument("--since", default="2026-10-18", help="refresh tables updated on or after this date")
    ap.add_argument("--only", help="comma-separated table ids")
    ap.add_argument("--dry-run", action="store_true", help="fetch and check, but change nothing")
    args = ap.parse_args()

    listed = json.load(open(args.tables, encoding="utf-8"))
    changed = {}
    for t in listed:
        updated = max((t.get(l) or {}).get("updated") or "" for l in ("is", "en"))
        if updated >= args.since:
            changed[t["path"].removesuffix(".px")] = updated
    only = set(args.only.split(",")) if args.only else None
    rows = list(csv.DictReader(open(ROOT / "catalogue.csv", encoding="utf-8-sig")))
    todo = [r for r in rows if (p := r["api_url"].split("/api/v1/", 1)[1].split("/", 1)[1].removesuffix(".px")) in changed
            and (not only or r["table_code"] in only) and changed[p] > r["last_updated"]]
    print(f"{len(changed)} tables updated since {args.since}; {len(todo)} table-language copies in the snapshot to refresh")

    catalog = Catalog.load(settings.catalog_path)
    log, done = [], 0
    with httpx.Client(timeout=180, headers={"User-Agent": "RAGstofan refresh (Gagnathon 2026)"}) as client:
        for r in todo:
            folder, url, code = ROOT / r["folder"], r["api_url"], r["table_code"]
            rec = {"table": code, "language": r["language"], "folder": r["folder"], "was": r["last_updated"]}
            try:
                meta = call(client, "GET", url).json()
                query = {"query": [{"code": v["code"], "selection": {"filter": "all", "values": ["*"]}}
                                   for v in meta["variables"]], "response": {"format": "px"}}
                px = call(client, "POST", url, json=query).content
                tmp_meta, tmp_px = folder / "metadata.json.new", folder / "data.px.new"
                tmp_meta.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
                tmp_px.write_bytes(px)
                # the app's own reader must load it, or the snapshot's copy stays
                table_meta = _meta_from_api(code, meta, catalog)
                PxTable(tmp_px, table_meta)
                time_var = next((v for v in table_meta.variables if v.time), None)
                rec["latest"] = time_var.values[-1] if time_var else ""
                if args.dry_run:
                    tmp_meta.unlink(); tmp_px.unlink()
                    rec["status"] = "checked"
                else:
                    os.replace(tmp_meta, folder / "metadata.json")
                    os.replace(tmp_px, folder / "data.px")
                    rec["status"] = "refreshed"
                    done += 1
            except Exception as e:
                for f in ("metadata.json.new", "data.px.new"):
                    (folder / f).unlink(missing_ok=True)
                rec["status"], rec["error"] = "kept old", f"{type(e).__name__}: {str(e)[:160]}"
            log.append(rec)
            print(f"{rec['status']:9} {code:10} {r['language']} {rec.get('latest', '')} {rec.get('error', '')}", flush=True)
    if not args.dry_run:
        out = ROOT / f"refresh_{datetime.now():%Y%m%d_%H%M}.json"
        out.write_text(json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"refreshed {done} of {len(todo)}; log {out}")


if __name__ == "__main__":
    main()
