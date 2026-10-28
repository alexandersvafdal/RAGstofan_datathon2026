"""Step 2: find the table. Keyword search gives candidates, the model picks one or asks back."""

from typing import Literal

from pydantic import BaseModel, Field

from ..catalog import Catalog, CatalogEntry
from ..llm import LLM


class Pick(BaseModel):
    table_id: str = Field(description="ID of the best table, copied exactly from the list")
    confidence: Literal["high", "low"] = Field(
        description="low only if two or more tables fit equally well and the question does not say which"
    )
    alternatives: list[str] = Field(default_factory=list, description="IDs of other tables that fit equally well")


class Retrieved(BaseModel):
    table: CatalogEntry
    alternatives: list[CatalogEntry] = []
    ambiguous: bool = False
    candidates: list[str] = []


SYSTEM = """You choose which Statistics Iceland table answers a question.
Rules:
- "How many people live in X" means population on 1 January, current municipality division, unless the question says otherwise.
- Prefer the table whose period covers the year asked about; with no year, prefer the most recent data.
- Prefer the most direct table over one that needs extra breakdowns.
- Prefer original (unadjusted) figures unless the question asks for seasonally adjusted or trend figures.
- Prefer monthly tables when a month is asked, yearly tables when only a year is asked.
- A place or category the question names should be among the table's options ("Has options").
Copy the table ID exactly."""


async def retrieve(llm: LLM, catalog: Catalog, question: str, search_query: str, k: int,
                   forced_table: str | None = None, original: str = "") -> Retrieved:
    if forced_table and (entry := catalog.get(forced_table)):
        return Retrieved(table=entry, candidates=[entry.id])
    # the original wording matches Icelandic titles even when the planner mistranslates it
    hits = catalog.search(f"{search_query} {question} {original}", k)
    if not hits:
        raise LookupError(f"No table matches “{question}”")
    entries = [e for e, _ in hits]
    if len(entries) == 1:
        return Retrieved(table=entries[0], candidates=[entries[0].id])

    cards = "\n".join(f"- {e.card(f'{question} {original}')}" for e in entries)
    asked = f"User asked: {original}\nThis part: {question}" if original and original != question else f"Question: {question}"
    pick = await llm.complete_json(SYSTEM, f"{asked}\n\nTables:\n{cards}", Pick)
    by_id = {e.id: e for e in entries}
    table = by_id.get(pick.table_id.strip(), entries[0])  # an invented ID falls back to the top search hit
    alts = [by_id[a] for a in pick.alternatives if a in by_id and a != table.id]
    return Retrieved(
        table=table,
        alternatives=alts,
        ambiguous=pick.confidence == "low" and bool(alts),
        candidates=list(by_id),
    )
