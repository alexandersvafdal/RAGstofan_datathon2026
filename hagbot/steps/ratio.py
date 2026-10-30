"""Ratios across two tables: "overnight stays per inhabitant", "births per 1,000 inhabitants".

The question is split in code, not by the model: the numerator is the question without its "per ..." clause, the
denominator is Iceland's population on 1 January of the year asked, read from the population table directly.
The division happens here, in Python.
"""

import re

from ..schemas import AnswerPart, Computation, Fact
from ..text import fmt_number

POPULATION_TABLE = "MAN00101"  # population by sex and age, 1 January, every year since 1841

# "per inhabitant", "per capita", "per 1,000 inhabitants", "á hvern íbúa", "á hverja 1.000 íbúa", "á mann"
_PER = re.compile(
    r"\s*\b(?:per|á)\s+(?:hvern|hverja|each|every)?\s*(?:(\d[\d.,\s]*\d|\d)\s+)?"
    r"(?:inhabitants?|capita|persons?|people|residents?|heads?|íbúa|íbúum|mann|manns)\b[^?]*",
    re.I,
)


class RatioAsk:
    def __init__(self, numerator: str, scale: int, year: str | None, clause: str):
        self.numerator, self.scale, self.year, self.clause = numerator, scale, year, clause

    def population_question(self, lang: str) -> str:
        year = self.year or "the latest year"
        return f"How many people lived in Iceland on 1 January {year}?"


def detect_ratio(question: str) -> RatioAsk | None:
    m = _PER.search(question)
    if not m:
        return None
    digits = re.sub(r"[^\d]", "", m.group(1) or "")
    scale = int(digits) if digits else 1
    years = re.findall(r"\b(?:18|19|20)\d\d\b", m.group(0)) or re.findall(r"\b(?:18|19|20)\d\d\b", question)
    numerator = (question[: m.start()] + question[m.end():]).strip()
    if not numerator.endswith("?"):
        numerator = numerator.rstrip(".") + "?"
    return RatioAsk(numerator, scale, years[-1] if years else None, m.group(0).strip())


def ratio_parts(ask: RatioAsk, num: AnswerPart, den: AnswerPart, lang: str) -> tuple[AnswerPart, AnswerPart] | None:
    """The numerator part with the ratio as its computation and headline, and the population part with a readable
    label. None if either side is not one number."""
    if len(num.facts) != 1 or len(den.facts) != 1 or not den.facts[0].value:
        return None
    a = num.facts[0]
    year = re.search(r"\b(?:18|19|20)\d\d\b", den.facts[0].label)
    b = den.facts[0].model_copy(update={"label": (f"Population, 1 January {year.group(0)}" if lang == "en" else
                                                   f"Mannfjöldi 1. janúar {year.group(0)}") if year else den.facts[0].label})
    result = a.value / b.value * ask.scale
    per = (f"per {fmt_number(ask.scale, lang)} inhabitants" if ask.scale > 1 else "per inhabitant") if lang == "en" else \
          (f"á hverja {fmt_number(ask.scale, lang)} íbúa" if ask.scale > 1 else "á hvern íbúa")
    times = f" × {fmt_number(ask.scale, lang)}" if ask.scale > 1 else ""
    formatted = fmt_number(result, lang, 1 if result < 1000 else 0)
    fact = Fact(label=f"{a.label}, {per}", value=result, unit="", formatted=formatted)
    return num.model_copy(update={
        "computation": Computation(op="ratio", expression=f"{a.formatted} / {b.formatted}{times}", result=result, unit=""),
        "facts": [a, b, fact],
        "chart": None,
    }), den.model_copy(update={"facts": [b]})
