"""Step 3: choose the dimension values (filters) and the calculation. Codes are validated in code, never trusted."""

import re
from typing import Literal

from pydantic import BaseModel, Field

from ..catalog import SYNONYMS
from ..llm import LLM
from ..outcomes import AnswerFailure
from ..vote import ask_all, majority, voting_on
from ..sources import TableMeta, Variable
from ..hierarchy import children, coded, named
from ..text import asked_bases, base_year, normalize, tokens
from .constraints import RequestedFilter, has_requested_period, period_requirements, validate_filters

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
    requested_filters: list[RequestedFilter] = Field(description=(
        "Every explicit filter required by THIS part of the question, including unsupported filters. "
        "Quote the user's wording; do not record defaults as requested filters."))


class Resolved(BaseModel):
    selections: dict[str, list[str]]
    operation: Op | Literal["rank"]  # rank: categories of a coded dimension, listed or largest first (set in code)
    compare_dimension: str | None = None
    part: str | None = None
    whole: str | None = None
    order: list[str] | None = None  # for a difference between categories: [first, second], result = first − second
    notes: list[str] = []
    window_note: dict[str, str] | None = None  # en/is, when the period asked for starts before the table does
    breakdown: str | None = None  # rank: the coded dimension listed, so its categories can be opened further
    top: int | None = None        # rank: keep the n largest (by size, either sign); None lists them all


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
Record EVERY explicit filter in requested_filters, quoting the question. Include an unavailable filter with
values=[] (and dimension=null if the dimension is absent). Never replace it by a total, a nearby period or another
category, or omit it because the table cannot answer it. For multiple parts, record only this part's requirements.
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


def _default(var: Variable) -> list[str]:
    if var.time:
        return [var.values[-1]]
    for code, label in zip(var.values, var.value_texts):
        if _TOTAL.search(label):
            return [code]
    if len(var.values) == 1:
        return [var.values[0]]
    raise AnswerFailure("clarify", "missing_filter", var.text)


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


