"""Step 3: choose the dimension values (filters) and the calculation. Codes are validated in code, never trusted."""

import re
from typing import Literal

from pydantic import BaseModel, Field

from ..catalog import SYNONYMS, _same_word
from ..llm import LLM
from ..vote import ask_all, majority, voting_on
from ..sources import TableMeta, Variable
from ..hierarchy import children, coded, named
from ..text import asked_bases, base_year, normalize, tokens

Op = Literal["value", "series", "difference", "pct_change", "share", "max", "min"]


class Selection(BaseModel):
    selections: dict[str, list[str]] = Field(description="dimension code -> list of value codes")
    operation: Op = Field(description=(
        "value: report the number(s). series: show a trend or comparison over many values. "
        "difference / pct_change: change between the first and last selected value of compare_dimension. "
        "share: part divided by whole within compare_dimension. "
        "max / min: the highest or lowest value along compare_dimension (\"when was it highest\")."))
    compare_dimension: str | None = Field(default=None, description="dimension code the operation runs along")
    part: str | None = Field(default=None, description="value code of the part, for share")
    whole: str | None = Field(default=None, description="value code of the whole (usually the total), for share")


class Resolved(BaseModel):
    selections: dict[str, list[str]]
    operation: Op | Literal["rank", "sum"]  # set in code: rank lists a coded dimension's categories, sum adds the chosen ones
    compare_dimension: str | None = None
    part: str | None = None
    whole: str | None = None
    order: list[str] | None = None  # for a difference between categories: [first, second], result = first − second
    notes: list[str] = []
    window_note: dict[str, str] | None = None  # en/is, when the period asked for starts before the table does
    breakdown: str | None = None  # rank: the coded dimension listed, so its categories can be opened further
    top: int | None = None        # rank: keep the n largest (by size, either sign); None lists them all
    beyond: str | None = None     # a period asked for that lies after this table's last one ("2026M09" in a table to 2026M08)
    place_note: dict[str, str] | None = None  # en/is, when a place the question names is not among the table's options
    missing_period: str = ""                  # the year the question names that the table's periods do not reach
    partial: str = ""   # a dimension the question names as a whole ("greenhouse gas") that this table has only in
                         # parts, with no total: the answer would be one part (CO2) for the whole
    aggregate_over: list[str] = []            # dimensions summed in code: a long list with no total (253 partner
                                              # countries) when the question names none of them
    alternatives: dict[str, list[str]] = {}   # dimension -> categories the model chose that the question did not name,
                                              # answered with the default instead and offered to the user as choices


def _mention_order(var: Variable, codes: list[str], question: str) -> list[str] | None:
    """The order the question names these categories in ("more men than women" -> Males, Females), or None.
    Question words go through the search synonyms, so "karlar" and "men" both find "Males"."""
    words = [w for t in tokens(question) for w in SYNONYMS.get(t, t).split()]
    def first_mention(code: str) -> int | None:
        label = [t for t in tokens(var.label_of(code)) if len(t) >= 3]
        hits = [i for i, w in enumerate(words) for t in label if w == t or (len(w) >= 4 and len(t) >= 4 and (w.startswith(t) or t.startswith(w)))]
        return min(hits) if hits else None
    pos = {c: first_mention(c) for c in codes}
    if any(v is None for v in pos.values()) or len(set(pos.values())) < len(codes):
        return None
    return sorted(codes, key=pos.get)


SYSTEM = """You fill in a query against one Statistics Iceland table.
For every dimension choose the value codes the question needs, copied exactly from the options.
- If the question does not mention a dimension, choose its total (e.g. "Total", "Alls") or, for time, the latest period.
- For "how much did X grow from A to B" select both periods and use difference or pct_change along the time dimension.
- For "what share of ... is X" select X and the total, and use share with part=X and whole=total.
- For trends ("how has ... changed", "over the years") select a range of periods and use series.
- For "when was X highest / lowest" select the periods (or categories) to search and use max or min.
- Inflation means the annual % change of the consumer price index. If the table has an option that already is the
  change asked for ("Annual change, %", "volume change on previous year"), select it with operation value.
- A dimension of measures (tonnes or kg against a value in ISK, a count against an index): "how much / how many of a
  good" with no money word takes the quantity (tonnes, kg, number); "value", "worth", "verðmæti", "fyrir hve mikið"
  takes the value measure.
Never compute anything yourself."""

_INTENT = [  # cheap keyword check; overrides a "value" answer when the question clearly asks for a calculation
    ("share", re.compile(r"(share|proportion|what part|percentage of|percent of|hlutfall|hluti)", re.I)),
    ("pct_change", re.compile(r"(percent|%|prosent)", re.I)),
    ("difference", re.compile(r"(grow|grew|increase|decrease|declin|change|differ|\brise|\brose|risen|\bfell|\bfall|\bdrop"
                              r"|fjolg|faekk|breyt|aukn|munur|haekk|laekk)", re.I)),
    ("sum", re.compile(r"(together|combined|altogether|in total|samtals|samanlagt|til samans)", re.I)),
]
_ANNUAL_ASK = r"12 month|twelve month|\bannual|year on year|inflation|verdbolg|\bgrowth|hagvoxt|arsbreyt|tolf manud"
_CHANGE_OPTION = r"annual change|12.month|change on previous year|change from previous year|arsbreyting|fra fyrra ari"
# "How has X changed", "since 2020", "over the years", "þróun": a trend, shown as a series, not one difference
_TREND = (r"how (has|have|did) .{0,60}(chang|develop|evolv|grow|gone)|\bsince (18|19|20)\d\d\b|over the (last |past )?"
          r"(years|decade|months)|over time|\btrend|\bdevelopment|hvernig (hefur|hafa) .{0,60}(throast|breyst|thrast)|"
          r"\bthroun|\bthroast|fra (18|19|20)\d\d(?! til)|undanfarin|sidustu (ar|aratug)")
_TREND_DEFAULT = {"M": 60, "Q": 20, "": 15}  # periods shown when the question names no start
# "the last 15 years", "síðustu 50 árum", "past 6 months", "last decade": a window ending at the latest period
_WINDOW = re.compile(r"\b(?:last|past|previous|sidustu|seinustu|sidastlidin\w*|undanfarin\w*)\s+(\d{1,3})\s+"
                     r"(years?|ar|arum|arin|ara|months?|manud\w*|quarters?|arsfjord\w*)\b")
