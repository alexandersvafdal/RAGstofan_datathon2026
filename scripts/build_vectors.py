"""Embed every table card of the catalog and save the vectors next to it.

    python -m scripts.build_vectors                                   # the catalog in CATALOG_PATH
    CATALOG_PATH=data/catalog_enriched.json python -m scripts.build_vectors
"""

import json
import time

import httpx

from hagbot.catalog import CatalogEntry
from hagbot.config import settings
from hagbot.vectors import Vectors, card_text, embed

BATCH = 32

entries = [CatalogEntry(**x) for x in json.loads(settings.catalog_path.read_text())]
client, vectors, t0 = httpx.Client(timeout=300), [], time.perf_counter()
for i in range(0, len(entries), BATCH):
    vectors += embed([card_text(e) for e in entries[i:i + BATCH]], client)
    print(f"{len(vectors)}/{len(entries)}", end="\r", flush=True)
Vectors.save(settings.catalog_path, [e.id for e in entries], vectors)
print(f"\n{len(vectors)} vectors from {settings.embed_model} in {time.perf_counter() - t0:.0f}s, "
      f"saved beside {settings.catalog_path}")
