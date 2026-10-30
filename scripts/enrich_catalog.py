"""Write a plain description and everyday search words for every table, so the search finds tables by the words
people use ("immigrants", "inflation", "how many people live") and not only by Statistics Iceland's titles.

    python -m scripts.enrich_catalog                      # resumes: tables already done are skipped
    python -m scripts.enrich_catalog --limit 20           # try a few first
    python -m scripts.enrich_catalog --merge              # write data/catalog_enriched.json from the results

English only: qwen2.5:14b writes poor Icelandic search words, and the Icelandic titles are already searched.
The prompt is the same for every table and names no table or topic, so the words come from what each table holds,
not from the questions we evaluate on.
"""

import argparse
import asyncio
import json
import time
from pathlib import Path

from pydantic import BaseModel, Field

from hagbot.catalog import CatalogEntry
from hagbot.config import settings
from hagbot.llm import LLM, LLMError

OUT = Path("data/enrichment.jsonl")
MERGED = Path("data/catalog_enriched.json")
SAMPLE_VALUES = 25


class Enrichment(BaseModel):
    about_en: str = Field(description="one plain English sentence: what is counted or measured, in what unit, broken down by what")
    keywords_en: list[str] = Field(description="8-12 English words or short phrases a person might use when asking for this "
                                   "data: everyday synonyms of the measure and of its main categories")


SYSTEM = """You describe one table from Statistics Iceland so that a search engine can find it from everyday questions.
Use only what the title, dimensions and categories show; do not invent figures, years or categories.
- about: say what the numbers are (a count of people, an index, a rate in %, an amount in ISK, nights, ...), and what
  they are broken down by. Mention the time step (monthly, quarterly, yearly) when the dimensions show it.
- keywords: the words a non-expert would type. Include synonyms of the measure (e.g. for "dwellings": housing, homes,
  apartments) and the commonly asked categories. No years, no table codes."""


def prompt(e: CatalogEntry) -> str:
    vals = ", ".join(e.values[:SAMPLE_VALUES]) + (f", ... ({len(e.values)} in all)" if len(e.values) > SAMPLE_VALUES else "")
    period = f"{e.first_period} to {e.last_period}" if e.first_period else "no time dimension"
    return (f"Title (English): {e.title_en}\nTitle (Icelandic): {e.title_is}\nFolder: {e.path}\n"
            f"Dimensions: {', '.join(e.dimensions)}\nCategories: {vals}\nPeriod: {period}")


async def enrich(limit: int | None) -> None:
    entries = [CatalogEntry(**x) for x in json.loads(settings.catalog_path.read_text())]
    done = {json.loads(line)["id"] for line in OUT.read_text().splitlines()} if OUT.exists() else set()
    todo = [e for e in entries if e.id not in done][:limit]
    print(f"{len(done)} done, {len(todo)} to go, model {settings.llm_model}", flush=True)
    llm, t0 = LLM(), time.perf_counter()
    try:
        with OUT.open("a", encoding="utf-8") as f:
            for n, e in enumerate(todo, 1):
                try:
                    r = await llm.complete_json(SYSTEM, prompt(e), Enrichment)
                except LLMError as err:
                    print(f"  {e.id}: {err}", flush=True)
                    continue
                f.write(json.dumps({"id": e.id, **r.model_dump()}, ensure_ascii=False) + "\n")
                f.flush()
                if n % 25 == 0 or n == len(todo):
                    rate = (time.perf_counter() - t0) / n
                    print(f"{n}/{len(todo)}  {rate:.1f}s each, ~{rate * (len(todo) - n) / 60:.0f} min left", flush=True)
    finally:
        await llm.aclose()


def merge() -> None:
    extra = {}
    for line in OUT.read_text().splitlines():
        x = json.loads(line)
        extra[x.pop("id")] = x
    entries = json.loads(settings.catalog_path.read_text())
    for e in entries:
        e.update(extra.get(e["id"], {}))
    MERGED.write_text(json.dumps(entries, ensure_ascii=False, indent=0))
    print(f"{MERGED}: {len(entries)} tables, {sum(e['id'] in extra for e in entries)} enriched")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int)
    ap.add_argument("--merge", action="store_true")
    a = ap.parse_args()
    merge() if a.merge else asyncio.run(enrich(a.limit))