_DECADE = re.compile(r"\b(?:last|past|previous)\s+decade|\b(?:sidasta|sidustu|seinasta)\s+aratug")
_EXTREME = [("max", re.compile(r"\b(highest|peak\w*|maximum|haest\w*|hamark\w*)\b")),
            ("min", re.compile(r"\b(lowest|minimum|laegst\w*|lagmark\w*)\b"))]
# the question asks when, so the extreme is searched over time ("which occupation earns the most" is not)
_WHEN = r"\bwhen\b|which (year|month|period)|what (year|month)|hvenaer|hvada (ar|manud)|\bever\b|fra upphafi|all time"
_TOTAL = re.compile(r"\b(total|alls|samtals|all)\b", re.I)
# A dimension of measures: "how much fish was exported" is tonnes, "the value of fish exports" is ISK. The question's
# wording decides between a quantity option and a value option when the table has both.
_HOW_MUCH = re.compile(r"\b(how much|how many|hve miki|hversu miki|hve mikl|hversu mikl|hve mor|hversu mor|hvad mor|hvad miki)")
_MONEY = re.compile(r"\b(value|worth|price|revenue|isk|kr|kronu|verd|verdmaet|fob|cif|upphaed|tekjur|million|billion|milljon|milljard)\w*")
_QUANTITY_OPTION = re.compile(r"\b(tonn|tons|kg|kilo|magn|quantity|number|fjoldi|stk|pieces|litr|liter)\w*")
_VALUE_OPTION = re.compile(r"\b(value|verd|fob|cif|isk|kr|kronu|price)\w*")
# "Hverjir eru undirflokkar …", "what are the categories of …": list a dimension's categories
_LIST = (r"undirflokk|undirvisitol|\bflokk(ar|um|a|i)\b|skipting|sundurlid|samsetning|categor|sub-?ind|breakdown|"
         r"\bcomponents?\b|consist|made up of|split (by|into)|broken down")
# "helstu áhrifaþættir", "the biggest industries", "top 5": the largest few, ranked
_TOP = (r"\b(helstu|staerstu|mestu|mikilvaegustu|biggest|largest|main|top|most important|leading)\b|"
        r"ahrifathaett|ahrifavald|\bdrivers?\b|\bdrives?\b|contribut")
_EFFECT_ASK = r"ahrif|driver|drives?\b|contribut|effect|impact"
_EFFECT_OPTION = r"\beffect\b|contribution|ahrif"
_MAX_OPTIONS = 80


def _mentions(question_tokens: set[str], label: str) -> bool:
    """True if a question word starts a label word or vice versa: "reykjavik" ~ "reykjavikurborg"."""
    return any(a.startswith(b) or b.startswith(a) for a in tokens(label) if len(a) >= 4 for b in question_tokens)


def _options_for_prompt(var: Variable, question: str) -> list[tuple[str, str]]:
    pairs = list(zip(var.values, var.value_texts))
    if len(pairs) <= _MAX_OPTIONS:
        return pairs
    q = {t for t in tokens(question) if len(t) >= 4}
    years = set(re.findall(r"\b(?:19|20)\d\d\b", question))
    keep = [p for p in pairs if _TOTAL.search(p[1]) or _mentions(q, p[1]) or any(y in p[0] for y in years)]
    if var.time:
        keep += pairs[-12:]
    seen, out = set(), []
    for p in keep:
        if p[0] not in seen:
            seen.add(p[0])
            out.append(p)
    return out[:_MAX_OPTIONS]


_IS_UNIT = {"year": "árin", "month": "mánuðina", "quarter": "ársfjórðungana"}


_PERCENT_OPTION = r"(?<![\d.])%|\bpercent|prosent|change|breyting|hlutfall"   # an option that is a change, not a level
_NUMBER_WORDS = {  # "síðustu tíu ár", "the last five years": the number spelt out, in the normalized spelling
    "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "twelve": 12,
    "fifteen": 15, "twenty": 20, "thirty": 30, "fifty": 50,
    "tvo": 2, "tvau": 2, "tveggja": 2, "thrju": 3, "thriggja": 3, "fjogur": 4, "fjogurra": 4, "fimm": 5, "sex": 6, "sjo": 7,
    "atta": 8, "niu": 9, "tiu": 10, "tolf": 12, "fimmtan": 15, "tuttugu": 20, "thrjatiu": 30, "fimmtiu": 50,
}
_NUMBER_WORD = re.compile(r"\b(" + "|".join(_NUMBER_WORDS) + r")\b")


def _window(q: str) -> tuple[int, str] | None:
    """(n, "year" | "month" | "quarter") for "the last n years" and the like, in a normalized question."""
    if _DECADE.search(q):
        return 10, "year"
    q = _NUMBER_WORD.sub(lambda m: str(_NUMBER_WORDS[m.group(1)]), q)
    m = _WINDOW.search(q)
    if not m:
        return None
    unit = m.group(2)
    return int(m.group(1)), "month" if unit.startswith(("month", "manud")) else \
        "quarter" if unit.startswith(("quarter", "arsfjord")) else "year"


def _same_year_edge(var: Variable, v: str) -> str | None:
    """The table's first or last period, when v is a period of the same kind and year just beyond it."""
    first, last = var.values[0], var.values[-1]
    if len(v) != len(first) or v[:4].isdigit() is False or v[4:5] != first[4:5]:
        return None
    if v < first and v[:4] == first[:4]:
        return first
    if v > last and v[:4] == last[:4]:
        return last
    return None


_MONTH_NUM = {m: i for i, m in enumerate(("january february march april may june july august september october november "
                                              "december").split(), 1)}
_MONTH_NUM |= {m: i for i, m in enumerate("januar februar mars april mai juni juli agust september oktober november desember".split(), 1)}
_QUARTER_NUM = {"first": 1, "1st": 1, "fyrsta": 1, "fyrsti": 1, "second": 2, "2nd": 2, "odrum": 2, "annar": 2, "annan": 2,
                "third": 3, "3rd": 3, "thridja": 3, "thridji": 3, "fourth": 4, "4th": 4, "fjorda": 4, "fjordi": 4}


