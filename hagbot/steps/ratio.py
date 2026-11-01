"""Per-inhabitant questions: "GDP per capita", "overnight stays per inhabitant", "births per 1,000 inhabitants".

A stored rate comes first: about a hundred tables already publish a per-inhabitant figure ("GDP per capita",
"Vehicles per 1,000 inhabitants", "Litres per inhabitant"). When a table that fits the question has one at the scale
asked, the question is answered from it like any other, with no division.

Otherwise, or when the question names the population date ("per inhabitant on 1 January 2025": published rates
mostly divide by the average population, so they would not match), the question is split in code, not by the model:
the numerator is the question without its "per ..." clause, the denominator is the population on 1 January of the
year asked, of the place asked about (Iceland unless a place is named). The division happens here, in Python.
"""

import re

from ..catalog import STOPWORDS, Catalog, CatalogEntry
from ..schemas import AnswerPart, Computation, Fact
from ..text import fmt_number, fmt_value, normalize, tokens

POPULATION_TABLE = "MAN00101"  # population by sex and age, 1 January, every year since 1841

_YEAR = r"\b(?:18|19|20)\d\d\b"
_NUMBER = r"(\d[\d.,\s]*\d|\d)\s+(?:(thousand|þúsund)\s+)?"
# "per inhabitant", "per capita", "per 1,000 inhabitants", "á hvern íbúa", "á hverja 1.000 íbúa", "á mann",
# with an optional population date: "on 1 January 2025", "1. janúar 2025"
_PER = re.compile(
    r"\s*\b(?:per|á)\s+(?:hvern|hverja|each|every)?\s*(?:" + _NUMBER + r")?"
    r"(?:inhabitants?|capita|persons?|people|residents?|heads?|íbúa|íbúum|mann|manns)\b"
    r"(?P<date>,?\s+(?:on|at|as of|þann|miðað við)?\s*(?:1\.?\s*(?:st\s+)?(?:of\s+)?(?:janúar|january|jan\.?)|"
    r"january\s+1(?:st)?)(?:,?\s+" + _YEAR + r")?)?",
    re.I,
)
# the same idea in a table's title or option labels: "GDP per capita", "Per 1,000 inhabitants", "Litres per inhabitant"
_RATE = re.compile(
    r"\b(?:per|á)\s+(?:hvern|hverja)?\s*(?:" + _NUMBER + r")?"
    r"(?:inhabitants?|inh\b|capita|head|population|pop\b|íbúa|mann)",
    re.I,
)
_NOT_PLACES = {"iceland", "island", "islandi", "islands", "icelandic"}
_TIME_WORDS = {"year", "years", "arid", "arinu", "arin", "ara", "month", "manudi"}  # say when, not what
_MONTH_WORDS = {"january", "februar", "february", "march", "april", "may", "june", "july", "august", "september",
                "october", "november", "december", "januar", "mars", "mai", "juni", "juli", "agust", "oktober", "desember"}


def _scale(m: re.Match) -> int:
    digits = re.sub(r"[^\d]", "", m.group(1) or "")
    return (int(digits) if digits else 1) * (1000 if m.group(2) else 1)


def _places(text: str) -> list[str]:
    """Capitalised words that do not start the sentence and are not Iceland or a month: Reykjavík, Akureyri …"""
    words = re.findall(r"(?<![.?!]\s)(?<!^)\b([A-ZÁÐÉÍÓÚÝÞÆÖ][\wáðéíóúýþæö-]+)", text.strip())
    out = []
    for w in words:
        n = normalize(w)
        if len(n) >= 4 and n not in STOPWORDS and n not in _NOT_PLACES and n not in _MONTH_WORDS:
            out.append(w)
    return list(dict.fromkeys(out))


