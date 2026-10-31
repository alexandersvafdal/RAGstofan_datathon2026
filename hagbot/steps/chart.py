"""Step 6: pick a chart from the shape of the data. Rules, not the model."""

import re

from ..schemas import AnswerPart, ChartPoint, ChartSpec, Computation, Row
from ..sources import TableMeta
from ..text import fmt_period


def choose_chart(meta: TableMeta, rows: list[Row], selections: dict[str, list[str]],
                 computation: Computation | None) -> ChartSpec | None:
    rows = [r for r in rows if r.value is not None]
    if not rows:
        return None
    varying = [v for v in meta.variables if len(selections.get(v.code, [])) > 1]
    if len(rows) == 1:
        if computation:  # the answer card already shows the computed number
            return None
        return ChartSpec(type="kpi", title=meta.title, unit=meta.unit,
                         data=[ChartPoint(x="", y=rows[0].value)])

    if not varying:
        return None
    time_var = next((v for v in varying if v.time), None)
    if time_var and len(selections[time_var.code]) > 2:
        x_var, kind = time_var, "line"
    else:
        x_var, kind = (time_var or varying[0]), "bar"
    series_vars = [v for v in varying if v is not x_var]

    data = []
    for r in rows:
        series = " · ".join(r.labels[v.text] for v in series_vars) or None
        data.append(ChartPoint(x=r.labels[x_var.text], y=r.value, series=series))
    return ChartSpec(type=kind, title=meta.title, x_label=x_var.text, y_label=meta.unit, unit=meta.unit, data=data)


def _short_title(title: str) -> str:
    """ "Wage index from 1989 - by month" -> "Wage index"; "Consumer price index and changes, base 1988=100" ->
    "Consumer price index": a legend name, not the table's full title."""
    t = re.split(r"\s+-\s+|,|\(|\s+(?:from|by|and changes|frá|eftir|og breytingar)\b|\s+\d{4}", title, maxsplit=1)[0]
    return t.strip() or title


def comparison_chart(parts: list[AnswerPart], lang: str) -> ChartSpec | None:
    """One chart for series from different tables ("how have wages and prices developed"). Their levels are not
    comparable (a wage index near 24,000, the CPI near 700) and two y-axes mislead, so each series is set to 100 at
    the first period they all share and the chart shows growth. Rates already in % are drawn as they are."""
    lines = [p for p in parts if p.chart and p.chart.type == "line" and len(p.chart.data) > 2]
    if len({p.table.id for p in lines}) < 2:
        return None
    series: dict[str, dict[str, float]] = {}
    for p in lines:
        name = _short_title(p.table.title)
        for d in p.chart.data:
            key = f"{name} · {d.series}" if d.series and len({x.series for x in p.chart.data}) > 1 else name
            series.setdefault(key, {})[d.x] = d.y
    if len(series) < 2:
        return None
    shared = sorted(set.intersection(*(set(s) for s in series.values())))
    if len(shared) < 3:   # monthly against yearly, or periods that do not overlap
        return None
    rates = all(p.unit == "%" or p.chart.unit == "%" for p in lines)
    base = shared[0]
    if not rates and any(s[base] == 0 for s in series.values()):
        return None
    data = [ChartPoint(x=x, y=round(s[x] if rates else s[x] / s[base] * 100, 2), series=name)
            for name, s in series.items() for x in shared]
    label = lambda x: fmt_period(x, lang)
    if rates:
        title, unit = ("Samanburður" if lang == "is" else "Comparison"), "%"
    else:
        title = f"Þróun, {label(base)} = 100" if lang == "is" else f"Growth compared, {label(base)} = 100"
        unit = ""
    return ChartSpec(type="line", title=title, x_label=lines[0].chart.x_label, unit=unit, data=data)