def _names_subyear(text: str) -> bool:
    """Does the question name a month or a quarter ("August 2026", "2026M08", "second quarter of 2026")?"""
    q = normalize(text)
    return bool(re.search(r"\b(" + "|".join([*_MONTH_NUM, *_QUARTER_NUM]) + r")\b|\b(?:18|19|20)\d\d\s?[mq]\d|quarter|arsfjordung|fjordung", q))


def _period_from_question(var: Variable, text: str) -> str | None:
    """The period a question names, as this table codes it: "August 2026" -> 2026M08, "second quarter of 2026" ->
    2026Q2, "in 2025" -> 2025. Only when the question names one year, so a range is left to the model."""
    q = normalize(text)
    years = re.findall(r"\b(?:18|19|20)\d\d\b", q)
    if len(set(years)) != 1:
        return None
    y = years[0]
    m = re.search(r"\b(" + "|".join(_MONTH_NUM) + r")\b\s+" + y, q)
    if m and f"{y}M{_MONTH_NUM[m.group(1)]:02d}" in var.values:
        return f"{y}M{_MONTH_NUM[m.group(1)]:02d}"
    qm = re.search(r"\b(" + "|".join(_QUARTER_NUM) + r")\s+(?:quarter|arsfjordung\w*)\s+(?:of\s+)?" + y, q) or \
         re.search(r"\bq([1-4])\s*" + y, q)
    if qm:
        n = _QUARTER_NUM.get(qm.group(1), qm.group(1))
        if f"{y}Q{n}" in var.values:
            return f"{y}Q{n}"
    return y if y in var.values else None


def _total_of(var: Variable) -> str | None:
    """The option that stands for everything: a total, a whole-country option, or a "…, total" option, in that order;
    None when the dimension has no such option (a table of municipalities only)."""
    pairs = list(zip(var.values, var.value_texts))
    for pattern in (re.compile(r"^(total|alls|samtals|all)$"), re.compile(r"whole country|landid allt|all of iceland|^iceland$|^island$"),
                    re.compile(r"\b(total|alls|samtals|allir|allar|oll)\b|^(all|allt) \w")):   # "All species", "All products"
        for code, label in pairs:
            if pattern.search(normalize(label)) and not _subtotal(label):
                return code
    return None


# "Total emission from energy production", "Samtals frá …": a total with a qualifier after it is the total of one
# part, not of the whole dimension. "Total of all industries" stays a total.
_SUBTOTAL = re.compile(r"\b(total|samtals|alls)\b,? (emissions? |losun )?(from|of|in|for|by|excluding|excl\.?|without|"
                       r"fra|i|af|fyrir|an|utan)\b")
_ALL = re.compile(r"\b(all|alls|allir|allar|oll|allt)\b")
# "Therein: Cattle", "Þar af: …", "of which": a part of the option above it, not a category of its own
_SUBITEM = re.compile(r"\b(therein|thereof|of which|whereof|thar af|thar med talid|hluti af)\b")


def _subtotal(label: str) -> bool:
    n = normalize(label)
    return bool(_SUBTOTAL.search(n)) and not _ALL.search(n)


# A figure that is already a ratio: a percentage, share, rate, index, average or median. Read from the labels of the
# options chosen and the table's unit. Such figures are not summed over a dimension and not divided by a total: the
# "percentage of immigrants" row is the share asked for, and a share of it over the population would be nonsense.
_RATIO = re.compile(r"(?<![\d.])%|\bpercent|prosent|hlutfall|hlutf\b|\bshare\b|proportion|\brate\b|\btidni|"
                    r"\bper (1[ ,.]?000|100|10[ ,.]?000|capita|ibua|inhabitant|head|person|resident)|a hverja|a hvern|\ba mann\b|"
                    r"\bindex|visitala|\baverage|\bmean\b|median|"
                    r"midgildi|\bmedal", re.I)


def ratio_measure(meta: TableMeta, out: dict[str, list[str]], only: dict[str, list[str]] = {}) -> str | None:
    """The chosen option (or the table's unit) that makes the figures ratios rather than counts, else None. `only`
    narrows the options looked at in a dimension (the part of a share, not its whole)."""
    if _RATIO.search(normalize(meta.unit)):
        return meta.unit
    for v in meta.variables:
        if v.time:
            continue
        for c in only.get(v.code, out.get(v.code, [])):
            if _RATIO.search(normalize(v.label_of(c))):
                return v.label_of(c)
    return None


def row_unit(meta: TableMeta, labels: dict[str, str]) -> str:
    """The unit of one row: "%" when an option of the row says so (a "Percentage of …" measure in a table whose
    unit is a count), else the table's unit."""
    if any(re.search(r"(?<![\d.])%|\bpercent|prosent|hlutfall", normalize(t)) for t in labels.values()):
        return "%"
    return meta.unit


def _partition(var: Variable) -> bool:
    """A long list of options with no total (partner countries, products): the whole is their sum, so when the
    question names none of them the code sums them all rather than answer for the first one alphabetically.
    Not a partition: a list with sub-totals ("Total emission from energy production") or sub-items ("Therein:
    Cattle"), whose options overlap; summing them counts figures twice."""
    if var.time or len(var.values) < 10 or _total_of(var) is not None:
        return False
    return not any(_subtotal(t) or _SUBITEM.search(normalize(t)) or _TOTAL.search(normalize(t)) for t in var.value_texts)


def _default(var: Variable) -> list[str]:
    """The latest period; for other dimensions the total (see _total_of), else the first option."""
    if var.time:
        return [var.values[-1]]
    total = _total_of(var)
    return [total] if total is not None else [var.values[0]]


def _resolve_value(var: Variable, v: str) -> str | None:
    if v in var.values:
        return v
    for cand in [v, *v.split("=")]:  # models sometimes echo "code=label"
        nv = normalize(cand.strip())
        for code, label in zip(var.values, var.value_texts):
            if normalize(label) == nv or normalize(code) == nv:
                return code
    return None


def guess_op(question: str) -> str | None:
    q = normalize(question)
    return next((op for op, rx in _INTENT if rx.search(q)), None)


_BREAKDOWN_ALL = re.compile(r"\b(all|allar|allir|oll|ollum|each|every|hvert|hverju|hverjum|per|a milli|milli)\b")