def resolve(meta: TableMeta, sel: Selection, question: str = "", part: str | None = None,
            filter_question: str | None = None) -> Resolved:
    """Map equivalent labels to codes; never repair an unavailable requested value into a default."""
    notes, out = [], {}
    own_question = filter_question or part or question
    known_keys = {normalize(v.code) for v in meta.variables} | {normalize(v.text) for v in meta.variables}
    if unknown := [k for k in sel.selections if normalize(k) not in known_keys]:
        raise AnswerFailure("unverified", "invalid_dimension", str(unknown))
    by_key = {normalize(k): k for k in sel.selections}
    for var in meta.variables:
        if not var.values:
            raise AnswerFailure("unavailable", "empty_dimension", var.text)
        if var.time:
            period_requirements(meta, var, own_question)
        raw = (sel.selections.get(var.code) or sel.selections.get(by_key.get(normalize(var.code), ""), [])
               or sel.selections.get(by_key.get(normalize(var.text), ""), []))
        codes = [c for c in (_resolve_value(var, v) for v in raw) if c]
        if len(codes) < len(raw):
            raise AnswerFailure("unverified", "invalid_selection", f"{var.text}: {raw}")
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

    # An index base the question does not name: the current (newest) one, whatever the model or the default chose.
    # "Base 1983" stays when the question asks for it ("grunnur 1983", "1983=100").
    asked = asked_bases(question)
    offered_bases = {y for v in meta.variables if not v.time for label in v.value_texts if (y := base_year(label))}
    if asked and not asked <= offered_bases | ({base_year(meta.title)} - {None}):
        raise AnswerFailure("unavailable", "requested_base_unavailable", str(sorted(asked)))
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
    if op in ("difference", "pct_change") and any(
            re.search(_CHANGE_OPTION, normalize(var.label_of(c))) for var in meta.variables if not var.time
            for c in out[var.code]):
        for var in meta.variables:
            if var.time and len(out[var.code]) > 1 and not has_requested_period(own_question):
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
                if years:
                    raise AnswerFailure("unavailable", "requested_period_unavailable", min(years))
                start = max(0, len(var.values) - _TREND_DEFAULT[step])
            out[var.code] = var.values[start:]
            notes.append(f"{var.text}: a trend, {var.values[start]} to {var.values[-1]}")
        if op != "series":
            notes.append(f"operation {op} -> series (trend wording)")
        op = "series"

    # "The last 15 years": from that calendar year to the latest period. Reject incomplete coverage.
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
                raise AnswerFailure("unavailable", "requested_window_unavailable", f"{n} {unit}s; starts {var.values[0]}")
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
            raise AnswerFailure("unverified", "incomplete_calculation", op)

    # No year or period in the question: the latest period, whatever the model chose
    if not has_requested_period(own_question) and op in ("value", "share") and not window:
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
        raise AnswerFailure("unverified", "incomplete_calculation", op)
    order = None
    if op in ("difference", "pct_change") and compare and not meta.var(compare).time and len(out[compare]) == 2:
        # comparing categories, not periods: the latest period unless one is named, and the question's own order
        if not has_requested_period(own_question):
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
        if (sel.part and not part) or (sel.whole and not whole):
            raise AnswerFailure("unverified", "invalid_share", f"{sel.part} / {sel.whole}")
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
            raise AnswerFailure("unverified", "incomplete_calculation", "share")
    # "Hverjir eru undirflokkar neysluvísitölunnar", "main drivers of …", "biggest industries": the categories of the
    # table's coded dimension (COICOP, NACE … read from the labels), at one period. A category the question names is
    # opened one level down; "largest/main" keeps the biggest few. Works for any table whose categories carry codes.
    breakdown, top = None, None
    if (re.search(_LIST, q) or re.search(_TOP, q)) and op not in ("difference", "pct_change", "share"):
        cand = [v for v in meta.variables if not v.time and coded(v)]
        var = max(cand, key=lambda v: len(coded(v))) if cand else None
        kids = (children(var, named(var, question)) or children(var, None)) if var else []
        if var and len(kids) >= 2:
            out[var.code], breakdown, compare = kids, var.code, var.code
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
                elif len(out[v.code]) > 1:   # one measure per category
                    out[v.code] = out[v.code][:1]
            if re.search(_TOP, q):
                m = re.search(r"\b(\d{1,2})\b", re.sub(r"\b(?:18|19|20)\d\d\b", "", q))
                top = int(m.group(1)) if m else 6
            op = "rank"
            notes.append(f"{var.text}: {'the largest ' + str(top) + ' of ' if top else ''}{len(kids)} categories"
                         + (f" under {var.label_of(named(var, question))!r}" if named(var, question) else ""))
    validate_filters(meta, out, sel.requested_filters, own_question, quoted_question=question)
    return Resolved(selections=out, operation=op, compare_dimension=compare, part=part, whole=whole, order=order, notes=notes,
                    window_note=window_note, breakdown=breakdown, top=top)


async def select(llm: LLM, meta: TableMeta, question: str, original: str | None = None,
                 *, filter_question: str | None = None) -> Resolved:
    lines = []
    for var in meta.variables:
        opts = "; ".join(f"{c}={t}" for c, t in _options_for_prompt(var, f"{original or ''} {question}"))
        more = f" (showing {_MAX_OPTIONS} of {len(var.values)})" if len(var.values) > _MAX_OPTIONS else ""
        lines.append(f'- code "{var.code}" ({var.text}{", time" if var.time else ""}){more}: {opts}')
    asked = f"User asked: {original}\nThis part: {question}" if original and original != question else f"Question: {question}"
    user = f"{asked}\n\nTable {meta.id}: {meta.title}\nDimensions:\n" + "\n".join(lines)
    if not voting_on():
        sel = await llm.complete_json(SYSTEM, user, Selection)
        return resolve(meta, sel, f"{original or ''} {question}", part=question, filter_question=filter_question)
    sel, agreement = vote_selection(await ask_all(llm, SYSTEM, user, Selection))
    res = resolve(meta, sel, f"{original or ''} {question}", part=question, filter_question=filter_question)
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
    # Keep every grounded requirement reported by the voters; disagreement must not erase a requested filter.
    requirements = {r.model_dump_json(): r for sample in samples for r in sample.requested_filters}
    return Selection(selections=chosen, operation=op[0], compare_dimension=op[1], part=op[2], whole=op[3],
                     requested_filters=list(requirements.values())), agreement
