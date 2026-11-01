"""Step 5: arithmetic in Python, never in the model."""

import re
from collections import defaultdict
from math import isfinite, prod

from ..outcomes import AnswerFailure
from ..schemas import Computation, Fact, Row
from ..sources import TableMeta
from ..text import fmt_number, fmt_value
from .select import Resolved

_TOTAL = re.compile(r"^(total|alls|samtals)$", re.I)


def validate_rows(meta: TableMeta, rows: list[Row], selections: dict[str, list[str]]) -> None:
    """A missing/suppressed operand cannot silently disappear from an answer or calculation."""
    if not rows or any(r.value is None for r in rows):
        raise AnswerFailure("unavailable", "missing_data", meta.id)
    keys = set()
    for row in rows:
        if set(row.key) != set(selections) or any(v not in selections[c] for c, v in row.key.items()):
            raise AnswerFailure("unverified", "unexpected_rows", meta.id)
        key = tuple(sorted(row.key.items()))
        if key in keys or not isfinite(row.value):
            raise AnswerFailure("unverified", "invalid_rows", meta.id)
        keys.add(key)
    if len(keys) != prod(len(values) for values in selections.values()):
        raise AnswerFailure("unavailable", "incomplete_data", meta.id)


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
    validate_rows(meta, rows, res.selections)
    unit = meta.unit
    rows = [r for r in rows if r.value is not None]
    varying = {c for c, v in res.selections.items() if len(v) > 1}
    facts = [Fact(label=row_label(meta, r, varying), value=r.value, unit=unit, formatted=fmt_value(r.value, unit, lang))
             for r in rows]
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
                if b.value == 0:
                    raise AnswerFailure("unavailable", "undefined_calculation", "percentage change from zero")
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
                raise AnswerFailure("unavailable", "undefined_calculation", "missing or zero share denominator")
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