def _names_dim(var: Variable, words: list[str]) -> bool:
    """The user's breakdown word names this dimension: "countries" / Country, "sveitarfélögum" / Municipality
    (Icelandic words mapped by breakdown_words), by a shared five-letter stem."""
    own = [t for t in tokens(var.text) if len(t) >= 4]
    return any(t == w or t[:5] == w[:5] for t in own for w in words if len(w) >= 4)


def _asks_breakdown(text: str) -> bool:
    """"by region", "eftir landshlutum", "in all regions", "each year": the question wants the categories apart."""
    from .retrieve import breakdown_words
    return bool(breakdown_words(text)) or bool(_BREAKDOWN_ALL.search(normalize(text)))


def resolve(meta: TableMeta, sel: Selection, question: str = "", part: str | None = None, facts=None) -> Resolved:
    """Map whatever the model returned onto valid codes; fill gaps with defaults. `facts` is the planner's reading of
    the question (places, period, breakdown; hagbot/steps/plan.py), checked against the text; without it (a table
    the user clicked, a published rate) the same facts are read from the text by the patterns below."""
    notes, out, beyond, alternatives, aggregate_over, missing_period, partial = [], {}, None, {}, [], "", ""
    from .retrieve import _DIMENSION_IS, breakdown_words, named_places
    if facts is not None:   # the planner's places as normalized words, as the patterns would give them
        places_part = [t for pl in facts.places for t in tokens(pl) if len(t) >= 4]
        places_all = [t for pl in (facts.all_places or facts.places) for t in tokens(pl) if len(t) >= 4]
        breakdown = facts.breakdown
        subyear = facts.period_kind in ("month", "quarter")
        period_text = facts.period or (part or question)
        by_words = [_DIMENSION_IS.get(t, t) for t in tokens(facts.breakdown_by)]
    else:
        places_part, places_all = named_places(part or question), named_places(f"{part or ''} {question}")
        breakdown = _asks_breakdown(part or question)
        by_words = breakdown_words(normalize(part or question))
        subyear = _names_subyear(part or question)
        period_text = part or question
    by_key = {normalize(k): k for k in sel.selections}
    for var in meta.variables:
        raw = sel.selections.get(var.code) or sel.selections.get(by_key.get(normalize(var.text), ""), [])
        codes = [c for c in (_resolve_value(var, v) for v in raw) if c]
        if var.time:   # "2000M01" in a table that starts at 2000M03: the nearest period in the same year
            for v in raw:
                if not _resolve_value(var, v) and (near := _same_year_edge(var, v)):
                    codes.append(near)
                    notes.append(f"{var.text}: {v} is outside the table, using {near}")
                    if v > near and subyear:
                        # "September 2026" in a table that stops at August: a neighbour may have it (pipeline switches).
                        # A bare "to 2026" means up to the latest figure, so the model's 2026M12 is no reason to switch.
                        beyond = v
            raw = [v for v in raw if _resolve_value(var, v) or not _same_year_edge(var, v)]
        if len(codes) < len(raw):
            notes.append(f"{var.text}: dropped unknown values {[v for v in raw if not _resolve_value(var, v)]}")
        if not codes and var.time and (asked := _period_from_question(var, period_text)):
            codes = [asked]   # the model left the period out: the one the question names, not the latest
            notes.append(f"{var.text}: {asked} from the question")
        # one year named ("prisoners in 2017") that no period of the table falls in: whatever the model chose, the
        # table cannot answer; the pipeline tries a neighbour, else says which years exist (no nearest-year guess)
        if var.time and len(years := set(re.findall(r"\b(?:1[5-9]|20)\d\d\b", normalize(period_text)))) == 1:
            y = next(iter(years))
            if not any(v.startswith(y) or t.startswith(y) for v, t in zip(var.values, var.value_texts)):
                missing_period = y
                notes.append(f"{var.text}: {y} is not in the table ({var.value_texts[0]}–{var.value_texts[-1]})")
        if not codes and _partition(var) and var.code != sel.compare_dimension:
            codes = list(var.values)
            aggregate_over.append(var.code)
            notes.append(f"{var.text}: no total in the table; summed over all {len(codes)} options")
        if not codes:
            codes = _default(var)
            notes.append(f"{var.text}: defaulted to {[var.label_of(c) for c in codes]}")
            if not var.time and _total_of(var) is None and 2 <= len(var.values) < 10:   # Unit: Fob value / Quantity
                alternatives[var.code] = [c for c in var.values if c not in codes]   # the user may want another
            if not var.time and _total_of(var) is None and _names_dim(var, [normalize(t) for t in tokens(question)]):
                partial = var.text   # "greenhouse gas emissions": every gas asked, the table has them one at a time
        out[var.code] = sorted(set(codes), key=var.values.index)

    q = normalize(question)
    # the question's words through the search synonyms, so "men"/"karlar" name Males and "verðbólga" the CPI
    qwords = [w for t in tokens(question) for w in SYNONYMS.get(t, t).split() if len(w) >= 4]
    def mentioned(label: str, siblings: list[str] = ()) -> bool:
        """Does the question name this category? Among siblings that share words ("Consumer price index" and
        "Consumer price index less housing cost"), a category is named only when the words that set it apart are
        all in the question; a category with no words of its own (the plain one) is named by any of its words."""
        words = lambda s: [t for t in tokens(s) if len(t) >= 4]
        hit = lambda t: any(_same_word(t, w) for w in qwords)
        own, others = words(label), [s for s in siblings if s != label]
        shared = {t for s in others for t in words(s)}
        distinct_of = lambda s: [t for t in words(s) if t not in {t for o in [label, *others] if o != s for t in words(o)}]
        distinct = distinct_of(label)
        if not distinct:                      # the plain one ("Consumer price index"): any of its words names it
            return any(hit(t) for t in own)
        if all(hit(t) for t in distinct):     # "multi-flat", "less housing cost" spelled out
            return True
        # only the shared words are in the question ("capital area"): that names every sibling sharing them, unless
        # one sibling is the plain one (then the question meant it) or another's own words are in the question
        return (any(hit(t) for t in own if t in shared) and not any(not distinct_of(s) for s in others)
                and not any(hit(t) for s in others for t in distinct_of(s)))
    # "Unknown domicile", "Not stated": never the answer unless asked for; a place the model made up (Garðabær for
    # Egilsstaðir, which the table lacks) goes too when the others chosen are the ones the question names
    for var in meta.variables:
        if var.time:
            continue
        chosen = out[var.code]
        if not re.search(r"unknown|othekkt|not stated|otilgreint|ospecified", q):
            kept = [c for c in chosen if not re.search(r"unknown|óþekkt|not stated|ótilgreint|unspecified", var.label_of(c), re.I)]
            if len(kept) < len(chosen):
                notes.append(f"{var.text}: dropped {[var.label_of(c) for c in chosen if c not in kept]} (not asked for)")
                out[var.code] = chosen = kept or _default(var)
        if sel.operation in ("value", "series") and len(chosen) >= 2 and not coded(var):
            labels = [var.label_of(c) for c in chosen]
            in_question = [c for c in chosen if mentioned(var.label_of(c), labels)]
            if in_question and len(in_question) < len(chosen):
                notes.append(f"{var.text}: dropped {[var.label_of(c) for c in chosen if c not in in_question]} (not named in the question)")
                alternatives[var.code] = [c for c in chosen if c not in in_question]   # offered as chips
                out[var.code] = chosen = in_question
        # (named among its siblings: "Aluminium" names "360 Aluminium and aluminium products", whose other words
        # the question need not repeat)
        # a measure the question did not name (Tonnes against FOB value): the others as chips, so the user can switch
        if (len(chosen) == 1 and not var.time and _total_of(var) is None and 2 <= len(var.values) < 10
                and var.code != sel.compare_dimension and not mentioned(var.label_of(chosen[0]), var.value_texts)):
            kind = lambda c: ("value" if _VALUE_OPTION.search(normalize(var.label_of(c))) else
                              "quantity" if _QUANTITY_OPTION.search(normalize(var.label_of(c))) else "")
            # read from the user's own words: the planner's English part may say "value" where the user said "how much"
            asked = normalize(question[:-len(part)]) if part and question.endswith(part) and question[:-len(part)].strip() else q
            wanted = "value" if _MONEY.search(asked) else "quantity" if _HOW_MUCH.search(asked) else ""
            if wanted and kind(chosen[0]) not in ("", wanted) and (fit := [c for c in var.values if kind(c) == wanted]):
                notes.append(f"{var.text}: {var.label_of(fit[0])!r} for the {wanted} asked, not {var.label_of(chosen[0])!r}")
                out[var.code] = chosen = fit[:1]
            alternatives[var.code] = [c for c in var.values if c != chosen[0]]
        if (len(chosen) == 1 and _partition(var) and var.code != sel.compare_dimension and var.code not in aggregate_over
                and not mentioned(var.label_of(chosen[0]), var.value_texts)):
            notes.append(f"{var.text}: {var.label_of(chosen[0])!r} is not named in the question and the table has no total; "
                         f"summed over all {len(var.values)} options")
            out[var.code] = chosen = list(var.values)
            aggregate_over.append(var.code)
        # "the housing price index from 2010": one line, the whole country, not every region and house type the model
        # could chart. Several categories stay when the question names one of them, asks for a breakdown ("by region",
        # "eftir landshlutum", "all regions"), or compares along this dimension.
        if (sel.operation in ("value", "series") and len(chosen) >= 2 and not coded(var) and var.code != sel.compare_dimension
                and not any(mentioned(var.label_of(c), [var.label_of(x) for x in chosen]) for c in chosen) and not breakdown
                and (total := _total_of(var)) is not None):
            notes.append(f"{var.text}: none of {[var.label_of(c) for c in chosen]} named in the question; using {[var.label_of(total)]}")
            out[var.code] = [total]
            alternatives[var.code] = [c for c in chosen if c != total]
    # a place the question names that no option and no title of this table carries: say so, rather than answer as if
    place_note = None
    texts = [t for v in meta.variables if not v.time for t in v.value_texts] + [meta.title]
    places = places_part
    def has_place(label: str, place: str) -> bool:
        return any(_same_word(t, place) for t in tokens(label))
    missing = [p for p in places if not any(has_place(label, p) for label in texts)]
    # the whole question's places, not this part's alone: "Reykjavík and Kópavogur together" is planned as one part
    # per town, yet both towns are selected in each and summed
    places = places_all
    for place in missing:
        shown = next((w for w in re.findall(r"[A-ZÁÐÉÍÓÚÝÞÆÖ][\wáðéíóúýþæö]+", part or question) if normalize(w).startswith(place[:5])), place)
        place_note = {"en": f"{shown} is not among this table's options; the figures are for the options selected.",
                      "is": f"{shown} er ekki meðal valkosta í þessari töflu; tölurnar eru fyrir það sem valið var."}
        notes.append(f"{shown}: not in the table's options")
        break
    # ...and the model must not stand another place in for a place the question names (Garðabær for Egilsstaðir,
    # Kópavogur for Reykjavík): in a place dimension, only options the question names, or the total, may stay; with
    # none of them chosen, the named ones answer, or the total under the note. A breakdown ("by municipality") or a
    # ranking along the dimension keeps every category.
    if places and sel.operation != "rank" and not breakdown:
        for var in meta.variables:
            if var.time:
                continue
            named_options = [c for c in var.values if any(has_place(var.label_of(c), p) for p in places)]
            if not (named_options or re.search(r"municipal|sveitarf|region|landsv|landshlut|svaed|domicile|logheimil|\barea\b|\bplace",
                                               normalize(var.text))):
                continue
            if var.code == sel.compare_dimension:
                continue
            chosen = out[var.code]
            dflt = _default(var)
            keep = [c for c in chosen if c in named_options or c in dflt]
            if len(keep) < len(chosen):
                # none of the chosen is named: the named options (up to two) answer; else the total; a table with
                # no total and no named option keeps the model's choice under the note
                total = _total_of(var)
                if not keep and not named_options and not total and _partition(var):
                    aggregate_over.append(var.code)
                    out[var.code] = list(var.values)
                else:
                    out[var.code] = keep or (named_options if 0 < len(named_options) <= 2 else [total] if total else chosen)
                notes.append(f"{var.text}: dropped {[var.label_of(c) for c in chosen if c not in keep]} (a place the question did "
                             f"not name); using {[var.label_of(c) for c in out[var.code]]}")

    # "Iceland" in a question means the whole country, not Icelandic citizens or people born in Iceland
    # ...unless the question compares groups ("Icelanders and foreigners") or the model chose several of them
    if not re.search(r"citizen|rikisborg|born|birth|faedd|faeding|origin|uppruna|icelander|foreign|islending|utlending|erlend", q):
        for var in meta.variables:
            if re.search(r"citizen|birth|origin|country|rikisfang|faedingarland|uppruni", normalize(var.text)) and \
                    len(out[var.code]) == 1 and any(normalize(var.label_of(c)) in ("iceland", "island") for c in out[var.code]):
                out[var.code] = _default(var)
                notes.append(f"{var.text}: 'Iceland' means the whole country; using {[var.label_of(c) for c in out[var.code]]}")

    # An index base the question does not name: the current (newest) one, whatever the model or the default chose.
    # "Base 1983" stays when the question asks for it ("grunnur 1983", "1983=100").
    asked = asked_bases(question)
    for var in meta.variables:
        bases = {c: y for c in var.values if (y := base_year(var.label_of(c)))}
        if var.time or len(bases) < 2:
            continue
        wanted = [c for c, y in bases.items() if y in asked]
        pick = wanted or [max(bases, key=bases.get)]
        if out[var.code] != pick:
            out[var.code] = sorted(pick, key=var.values.index)
            notes.append(f"{var.text}: {'base asked for' if wanted else 'current base'} {[var.label_of(c) for c in pick]}")

    # Inflation, wage rise "in 12 months", GDP growth: the table publishes the change itself; use it rather than
    # computing one between two periods the model picked (it picked 11 months apart, or 2014 to 2025)
    op = sel.operation
    if re.search(_ANNUAL_ASK, q):
        for var in meta.variables:
            changes = [c for c in var.values if re.search(_CHANGE_OPTION, normalize(var.label_of(c)))]
            chosen = out[var.code]
            if var.time or not changes or all(c in changes for c in chosen):
                continue
            words = set(tokens(" ".join(var.label_of(c) for c in chosen)))
            best = max(changes, key=lambda c: len(words & set(tokens(var.label_of(c)))))
            out[var.code] = [best]
            notes.append(f"{var.text}: the question asks for the annual change; using {var.label_of(best)!r}")
    points = bool(re.search(r"\bstig\b|\bstigum\b|\bpoints?\b", q))   # "um hve mörg stig": the index itself, two periods
    if op in ("difference", "pct_change") and not points and any(
            re.search(_CHANGE_OPTION, normalize(var.label_of(c))) for var in meta.variables if not var.time
            for c in out[var.code]):
        for var in meta.variables:
            if var.time and len(out[var.code]) > 1:
                out[var.code] = out[var.code][-1:]
        notes.append(f"operation {op} -> value (the table already gives the change)")
        op = "value"

    # A trend question: every period from the year asked (or a default span) to the latest, as a series
    # "how much has it grown since 2020" asks for one number, not a trend
    trend = bool(re.search(_TREND, q)) and op != "share" and not re.search(r"how much|how many|by how|hve mik|um hve|hve marg", q)
    if trend:
        for var in meta.variables:
            if not var.time:
                continue
            years = re.findall(r"\b(?:18|19|20)\d\d\b", question)
            step = "M" if "M" in var.values[-1] else "Q" if "Q" in var.values[-1] else ""
            start = next((i for i, c in enumerate(var.values) if years and c.startswith(min(years))), None)
            if start is None:
                start = max(0, len(var.values) - _TREND_DEFAULT[step])
            out[var.code] = var.values[start:]
            notes.append(f"{var.text}: a trend, {var.values[start]} to {var.values[-1]}")
        if op != "series":
            notes.append(f"operation {op} -> series (trend wording)")
        op = "series"

    # "The last 15 years": from that calendar year to the latest period. A window reaching back before the table
    # starts gets every period there is, and the answer says where the table begins.
    # ("in the last 12 months" with inflation or wages is the published annual change, handled above)
    # Read from this part alone: "the past 5 years compared to 2000 to 2005" is planned as two parts, and the second
    # must keep its own years. A part that names a year has no window.
    own = normalize(part or question)
    window = None if re.search(_ANNUAL_ASK, q) or re.search(r"\b(?:18|19|20)\d\d\b", own) else _window(own)
    window_note = None
    if window:
        for var in meta.variables:
            if not var.time:
                continue
            n, unit = window
            step = "M" if "M" in var.values[-1] else "Q" if "Q" in var.values[-1] else ""
            if unit == "year":
                start_year = int(var.values[-1][:4]) - n
                start = next((i for i, c in enumerate(var.values) if int(c[:4]) >= start_year), 0)
                short = int(var.values[0][:4]) > start_year
            else:
                per = {"month": {"M": 1, "Q": 1 / 3, "": 1 / 12}, "quarter": {"M": 3, "Q": 1, "": 1 / 4}}[unit][step]
                count = max(1, round(n * per))
                start, short = max(0, len(var.values) - count), count > len(var.values)
            out[var.code] = var.values[start:]
            notes.append(f"{var.text}: the last {n} {unit}s, {var.values[start]} to {var.values[-1]}")
            if short:
                first = var.values[0]
                window_note = {"en": f"The table only goes back to {first}, so this covers {first} to {var.values[-1]}, "
                                     f"not the full {n} {unit}s asked for.",
                               "is": f"Taflan nær aðeins aftur til {first}, svo þetta nær yfir {first} til {var.values[-1]}, "
                                     f"ekki öll {n} {_IS_UNIT[unit]} sem spurt var um."}
        if op == "value":
            op = "series"

    # Stretches of periods with a hole between them ("January 2016 to September 2026" in a table too long to list,
    # so the model saw only 2016 and the last 12 months): a series runs from the first period to the last, never
    # two stretches joined across years. Single periods ("2016, 2020 and 2024") stay as asked.
    for var in meta.variables:
        chosen = out[var.code]
        if not var.time or len(chosen) < 3:
            continue
        idx = [var.values.index(c) for c in chosen]
        runs = sum(1 for a, b in zip(idx, idx[1:]) if b == a + 1)
        if idx[-1] - idx[0] + 1 > len(idx) and runs >= 2:
            out[var.code] = var.values[idx[0]:idx[-1] + 1]
            notes.append(f"{var.text}: filled the gap, {var.values[idx[0]]} to {var.values[idx[-1]]}")
            if op == "value":
                op = "series"

    # "When was it highest": the extreme along the dimension that varies, time by default
    extreme = next((o for o, rx in _EXTREME if rx.search(q)), None) if op in ("value", "series") else None
    if op in ("max", "min") or extreme:
        op = op if op in ("max", "min") else extreme
        multi = [c for c, v in out.items() if len(v) > 1 and c not in aggregate_over]   # a summed list is one value
        time_var = next((v for v in meta.variables if v.time), None)
        compare = sel.compare_dimension if sel.compare_dimension in multi else \
            (time_var.code if time_var and time_var.code in multi else (multi[0] if multi else None))
        if time_var and (compare in (None, time_var.code)) and not window and re.search(_WHEN, q) \
                and not re.search(r"\b(?:18|19|20)\d\d\b", question):
            # no period named: search everything the table has, not just the periods the model was shown
            out[time_var.code], compare = list(time_var.values), time_var.code
            notes.append(f"{time_var.text}: {op} over every period, {time_var.values[0]} to {time_var.values[-1]}")
        if compare:
            sel = sel.model_copy(update={"compare_dimension": compare})
            notes.append(f"operation {op} along {meta.var(compare).text}")
        else:
            notes.append(f"{op} needs several values to search; reporting values instead")
            op = "value"

    # No year or period in the question: the latest period, whatever the model chose
    if not re.search(r"\b(?:18|19|20)\d\d\b", question) and op in ("value", "share") and not window:
        for var in meta.variables:
            if var.time and out.get(var.code) != [var.values[-1]]:
                notes.append(f"{var.text}: no period asked, using latest {var.values[-1]}")
                out[var.code] = [var.values[-1]]

    compare = sel.compare_dimension if sel.compare_dimension in out else None
    if compare in aggregate_over:   # the dimension an operation runs along is never summed away
        aggregate_over.remove(compare)
        notes.append(f"{meta.var(compare).text}: compared, not summed")
    hint = guess_op(question)
    gives_change = any(re.search(_CHANGE_OPTION, normalize(var.label_of(c))) for var in meta.variables if not var.time
                       for c in out[var.code])
    if (op in ("value", "series") and not trend and hint in ("difference", "pct_change") and not gives_change
            and any(len(v) == 2 for v in out.values())):
        notes.append(f"operation {op} -> {hint} (question wording)")
        op = hint
    if op == "value" and hint == "share":
        notes.append("operation value -> share (question wording)")
        op = "share"
    if op == "pct_change" and re.search(r"\bstig\b|\bstigum\b|\bpoints?\b", q):   # "um hve mörg stig": index points, not %
        notes.append("operation pct_change -> difference (points asked)")
        op = "difference"
    if op == "pct_change" and hint == "difference" and not re.search(r"percent|%|prosent|hlutfall|per cent|hundradshlut", q):
        notes.append("operation pct_change -> difference (a change asked, no percent word)")   # "how much did Reykjavík grow"
        op = "difference"
    if op == "difference" and re.search(r"\bstig\b|\bstigum\b|\bpoints?\b", q):
        # points are a difference of the index itself: a dimension chosen as "Annual change, %" goes back to the level
        for var in meta.variables:
            chosen = out.get(var.code) or []
            if var.time or not chosen or not all(re.search(_PERCENT_OPTION, normalize(var.label_of(c))) for c in chosen):
                continue
            level = [c for c in var.values if not re.search(_PERCENT_OPTION, normalize(var.label_of(c)))]
            if level:
                notes.append(f"{var.text}: points asked, {var.label_of(level[0])!r} instead of {var.label_of(chosen[0])!r}")
                out[var.code] = [level[0]]
    if op == "value" and hint == "sum":   # "Reykjavík and Kópavogur together": the chosen categories added up
        dim = next((c for c, v in out.items() if len(v) >= 2 and not meta.var(c).time), None)
        if dim:
            notes.append(f"operation value -> sum over {meta.var(dim).text} (question wording)")
            op, compare = "sum", dim
    if op in ("difference", "pct_change", "share") and not compare:
        compare = next((c for c, v in out.items() if len(v) > 1), None)
    if op in ("difference", "pct_change") and (not compare or len(out[compare]) < 2):
        notes.append(f"{op} needs two values to compare; reporting values instead")
        op = "value"
    order = None
    if op in ("difference", "pct_change") and compare and not meta.var(compare).time and len(out[compare]) == 2:
        # comparing categories, not periods: the latest period unless one is named, and the question's own order
        if not re.search(r"\b(?:18|19|20)\d\d\b", question):
            for var in meta.variables:
                if var.time and out.get(var.code) != [var.values[-1]]:
                    notes.append(f"{var.text}: no period asked, using latest {var.values[-1]}")
                    out[var.code] = [var.values[-1]]
        order = _mention_order(meta.var(compare), out[compare], question)
        if order:
            notes.append(f"{op}: {meta.var(compare).label_of(order[0])} minus {meta.var(compare).label_of(order[1])}, as asked")
    part = whole = None
    if op == "share":
        var = meta.var(compare) if compare else None
        part = _resolve_value(var, sel.part or "") if var else None
        whole = _resolve_value(var, sel.whole or "") if var else None
        if part and whole and var and {part, whole} - set(out[compare]):
            # the model named the part and the whole but selected only one of them ("Females", not "Total")
            out[compare] = sorted(set(out[compare]) | {part, whole}, key=var.values.index)
            notes.append(f"share: selecting both {var.label_of(part)!r} and {var.label_of(whole)!r}")
        if not (part and whole and var):
            # the model picked only the part ("Reykjavík"): the whole is that dimension's total
            for v in meta.variables:
                chosen = out[v.code]
                total = next((c for c, t in zip(v.values, v.value_texts) if _TOTAL.search(t)), None)
                if not v.time and len(chosen) == 1 and total and chosen[0] != total:
                    compare, var, part, whole = v.code, v, chosen[0], total
                    out[v.code] = sorted([total, chosen[0]], key=v.values.index)
                    notes.append(f"share: part {v.label_of(part)!r}, whole {v.label_of(total)!r}")
                    break
        if not (part and whole and part in out[compare] and whole in out[compare]):
            notes.append("share needs a part and a whole; reporting values instead")
            op, part, whole = "value", None, None
    # "Hverjir eru undirflokkar neysluvísitölunnar", "main drivers of …", "biggest industries": the categories of the
    # table's coded dimension (COICOP, NACE … read from the labels), at one period. A category the question names is
    # opened one level down; "largest/main" keeps the biggest few. Works for any table whose categories carry codes.
    breakdown, top = None, None
    # the dimension the user's own breakdown word names ("by countries", "eftir sveitarfélögum"), coded or not
    want = [v for v in meta.variables if not v.time and _names_dim(v, by_words)]
    one_period = all(len(out[v.code]) == 1 for v in meta.variables if v.time)
    if ((re.search(_LIST, q) or re.search(_TOP, q) or (want and op == "value" and one_period))
            and op not in ("difference", "pct_change", "share")):
        cand = want or [v for v in meta.variables if not v.time and coded(v)]
        var = max(cand, key=lambda v: len(coded(v))) if cand else None
        kids = (children(var, named(var, question)) or children(var, None)) if var else []
        if var and not kids and var in want:   # a plain list (partner countries): every option but the total
            kids = [c for c in var.values if c != _total_of(var)]
        if var and len(kids) >= 2:
            out[var.code], breakdown, compare = kids, var.code, var.code
            if var.code in aggregate_over:   # listed one by one, not summed
                aggregate_over.remove(var.code)
            if re.search(_EFFECT_ASK, q):   # drivers: the measure saying how much each part moved the whole
                for v in meta.variables:
                    hits = [c for c, t in zip(v.values, v.value_texts) if re.search(_EFFECT_OPTION, normalize(t))]
                    if not v.time and v.code != var.code and hits:
                        out[v.code] = hits[:1]
            for v in meta.variables:
                if v.code == var.code:
                    continue
                if v.time:   # one period: the latest unless the question names one
                    named_period = [c for c in out[v.code] if c[:4] in question]
                    out[v.code] = [named_period[-1] if named_period else v.values[-1]]
                elif len(out[v.code]) > 1 and v.code not in aggregate_over:   # one measure per category
                    out[v.code] = out[v.code][:1]
            if re.search(_TOP, q):
                m = re.search(r"\b(\d{1,2})\b", re.sub(r"\b(?:18|19|20)\d\d\b", "", q))
                top = int(m.group(1)) if m else 6
            elif len(kids) > 12 and not _BREAKDOWN_ALL.search(q):   # 253 partner countries: the largest ten, unless all asked
                top = 10
            op = "rank"
            notes.append(f"{var.text}: {'the largest ' + str(top) + ' of ' if top else ''}{len(kids)} categories"
                         + (f" under {var.label_of(named(var, question))!r}" if named(var, question) else ""))
    # Units. A figure that is already a ratio (a percentage, rate, index, average: ratio_measure) is the answer as it
    # stands: it is not divided by a total, not added up over a dimension, and not compared with a count.
    if op == "share" and compare:
        ratio = ratio_measure(meta, out, {compare: [part]})
        if ratio:
            notes.append(f"share: {ratio!r} is already a ratio; shown as it is, not divided by {meta.var(compare).label_of(whole)!r}")
            out[compare] = [part]
            op, compare, part, whole = "value", None, None, None
    if op in ("difference", "pct_change") and compare and not meta.var(compare).time:
        kinds = {bool(ratio_measure(meta, out, {compare: [c]})) for c in out[compare]}
        if len(kinds) > 1:
            notes.append(f"{op}: the options compared are in different units (a ratio and a count); reporting values instead")
            op, compare, order = "value", None, None
    ratio = ratio_measure(meta, out)
    if ratio and op == "sum":
        notes.append(f"sum: {ratio!r} is a ratio and cannot be added up; reporting values instead")
        op = "value"
    if ratio and aggregate_over:
        notes.append(f"{ratio!r} is a ratio and cannot be summed over {', '.join(meta.var(c).text for c in aggregate_over)}")
        aggregate_over = []
    return Resolved(selections=out, operation=op, compare_dimension=compare, part=part, whole=whole, order=order, notes=notes,
                    window_note=window_note, breakdown=breakdown, top=top, beyond=beyond, place_note=place_note,
                    alternatives=alternatives, aggregate_over=aggregate_over, missing_period=missing_period, partial=partial)


