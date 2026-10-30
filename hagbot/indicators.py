"""Headline indicators for the dashboard: fixed table cells read straight from the data source, no model involved.

Each indicator names its table and the category codes of every non-time dimension; the time dimension supplies the
series. Values come back in milliseconds from the local snapshot, so the dashboard loads instantly. A period whose
cell is still empty ("2026" before the year ends) is skipped, so `period` is always the latest period with a number.
"""

import asyncio

from pydantic import BaseModel

from .config import settings
from .schemas import ChartPoint
from .sources import DataSource, SourceError, table_url


class Indicator(BaseModel):
    id: str
    label: dict[str, str]  # language -> label
    unit: dict[str, str]
    table: str
    selection: dict[str, str]  # non-time dimension code -> category code
    history: int  # how many periods the sparkline shows
    question: dict[str, str]  # what clicking the tile asks the bot


INDICATORS = [
    Indicator(id="population", label={"en": "Population, 1 January", "is": "Mannfjöldi 1. janúar"},
              unit={"en": "people", "is": "manns"}, table="MAN00101", selection={"Kyn": "Total", "Aldur": "-1"},
              history=15, question={"en": "How many people live in Iceland?", "is": "Hve margir búa á Íslandi?"}),
    Indicator(id="inflation", label={"en": "Inflation, 12 months", "is": "Verðbólga, 12 mánuðir"},
              unit={"en": "%", "is": "%"}, table="VIS01000", selection={"Index": "CPI", "Item": "change_A"},
              history=24, question={"en": "What is the annual inflation rate?", "is": "Hver er verðbólgan?"}),
    Indicator(id="unemployment", label={"en": "Unemployment rate", "is": "Atvinnuleysi"},
              unit={"en": "%", "is": "%"}, table="VIN00001", selection={"Kyn/aldur": "0", "Eining": "7"},
              history=24, question={"en": "What is the unemployment rate?", "is": "Hvert er atvinnuleysið?"}),
    Indicator(id="wages", label={"en": "Wage index, 12-month change", "is": "Launavísitala, 12 mánaða breyting"},
              unit={"en": "%", "is": "%"}, table="LAU04000", selection={"Eining": "change_A"},
              history=24, question={"en": "How much have wages risen in the last 12 months?",
                                    "is": "Hve mikið hafa laun hækkað síðustu 12 mánuði?"}),
    Indicator(id="overnight_stays", label={"en": "Overnight stays, registered accommodation",
                                           "is": "Gistinætur á skráðum gististöðum"},
              unit={"en": "nights", "is": "nætur"}, table="SAM01601",
              selection={"Þjóðerni": "Total", "Landshluti": "IS", "Eining": "0", "Mánuður": "0"},
              history=12, question={"en": "How many overnight stays were there in registered accommodation?",
                                    "is": "Hve margar gistinætur voru á skráðum gististöðum?"}),
    Indicator(id="gdp_growth", label={"en": "GDP growth", "is": "Hagvöxtur"},
              unit={"en": "%", "is": "%"}, table="THJ01000", selection={"Skipting": "3"},
              history=15, question={"en": "What was Iceland's economic growth last year?",
                                    "is": "Hver var hagvöxturinn á síðasta ári?"}),
]


class IndicatorValue(BaseModel):
    id: str
    label: dict[str, str]
    unit: dict[str, str]
    value: float
    period: str
    previous: float | None = None  # the period before, for a change arrow
    previous_period: str | None = None
    series: list[ChartPoint]
    table: dict[str, str]  # id, title, url
    question: dict[str, str]


class Indicators(BaseModel):
    data_as_of: str  # "2026-10-18 snapshot" or "live"
    indicators: list[IndicatorValue]
    errors: list[str] = []


async def _one(source: DataSource, ind: Indicator) -> IndicatorValue:
    meta = await source.meta(ind.table)
    time_var = next(v for v in meta.variables if v.time)
    rows = await source.query(ind.table, {**{k: [v] for k, v in ind.selection.items()}, time_var.code: time_var.values})
    points = sorted(((r.key[time_var.code], r.value) for r in rows if r.value is not None),
                    key=lambda p: time_var.values.index(p[0]))
    if not points:
        raise SourceError(f"{ind.id}: no numbers in {ind.table}")
    recent = points[-ind.history:]
    prev = points[-2] if len(points) > 1 else (None, None)
    return IndicatorValue(
        id=ind.id, label=ind.label, unit=ind.unit, value=recent[-1][1], period=recent[-1][0],
        previous=prev[1], previous_period=prev[0], series=[ChartPoint(x=x, y=y) for x, y in recent],
        table={"id": meta.id, "title": meta.title, "url": table_url(meta.path, meta.id)}, question=ind.question)


_cache: Indicators | None = None
_lock = asyncio.Lock()


async def indicators(source: DataSource) -> Indicators:
    """Computed once per server start: the snapshot does not change, and the live API is cached on disk anyway."""
    global _cache
    async with _lock:
        if _cache is None:
            results = await asyncio.gather(*(_one(source, i) for i in INDICATORS), return_exceptions=True)
            _cache = Indicators(
                data_as_of="2026-10-18 snapshot" if settings.data_source == "local" else settings.data_source,
                indicators=[r for r in results if isinstance(r, IndicatorValue)],
                errors=[str(r) for r in results if isinstance(r, Exception)],
            )
        return _cache