class RatioAsk:
    def __init__(self, numerator: str, scale: int, year: str | None, clause: str, dated: bool = False,
                 place: str | None = None):
        self.numerator, self.scale, self.year, self.clause = numerator, scale, year, clause
        self.dated = dated  # the question names the population date, so the division must be done here
        self.place = place  # the population is this place's, not Iceland's

    def population_question(self, lang: str) -> str:
        year = self.year or "the latest year"
        return f"How many people lived in {self.place or 'Iceland'} on 1 January {year}?"

    def population_search(self) -> str:
        return f"population {self.place} municipality 1 January" if self.place else "population 1 January"


def detect_ratio(question: str) -> RatioAsk | None:
    m = _PER.search(question)
    if not m:
        return None
    years = re.findall(_YEAR, m.group(0)) or re.findall(_YEAR, question)
    numerator = re.sub(r"\s+", " ", question[: m.start()] + " " + question[m.end():]).strip()
    numerator = re.sub(r"\s+([?.,])", r"\1", numerator)
    if not numerator.endswith("?"):
        numerator = numerator.rstrip(".") + "?"
    places = _places(numerator)
    return RatioAsk(numerator, _scale(m), years[-1] if years else None, m.group(0).strip(),
                    dated=bool(m.group("date")), place=places[0] if places else None)


def rate_scales(entry: CatalogEntry) -> set[int]:
    """The per-inhabitant scales a table publishes: {1} for "per capita", {1000} for "per 1,000 inhabitants"."""
    return {_scale(m) for text in [entry.title_en, entry.title_is, *entry.values] for m in _RATE.finditer(text)}


def _about(text: str, subject: list[str]) -> bool:
    """The text shares a word with the subject (by prefix, so "landsframleiðsla" ~ "landsframleiðslan")."""
    return any(t.startswith(w[:max(3, len(w) - 2)]) for w in subject for t in tokens(text))


def _has_rate(text: str, scale: int) -> bool:
    return any(_scale(m) == scale for m in _RATE.finditer(text))


def stored_rate(catalog: Catalog, ask: RatioAsk, question: str, k: int = 10) -> CatalogEntry | None:
    """A table among the first search hits for the whole question that publishes this rate at this scale, covers the
    year and place asked, and is about the numerator's subject (shares a word with it). The table covering most of the
    subject's words wins; on a tie, one whose title says both ("GDP per capita", "Landsframleiðslan á mann").
    None if the question names the population date: then the division is what is asked."""
    if ask.dated:
        return None
    from .retrieve import narrow  # retrieve imports nothing from here; a late import keeps the steps independent

    subject = [w for w in tokens(ask.numerator)
               if len(w) >= 3 and w.isalpha() and w not in STOPWORDS and w not in _TIME_WORDS]
    place = normalize(ask.place) if ask.place else None
    best, best_score = None, (0, 0)
    for e in narrow([e for e, _ in catalog.search(question, 2 * k)], question)[:k]:
        texts = [e.title_en, e.title_is, *e.values]
        if not any(_has_rate(t, ask.scale) for t in texts) or not any(_about(t, subject) for t in texts):
            continue
        if place and not any(_about(t, [place]) for t in texts):
            continue
        # first: how many of the subject's words the table covers ("public expenditure on health care" beats
        # "real expenditure"); then 2 if one title names the rate and the subject, 1 if an option does, else 0
        covered = sum(any(_about(t, [w]) for t in texts) for w in subject)
        together = 2 if any(_has_rate(t, ask.scale) and _about(t, subject) for t in texts[:2]) else \
            1 if any(_has_rate(t, ask.scale) and _about(t, subject) for t in texts[2:]) else 0
        score = (covered, together)
        if best is None or score > best_score:
            best, best_score = e, score
    return best


def ratio_parts(ask: RatioAsk, num: AnswerPart, den: AnswerPart, lang: str) -> tuple[AnswerPart, AnswerPart] | None:
    """The numerator part with the ratio as its computation and headline, and the population part with a readable
    label. None if either side is not one number."""
    if len(num.facts) != 1 or len(den.facts) != 1 or not den.facts[0].value:
        return None
    a = num.facts[0]
    year = re.search(r"\b(?:18|19|20)\d\d\b", den.facts[0].label)
    where = f" of {ask.place}" if ask.place and lang == "en" else f" í {ask.place}" if ask.place else ""
    b = den.facts[0].model_copy(update={"label": (f"Population{where}, 1 January {year.group(0)}" if lang == "en" else
                                                   f"Mannfjöldi{where} 1. janúar {year.group(0)}") if year else den.facts[0].label})
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