async def select(llm: LLM, meta: TableMeta, question: str, original: str | None = None, facts=None) -> Resolved:
    lines = []
    for var in meta.variables:
        opts = "; ".join(f"{c}={t}" for c, t in _options_for_prompt(var, f"{original or ''} {question}"))
        more = f" (showing {_MAX_OPTIONS} of {len(var.values)})" if len(var.values) > _MAX_OPTIONS else ""
        lines.append(f'- code "{var.code}" ({var.text}{", time" if var.time else ""}){more}: {opts}')
    asked = f"User asked: {original}\nThis part: {question}" if original and original != question else f"Question: {question}"
    user = f"{asked}\n\nTable {meta.id}: {meta.title}\nDimensions:\n" + "\n".join(lines)
    if not voting_on():
        sel = await llm.complete_json(SYSTEM, user, Selection)
        return resolve(meta, sel, f"{original or ''} {question}", part=question, facts=facts)
    sel, agreement = vote_selection(await ask_all(llm, SYSTEM, user, Selection))
    res = resolve(meta, sel, f"{original or ''} {question}", part=question, facts=facts)
    res.notes.insert(0, "votes: " + ", ".join(f"{(meta.var(k).text if k in {v.code for v in meta.variables} else k)} {a}"
                                              for k, a in agreement.items()))
    return res


def vote_selection(samples: list[Selection]) -> tuple[Selection, dict[str, str]]:
    """One Selection from several: per dimension the most common set of codes, and the most common operation
    (with its compare dimension, part and whole). Returns it with each choice's agreement, e.g. {"Ár": "3/3"}."""
    n, agreement, chosen = len(samples), {}, {}
    for dim in dict.fromkeys(k for s in samples for k in s.selections):
        picks = [tuple(sorted(s.selections.get(dim, []))) for s in samples]
        win, c = majority(picks)
        if win:
            chosen[dim] = list(win)
        agreement[dim] = f"{c}/{n}"
    op, c = majority([(s.operation, s.compare_dimension, s.part, s.whole) for s in samples])
    agreement["operation"] = f"{c}/{n}"
    return Selection(selections=chosen, operation=op[0], compare_dimension=op[1], part=op[2], whole=op[3]), agreement
