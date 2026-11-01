"""Two parts the planner asked for, combined into one number in code: a figure per inhabitant (one part divided by
the population part), a share, or a change. The model never does the arithmetic.

Whether a question is per inhabitant is the planner's call (Plan.per_inhabitant): it either keeps one part, when a
table is likely to publish the rate itself ("GDP per capita"), or asks for the figure and the population as two parts.
"""

import re

from ..schemas import AnswerPart, Computation, Fact
from ..text import fmt_number, fmt_value


def divide_parts(num: AnswerPart, den: AnswerPart, scale: int, lang: str) -> tuple[AnswerPart, AnswerPart] | None:
    """The first part divided by the second (the population), times the scale (1 per inhabitant, 1,000 per 1,000
    inhabitants), as the first part's computation and headline. None unless each side is one number."""
    if len(num.facts) != 1 or len(den.facts) != 1 or not den.facts[0].value:
        return None
    a, b = num.facts[0], den.facts[0]
    scale = scale if scale > 0 else 1
    result = a.value / b.value * scale
    per = (f"per {fmt_number(scale, lang)} inhabitants" if scale > 1 else "per inhabitant") if lang == "en" else \
          (f"á hverja {fmt_number(scale, lang)} íbúa" if scale > 1 else "á hvern íbúa")
    times = f" × {fmt_number(scale, lang)}" if scale > 1 else ""
    fact = Fact(label=f"{a.label}, {per}", value=result, unit="", formatted=fmt_number(result, lang, 1 if result < 1000 else 0))
    return num.model_copy(update={
        "computation": Computation(op="ratio", expression=f"{a.formatted} / {b.formatted}{times}", result=result, unit=""),
        "facts": [a, b, fact],
        "chart": None,
    }), den


def share_parts(first: AnswerPart, second: AnswerPart, lang: str) -> tuple[AnswerPart, AnswerPart] | None:
    """A share the planner split in two ("what percentage of Iceland's population lived in Reykjavík": Reykjavík from
    the municipality table, the country from the population table): the smaller number over the larger, as a
    percentage, on the part that holds the smaller one. None unless each side is one plain number."""
    if any(len(p.facts) != 1 or p.computation for p in (first, second)):
        return None
    a, b = first.facts[0], second.facts[0]
    if not a.value or not b.value or a.value < 0 or b.value < 0:
        return None
    if a.value > b.value:
        first, second, a, b = second, first, b, a
    result = round(a.value / b.value * 100, 4)
    label = f"{'Hlutfall' if lang == 'is' else 'Share'}: {a.label} / {b.label}"
    fact = Fact(label=label, value=result, unit="%", formatted=fmt_value(result, "%", lang))
    return first.model_copy(update={
        "computation": Computation(op="share", expression=f"{a.formatted} ÷ {b.formatted}", result=result, unit="%"),
        "facts": [a, b, fact],
        "chart": None,
    }), second


def difference_parts(first: AnswerPart, second: AnswerPart, lang: str) -> tuple[AnswerPart, AnswerPart] | None:
    """A change the planner split in two (the same measure at two years, each a plain number): later minus earlier,
    on the later part. None unless both sides are one number with a year in the label."""
    if any(len(p.facts) != 1 or p.computation for p in (first, second)):
        return None
    ya, yb = (re.search(r"\b(?:18|19|20)\d\d(?:M\d\d|Q\d)?\b", p.facts[0].label) for p in (first, second))
    if not ya or not yb or ya.group(0) == yb.group(0):
        return None
    if ya.group(0) > yb.group(0):
        first, second = second, first
    a, b = first.facts[0], second.facts[0]
    result = round(b.value - a.value, 4)
    sign = "+" if result > 0 else ""
    label = f"{'Breyting' if lang == 'is' else 'Change'} {a.label} → {b.label}"
    fact = Fact(label=label, value=result, unit=b.unit, formatted=sign + fmt_value(result, b.unit, lang))
    return second.model_copy(update={
        "computation": Computation(op="difference", expression=f"{b.formatted} − {a.formatted}", result=result, unit=b.unit),
        "facts": [a, b, fact],
        "chart": None,
    }), first
