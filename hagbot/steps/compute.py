"""Step 5: arithmetic in Python, never in the model."""

import re
from collections import defaultdict

from ..schemas import Computation, Fact, Row
from ..sources import TableMeta
from ..text import fmt_number, fmt_value
from .select import Resolved

_TOTAL = re.compile(r"^(total|alls|samtals)$", re.I)


def row_label(meta: TableMeta, row: Row, keep: set[str] = frozenset()) -> str:
    """Dimension labels joined, leaving out "Total" unless that dimension is being compared."""
    parts = []
    for var in meta.variables:
        label = row.labels[var.text]
        if var.code in keep or not _TOTAL.match(label):
            parts.append(label)
    return ", ".join(parts) or meta.title


def _groups(rows: list[Row], dim: str) -> dict[tuple, dict[str, Row]]:
    groups: dict[tuple, dict[str, Row]] = defaultdict(dict)
    for r in rows:
        rest = tuple(sorted((k, v) for k, v in r.key.items() if k != dim))
        groups[rest][r.key[dim]] = r
    return groups


def compute(meta: TableMeta, rows: list[Row], res: Resolved, lang: str) -> tuple[Computation | None, list[Fact]]:
    unit = meta.unit
    rows = [r for r in rows if r.value is not None]
    varying = {c for c, v in res.selections.items() if len(v) > 1}
    facts = [Fact(label=row_label(meta, r, varying), value=r.value, unit=unit, formatted=fmt_value(r.value, unit, lang))
             for r in rows]
    if res.operation in ("value", "series") or not rows:
        return None, facts

    dim = res.compare_dimension
    order = meta.var(dim).values
    comp = None
    for group in _groups(rows, dim).values():
        if res.operation in ("difference", "pct_change"):
            present = sorted(group, key=order.index)
            if len(present) < 2:
                continue
            a, b = group[present[-1]], group[present[0]]
            if res.operation == "difference":
                result, op_unit = a.value - b.value, unit
                expr = f"{fmt_number(a.value, lang)} − {fmt_number(b.value, lang)}"
            else:
                result, op_unit = (a.value - b.value) / b.value * 100, "%"
                expr = f"({fmt_number(a.value, lang)} − {fmt_number(b.value, lang)}) ÷ {fmt_number(b.value, lang)}"
            label = f"{'Change' if res.operation == 'difference' else 'Change (%)'} " \
                    f"{a.labels[meta.var(dim).text]} vs {b.labels[meta.var(dim).text]}"
        else:
            if res.part not in group or res.whole not in group or not group[res.whole].value:
                continue
            p, w = group[res.part], group[res.whole]
            result, op_unit = p.value / w.value * 100, "%"
            expr = f"{fmt_number(p.value, lang)} ÷ {fmt_number(w.value, lang)}"
            label = f"Share: {row_label(meta, p, {dim})} of {w.labels[meta.var(dim).text]}"
        sign = "+" if res.operation != "share" and result > 0 else ""
        facts.append(Fact(label=label, value=round(result, 4), unit=op_unit,
                          formatted=sign + fmt_value(result, op_unit, lang)))
        if comp is None:
            comp = Computation(op=res.operation, expression=expr, result=round(result, 4), unit=op_unit)
    return comp, facts
