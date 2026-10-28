"""Step 6: pick a chart from the shape of the data. Rules, not the model."""

from ..schemas import ChartPoint, ChartSpec, Computation, Row
from ..sources import TableMeta


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
