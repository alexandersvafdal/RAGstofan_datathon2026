"""Step 3: choose the dimension values (filters) and the calculation. Codes are validated in code, never trusted."""

import re
from typing import Literal

from pydantic import BaseModel, Field

from ..llm import LLM
from ..sources import TableMeta, Variable
from ..text import normalize, tokens

Op = Literal["value", "series", "difference", "pct_change", "share"]


class Selection(BaseModel):
    selections: dict[str, list[str]] = Field(description="dimension code -> list of value codes")
    operation: Op = Field(description=(
        "value: report the number(s). series: show a trend or comparison over many values. "
        "difference / pct_change: change between the first and last selected value of compare_dimension. "
        "share: part divided by whole within compare_dimension."))
    compare_dimension: str | None = Field(default=None, description="dimension code the operation runs along")
    part: str | None = Field(default=None, description="value code of the part, for share")
    whole: str | None = Field(default=None, description="value code of the whole (usually the total), for share")


class Resolved(BaseModel):
    selections: dict[str, list[str]]
    operation: Op
    compare_dimension: str | None = None
    part: str | None = None
    whole: str | None = None
    notes: list[str] = []


SYSTEM = """You fill in a query against one Statistics Iceland table.
For every dimension choose the value codes the question needs, copied exactly from the options.
- If the question does not mention a dimension, choose its total (e.g. "Total", "Alls") or, for time, the latest period.
- For "how much did X grow from A to B" select both periods and use difference or pct_change along the time dimension.
- For "what share of ... is X" select X and the total, and use share with part=X and whole=total.
- For trends ("how has ... changed", "over the years") select a range of periods and use series.
Never compute anything yourself."""

_INTENT = [  # cheap keyword check; overrides a "value" answer when the question clearly asks for a calculation
    ("pct_change", re.compile(r"(percent|%|prosent)", re.I)),
    ("share", re.compile(r"(share|proportion|what part|hlutfall|hluti)", re.I)),
    ("difference", re.compile(r"(grow|grew|increase|decrease|declin|change|differ|fjolg|faekk|breyt|aukn|munur)", re.I)),
]
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


def resolve(meta: TableMeta, sel: Selection, question: str = "") -> Resolved:
    """Map whatever the model returned onto valid codes; fill gaps with defaults."""
    notes, out = [], {}
    by_key = {normalize(k): k for k in sel.selections}
    for var in meta.variables:
        raw = sel.selections.get(var.code) or sel.selections.get(by_key.get(normalize(var.text), ""), [])
        codes = [c for c in (_resolve_value(var, v) for v in raw) if c]
        if len(codes) < len(raw):
            notes.append(f"{var.text}: dropped unknown values {[v for v in raw if not _resolve_value(var, v)]}")
        if not codes:
            codes = _default(var)
            notes.append(f"{var.text}: defaulted to {[var.label_of(c) for c in codes]}")
        out[var.code] = sorted(set(codes), key=var.values.index)

    # No year or period in the question: the latest period, whatever the model chose
    if not re.search(r"\b(?:18|19|20)\d\d\b", question) and sel.operation == "value":
        for var in meta.variables:
            if var.time and out.get(var.code) != [var.values[-1]]:
                notes.append(f"{var.text}: no period asked, using latest {var.values[-1]}")
                out[var.code] = [var.values[-1]]

    compare = sel.compare_dimension if sel.compare_dimension in out else None
    op = sel.operation
    hint = guess_op(question)
    if op in ("value", "series") and hint in ("difference", "pct_change") and any(len(v) == 2 for v in out.values()):
        notes.append(f"operation {op} -> {hint} (question wording)")
        op = hint
    if op in ("difference", "pct_change", "share") and not compare:
        compare = next((c for c, v in out.items() if len(v) > 1), None)
    if op in ("difference", "pct_change") and (not compare or len(out[compare]) < 2):
        notes.append(f"{op} needs two values to compare; reporting values instead")
        op = "value"
    part = whole = None
    if op == "share":
        var = meta.var(compare) if compare else None
        part = _resolve_value(var, sel.part or "") if var else None
        whole = _resolve_value(var, sel.whole or "") if var else None
        if not (part and whole and part in out[compare] and whole in out[compare]):
            notes.append("share needs a part and a whole; reporting values instead")
            op, part, whole = "value", None, None
    return Resolved(selections=out, operation=op, compare_dimension=compare, part=part, whole=whole, notes=notes)


async def select(llm: LLM, meta: TableMeta, question: str, original: str | None = None) -> Resolved:
    lines = []
    for var in meta.variables:
        opts = "; ".join(f"{c}={t}" for c, t in _options_for_prompt(var, f"{original or ''} {question}"))
        more = f" (showing {_MAX_OPTIONS} of {len(var.values)})" if len(var.values) > _MAX_OPTIONS else ""
        lines.append(f'- code "{var.code}" ({var.text}{", time" if var.time else ""}){more}: {opts}')
    asked = f"User asked: {original}\nThis part: {question}" if original and original != question else f"Question: {question}"
    user = f"{asked}\n\nTable {meta.id}: {meta.title}\nDimensions:\n" + "\n".join(lines)
    sel = await llm.complete_json(SYSTEM, user, Selection)
    return resolve(meta, sel, f"{original or ''} {question}")
