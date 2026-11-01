"""Step 5: arithmetic in Python, never in the model."""

import re
from collections import defaultdict

from ..schemas import Computation, Fact, Row
from ..sources import TableMeta
from ..text import fmt_number, fmt_value
from .select import Resolved, row_unit

_TOTAL = re.compile(r"^(total|alls|samtals)$", re.I)


def row_label(meta: TableMeta, row: Row, keep: set[str] = frozenset()) -> str:
    """Dimension labels joined, leaving out "Total" unless that dimension is being compared."""
    parts = []
    for var in meta.variables:
        label = row.labels[var.text]
        if var.code in keep or not _TOTAL.match(label):
            parts.append(label)
    return ", ".join(parts) or meta.title


def aggregate_rows(meta: TableMeta, rows: list[Row], dims: list[str], lang: str) -> list[Row]:
    """Rows summed over `dims` (partner countries, say): one row per combination of the other dimensions, labelled
    "Total"/"Samtals" for the summed ones (row_label leaves that out). Missing cells are skipped; a sum with no
    cell at all stays None."""
    out: dict[tuple, Row] = {}
    for r in rows:
        rest = tuple(sorted((k, v) for k, v in r.key.items() if k not in dims))
        if rest not in out:
            key = {k: ("__all__" if k in dims else v) for k, v in r.key.items()}
            labels = {meta.var(k).text if k in dims else lab_k: ("Samtals" if lang == "is" else "Total") if k in dims else lab
                      for (k, _), (lab_k, lab) in zip(r.key.items(), r.labels.items())}
            out[rest] = Row(key=key, labels=labels, value=None)
        if r.value is not None:
            out[rest].value = (out[rest].value or 0) + r.value
    return list(out.values())


def _groups(rows: list[Row], dim: str) -> dict[tuple, dict[str, Row]]:
    groups: dict[tuple, dict[str, Row]] = defaultdict(dict)
    for r in rows:
        rest = tuple(sorted((k, v) for k, v in r.key.items() if k != dim))
        groups[rest][r.key[dim]] = r
    return groups


def compute(meta: TableMeta, rows: list[Row], res: Resolved, lang: str) -> tuple[Computation | None, list[Fact]]:
    rows = [r for r in rows if r.value is not None]
    varying = {c for c, v in res.selections.items() if len(v) > 1}
    # each row in its own unit: a "Percentage of …" option makes the row a percentage whatever the table's unit says
    facts = [Fact(label=row_label(meta, r, varying), value=r.value, unit=row_unit(meta, r.labels),
                  formatted=fmt_value(r.value, row_unit(meta, r.labels), lang)) for r in rows]
    unit = row_unit(meta, rows[0].labels) if rows and len({row_unit(meta, r.labels) for r in rows}) == 1 else meta.unit
    if res.operation in ("value", "series", "rank") or not rows:   # rank: the rows arrive ranked and trimmed
        return None, facts

    dim = res.compare_dimension
    order = meta.var(dim).values
    comp = None
    if res.operation in ("max", "min"):
        # the highest or lowest point of each line, labelled with where it is ("Highest: 2011M05, …")
        word = {("max", "en"): "Highest", ("max", "is"): "Hæst", ("min", "en"): "Lowest", ("min", "is"): "Lægst"}
        for group in _groups(rows, dim).values():
            present = sorted(group, key=order.index)
            best = (max if res.operation == "max" else min)((group[c] for c in present), key=lambda r: r.value)
            first, last = (group[present[i]].labels[meta.var(dim).text] for i in (0, -1))
            facts.append(Fact(label=f"{word[res.operation, lang]}: {row_label(meta, best, {dim})}", value=best.value,
                              unit=unit, formatted=fmt_value(best.value, unit, lang)))
            if comp is None:
                expr = (f"{'hæsta' if res.operation == 'max' else 'lægsta'} af {len(present)} gildum, {first}–{last}"
                        if lang == "is" else f"{'highest' if res.operation == 'max' else 'lowest'} of {len(present)} values, {first}–{last}")
                comp = Computation(op=res.operation, expression=expr, result=best.value, unit=unit)
        return comp, facts

    if res.operation == "sum":
        # "Reykjavík and Kópavogur together": the chosen categories added up, per line of the other dimensions
        for group in _groups(rows, dim).values():
            present = sorted(group, key=order.index)
            if len(present) < 2:
                continue
            total = sum(group[c].value for c in present)
            names = " + ".join(group[c].labels[meta.var(dim).text] for c in present)
            facts.append(Fact(label=f"{'Samtals' if lang == 'is' else 'Total'}: {names}", value=total, unit=unit,
                              formatted=fmt_value(total, unit, lang)))
            if comp is None:
                comp = Computation(op="sum", expression=" + ".join(fmt_number(group[c].value, lang) for c in present),
                                   result=total, unit=unit)
        return comp, facts

    for group in _groups(rows, dim).values():
        if res.operation in ("difference", "pct_change"):
            present = sorted(group, key=order.index)
            if len(present) < 2:
                continue
            if res.order and all(c in group for c in res.order):
                a, b = group[res.order[0]], group[res.order[1]]   # "more men than women": men − women
            else:
                a, b = group[present[-1]], group[present[0]]      # periods: later − earlier
            if res.operation == "difference":
                result, op_unit = a.value - b.value, unit
                expr = f"{fmt_number(a.value, lang)} − {fmt_number(b.value, lang)}"
            else:
                result, op_unit = (a.value - b.value) / b.value * 100, "%"
                expr = f"({fmt_number(a.value, lang)} − {fmt_number(b.value, lang)}) ÷ {fmt_number(b.value, lang)}"
            la, lb = a.labels[meta.var(dim).text], b.labels[meta.var(dim).text]
            pct = " (%)" if res.operation == "pct_change" else ""
            if meta.var(dim).time:   # over time: a change from the earlier period to the later one
                label = f"{'Breyting' if lang == 'is' else 'Change'}{pct} {lb} → {la}"
            else:                    # two groups at one point: a difference, in the order the question asked
                label = f"{'Mismunur' if lang == 'is' else 'Difference'}{pct}: {la} − {lb}"
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
