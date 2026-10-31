"""Step 3: choose the dimension values (filters) and the calculation. Codes are validated in code, never trusted."""

import re
from typing import Literal

from pydantic import BaseModel, Field

from ..catalog import SYNONYMS
from ..llm import LLM
from ..vote import ask_all, majority, voting_on
from ..sources import TableMeta, Variable
from ..text import normalize, tokens

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
    operation: Op
    compare_dimension: str | None = None
    part: str | None = None
    whole: str | None = None
    order: list[str] | None = None  # for a difference between categories: [first, second], result = first − second
    notes: list[str] = []
    window_note: dict[str, str] | None = None  # en/is, when the period asked for starts before the table does


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
Never compute anything yourself."""

_INTENT = [  # cheap keyword check; overrides a "value" answer when the question clearly asks for a calculation
    ("pct_change", re.compile(r"(percent|%|prosent)", re.I)),
    ("share", re.compile(r"(share|proportion|what part|hlutfall|hluti)", re.I)),
    ("difference", re.compile(r"(grow|grew|increase|decrease|declin|change|differ|fjolg|faekk|breyt|aukn|munur)", re.I)),
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


def _window(q: str) -> tuple[int, str] | None:
    """(n, "year" | "month" | "quarter") for "the last n years" and the like, in a normalized question."""
    if _DECADE.search(q):
        return 10, "year"
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


def _default(var: Variable) -> list[str]:
    if var.time:
        return [var.values[-1]]
    for code, label in zip(var.values, var.value_texts):
        if _TOTAL.search(label):
            return [code]
    return [var.values[0]]


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


def resolve(meta: TableMeta, sel: Selection, question: str = "", part: str | None = None) -> Resolved:
    """Map whatever the model returned onto valid codes; fill gaps with defaults."""
    notes, out = [], {}
    by_key = {normalize(k): k for k in sel.selections}
    for var in meta.variables:
        raw = sel.selections.get(var.code) or sel.selections.get(by_key.get(normalize(var.text), ""), [])
        codes = [c for c in (_resolve_value(var, v) for v in raw) if c]
        if var.time:   # "2000M01" in a table that starts at 2000M03: the nearest period in the same year
            for v in raw:
                if not _resolve_value(var, v) and (near := _same_year_edge(var, v)):
                    codes.append(near)
                    notes.append(f"{var.text}: {v} is outside the table, using {near}")
            raw = [v for v in raw if _resolve_value(var, v) or not _same_year_edge(var, v)]
        if len(codes) < len(raw):
            notes.append(f"{var.text}: dropped unknown values {[v for v in raw if not _resolve_value(var, v)]}")
        if not codes:
            codes = _default(var)
            notes.append(f"{var.text}: defaulted to {[var.label_of(c) for c in codes]}")
        out[var.code] = sorted(set(codes), key=var.values.index)

    # "Iceland" in a question means the whole country, not Icelandic citizens or people born in Iceland
    q = normalize(question)
    # ...unless the question compares groups ("Icelanders and foreigners") or the model chose several of them
    if not re.search(r"citizen|rikisborg|born|birth|faedd|faeding|origin|uppruna|icelander|foreign|islending|utlending|erlend", q):
        for var in meta.variables:
            if re.search(r"citizen|birth|origin|country|rikisfang|faedingarland|uppruni", normalize(var.text)) and \
                    len(out[var.code]) == 1 and any(normalize(var.label_of(c)) in ("iceland", "island") for c in out[var.code]):
                out[var.code] = _default(var)
                notes.append(f"{var.text}: 'Iceland' means the whole country; using {[var.label_of(c) for c in out[var.code]]}")

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
    if op in ("difference", "pct_change") and any(
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

    # "When was it highest": the extreme along the dimension that varies, time by default
    extreme = next((o for o, rx in _EXTREME if rx.search(q)), None) if op in ("value", "series") else None
    if op in ("max", "min") or extreme:
        op = op if op in ("max", "min") else extreme
        multi = [c for c, v in out.items() if len(v) > 1]
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
    hint = guess_op(question)
    if op in ("value", "series") and not trend and hint in ("difference", "pct_change") and any(len(v) == 2 for v in out.values()):
        notes.append(f"operation {op} -> {hint} (question wording)")
        op = hint
    if op == "value" and hint == "share":
        notes.append("operation value -> share (question wording)")
        op = "share"
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
    return Resolved(selections=out, operation=op, compare_dimension=compare, part=part, whole=whole, order=order, notes=notes,
                    window_note=window_note)


async def select(llm: LLM, meta: TableMeta, question: str, original: str | None = None) -> Resolved:
    lines = []
    for var in meta.variables:
        opts = "; ".join(f"{c}={t}" for c, t in _options_for_prompt(var, f"{original or ''} {question}"))
        more = f" (showing {_MAX_OPTIONS} of {len(var.values)})" if len(var.values) > _MAX_OPTIONS else ""
        lines.append(f'- code "{var.code}" ({var.text}{", time" if var.time else ""}){more}: {opts}')
    asked = f"User asked: {original}\nThis part: {question}" if original and original != question else f"Question: {question}"
    user = f"{asked}\n\nTable {meta.id}: {meta.title}\nDimensions:\n" + "\n".join(lines)
    if not voting_on():
        sel = await llm.complete_json(SYSTEM, user, Selection)
        return resolve(meta, sel, f"{original or ''} {question}", part=question)
    sel, agreement = vote_selection(await ask_all(llm, SYSTEM, user, Selection))
    res = resolve(meta, sel, f"{original or ''} {question}", part=question)
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
