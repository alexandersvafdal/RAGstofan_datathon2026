"""Build source-backed discovery profiles before serving/evaluation; no LLM calls.

python -m scripts.build_source_profiles --source local
python -m scripts.build_source_profiles --source live --ids VIN00001,MAN02005
"""

import argparse
import asyncio
import json
from pathlib import Path

from hagbot.catalog import Catalog, CatalogEntry
from hagbot.config import settings
from hagbot.profiles import load_profiles, save_profiles
from hagbot.sources import LocalPxSource, PxApiSource


async def build(args):
    catalog = Catalog([CatalogEntry(**row) for row in json.loads(Path(args.catalog).read_text())], dense=[])
    source = LocalPxSource(catalog, lang=args.language, freshness_policy="snapshot") if args.source == "local" else PxApiSource(catalog, args.language)
    path = Path(args.output)
    profiles = {f"{lang}:{table}": meta for lang in ("en", "is") for table, meta in load_profiles(path, lang).items()}
    ids = args.ids.split(",") if args.ids else [e.id for e in catalog.entries]
    failures = []
    try:
        for table in ids:
            try:
                meta = await (source.warm_definitions(table) if args.source == "live" else source.meta(table))
                profiles[f"{args.language}:{table}"] = meta
            except Exception as error:
                failures.append({"table": table, "error": str(error)})
        save_profiles(path, profiles)
    finally:
        await (source.live.client if isinstance(source, LocalPxSource) else source.client).aclose()
        catalog._pool.shutdown()
    print(json.dumps({"saved": len(ids) - len(failures), "failed": failures, "output": str(path)}, ensure_ascii=False))
    return bool(failures)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=["local", "live"], default="local")
    parser.add_argument("--catalog", default=str(settings.catalog_path))
    parser.add_argument("--output", default=str(settings.profiles_path))
    parser.add_argument("--language", choices=["en", "is"], default="en")
    parser.add_argument("--ids")
    raise SystemExit(asyncio.run(build(parser.parse_args())))


if __name__ == "__main__":
    main()