def _compatible_units(first: AnswerPart, second: AnswerPart) -> bool:
    units = [normalize(p.facts[0].unit or p.unit) for p in (first, second)]
    units = ["persons" if unit in ("people", "person", "persons", "inhabitants") else unit for unit in units]
    return bool(units[0]) and units[0] == units[1]


def share_parts(first: AnswerPart, second: AnswerPart, lang: str, *, ordered: bool = False) -> tuple[AnswerPart, AnswerPart] | None:
    """A share the planner split in two ("what percentage of Iceland's population lived in Reykjavík": Reykjavík from
    the municipality table, the country from the population table): the smaller number over the larger, as a
    percentage, on the part that holds the smaller one. None unless each side is one plain number."""
    if any(len(p.facts) != 1 or p.computation for p in (first, second)):
        return None
    if not _compatible_units(first, second):
        return None
    periods = [re.search(r"\b(?:18|19|20)\d\d(?:M\d\d|Q\d)?\b", p.facts[0].label) for p in (first, second)]
    if any(period is None for period in periods) or periods[0].group(0) != periods[1].group(0):
        return None
    a, b = first.facts[0], second.facts[0]
    if not b.value or a.value < 0 or b.value < 0:
        return None
    if not ordered and a.value > b.value:
        first, second, a, b = second, first, b, a
    result = round(a.value / b.value * 100, 4)
    label = f"{'Hlutfall' if lang == 'is' else 'Share'}: {a.label} / {b.label}"
    fact = Fact(label=label, value=result, unit="%", formatted=fmt_value(result, "%", lang))
    return first.model_copy(update={
        "computation": Computation(op="share", expression=f"{a.formatted} ÷ {b.formatted}", result=result, unit="%"),
        "facts": [a, b, fact],
        "chart": None,
    }), second


def difference_parts(first: AnswerPart, second: AnswerPart, lang: str, *, operation: str = "difference",
                     ordered: bool = False) -> tuple[AnswerPart, AnswerPart] | None:
    """A change the planner split in two (the same measure at two years, each a plain number): later minus earlier,
    on the later part. None unless both sides are one number with a year in the label."""
    if any(len(p.facts) != 1 or p.computation for p in (first, second)):
        return None
    if not _compatible_units(first, second) or operation not in ("difference", "pct_change"):
        return None
    ya, yb = (re.search(r"\b(?:18|19|20)\d\d(?:M\d\d|Q\d)?\b", p.facts[0].label) for p in (first, second))
    if not ordered and (not ya or not yb or ya.group(0) == yb.group(0)):
        return None
    if not ordered and ya.group(0) > yb.group(0):
        first, second = second, first
    if ordered:
        first, second = second, first  # below, b is the minuend and a is the base
    a, b = first.facts[0], second.facts[0]
    if operation == "pct_change" and a.value == 0:
        return None
    result = round((b.value - a.value) / a.value * 100 if operation == "pct_change" else b.value - a.value, 4)
    unit = "%" if operation == "pct_change" else b.unit
    sign = "+" if result > 0 else ""
    label = f"{'Breyting' if lang == 'is' else 'Change'} {a.label} → {b.label}"
    fact = Fact(label=label, value=result, unit=unit, formatted=sign + fmt_value(result, unit, lang))
    return second.model_copy(update={
        "computation": Computation(op=operation, expression=(f"({b.formatted} − {a.formatted}) ÷ {a.formatted} × 100"
                                      if operation == "pct_change" else f"{b.formatted} − {a.formatted}"), result=result, unit=unit),
        "facts": [a, b, fact],
        "chart": None,
    }), first
