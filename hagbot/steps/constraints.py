"""Checks that filter resolution preserves requirements, rather than repairing their meaning."""

import re
from datetime import date

from pydantic import BaseModel, Field

from ..catalog import SYNONYMS
from ..outcomes import AnswerFailure
from ..sources import TableMeta, Variable
from ..text import _BASE_ASKED, normalize, tokens


class RequestedFilter(BaseModel):
    dimension: str | None = Field(description="Exact dimension code, or null if the table lacks the requested dimension")
    mention: str = Field(description="Exact quote of the explicit requirement from the question, not an inferred default")
    values: list[str] = Field(description="All required value codes. Empty if the table cannot represent the requirement")


_YEAR_NUMBER = r"(?:1[6-9]|2\d)\d\d"
_YEAR = re.compile(rf"\b{_YEAR_NUMBER}\b")
_PERIOD = re.compile(rf"\b({_YEAR_NUMBER})([MQ])(\d{{1,2}})\b", re.I)
_MONTH_NAMES = (
    "january|jan(?:uar)?", "february|feb(?:ruar)?", "march|mar|mars", "april|apr", "may|mai",
    "june|jun[ia]?", "july|jul[ia]?", "august|aug|agust", "september|sep|sept", "october|oct|oktober|okt",
    "november|nov", "december|dec|desember|des",
)
_MONTHS = [re.compile(rf"\b(?:{name})\b") for name in _MONTH_NAMES]
QUARTER_NUMBERS = {"first": 1, "1st": 1, "fyrsta": 1, "fyrsti": 1, "second": 2, "2nd": 2, "odrum": 2,
                   "annar": 2, "annan": 2, "third": 3, "3rd": 3, "thridja": 3, "thridji": 3,
                   "fourth": 4, "4th": 4, "fjorda": 4, "fjordi": 4}
_ORDINAL_QUARTER = re.compile(r"\b(" + "|".join(QUARTER_NUMBERS) + r")\s+(?:quarter|arsfjordung\w*|fjordung\w*)\b")
_RELATIVE = re.compile(r"\blast (?:year|month|quarter)\b|\bprevious year\b|\bi fyrra\b|\bfyrra ar\b|"
                       r"\bsidast[ai] (?:ar|manud\w*|arsfjord\w*)\b")
_WINDOW = re.compile(r"\b(?:last|past|previous|sidustu|seinustu|undanfarin\w*)\s+(?:\d+|decade|aratug)")


def _time_text(question: str) -> str:
    # The base of an index is a measure constraint, not an observation year.
    # Remove only the base phrase: the same year can also occur as an observation date.
    return _BASE_ASKED.sub(" ", normalize(question))


def has_requested_period(question: str) -> bool:
    q = _time_text(question)
    return bool(_YEAR.search(q) or _PERIOD.search(q) or _RELATIVE.search(q) or _WINDOW.search(q)
                or any(rx.search(q) for rx in _MONTHS) or _ORDINAL_QUARTER.search(q)
                or re.search(r"\bq[1-4]\b|\b[1-4]\.\s*arsfjord", q))


def period_requirements(meta: TableMeta, var: Variable, question: str) -> list[set[str]]:
    """Each set is an allowed representation of one explicit date requirement. No nearby-date substitutions."""
    q = _time_text(question)
    if "M" not in var.values[-1] and "Q" not in var.values[-1] and re.search(r"population|mannfjold", normalize(meta.title)):
        # Annual population reference dates are not monthly observations.
        q = re.sub(r"\b1\.?\s*(?:january|januar|jan)\b|\b(?:january|januar|jan)\.?\s*1(?:st)?\b", "", q)
    groups = []
    for match in _PERIOD.finditer(q):
        year, kind, number = match.groups()
        code = f"{year}{kind.upper()}{int(number):02d}" if kind.upper() == "M" else f"{year}Q{int(number)}"
        groups.append({code} & set(var.values))
    years = set(_YEAR.findall(q)) | {m[1] for m in _PERIOD.finditer(q)}
    months = {i for i, rx in enumerate(_MONTHS, 1) if rx.search(q)}
    quarters = set(re.findall(r"\bq([1-4])\b", q)) | set(re.findall(r"\b([1-4])\.\s*arsfjord", q))
    for mention in _ORDINAL_QUARTER.finditer(q):
        number = QUARTER_NUMBERS[mention[1]]
        quarters.add(str(number))
        after = re.match(rf"\s+(?:of\s+|arsins\s+)?({_YEAR_NUMBER})\b", q[mention.end():])
        if after:
            groups.append({f"{after[1]}Q{number}"} & set(var.values))
    today = date.today()
    if re.search(r"\blast year\b|\bprevious year\b|\bi fyrra\b|\bfyrra ar\b|\bsidasta ar\b", q):
        years.add(str(today.year - 1))
    if re.search(r"\blast month\b|\bsidast[ai] manud", q):
        months.add(today.month - 1 or 12)
        years.add(str(today.year if today.month > 1 else today.year - 1))
    if re.search(r"\blast quarter\b|\bsidast[ai] arsfjord", q):
        quarter = (today.month - 1) // 3
        quarters.add(str(quarter or 4))
        years.add(str(today.year if quarter else today.year - 1))
    for year in years:
        groups.append({c for c in var.values if c[:4] == year})
    if months:
        for month in months:
            # A yearly table can explicitly report a fixed month, e.g. beneficiaries in December.
            fixed = "M" not in var.values[-1] and "Q" not in var.values[-1] and _MONTHS[month - 1].search(normalize(meta.title))
            groups.append({c for c in var.values if (fixed or c[4:] == f"M{month:02d}") and (not years or c[:4] in years)})
            if not fixed:
                for mention in _MONTHS[month - 1].finditer(q):
                    after = re.match(rf"[\s.,-]*(?:in\s+|arid\s+)?({_YEAR_NUMBER})\b", q[mention.end():])
                    before = re.search(rf"\b({_YEAR_NUMBER})[\s.,-]*$", q[:mention.start()])
                    for match in (after, before):
                        if match:
                            groups.append({f"{match[1]}M{month:02d}"} & set(var.values))
        if not any("M" in c for c in var.values):
            if not all(_MONTHS[m - 1].search(normalize(meta.title)) for m in months):
                groups.append(set())
    for quarter in quarters:
        groups.append({c for c in var.values if c[4:] == f"Q{quarter}" and (not years or c[:4] in years)})
        for mention in re.finditer(rf"\bq{quarter}\b", q):
            after = re.match(rf"[\s.,-]*({_YEAR_NUMBER})\b", q[mention.end():])
            before = re.search(rf"\b({_YEAR_NUMBER})[\s.,-]*$", q[:mention.start()])
            for match in (after, before):
                if match:
                    groups.append({f"{match[1]}Q{quarter}"} & set(var.values))
    if any(not group for group in groups):
        raise AnswerFailure("unavailable", "requested_period_unavailable", f"{meta.id}: {question}")
    return groups


def validate_filters(meta: TableMeta, selected: dict[str, list[str]], requirements: list[RequestedFilter],
                     question: str, *, quoted_question: str = "",
                     defaults: dict[str, list[str]] | None = None,
                     covered_scopes: set[str] | None = None) -> None:
    """Validate both extracted requirements and clear literal mentions independently of model defaults."""
    quoted = " ".join(tokens(quoted_question or question))
    for req in requirements:
        mention = " ".join(tokens(req.mention))
        if mention in (covered_scopes or set()) and (req.dimension not in selected or not req.values):
            # An independently verified table/dataset definition needs no
            # synthetic filter. Explicit contradictory codes still fail below.
            continue
        if not mention or f" {mention} " not in f" {quoted} ":
            # Models sometimes put policy defaults in this explicit-filter manifest.
            # Accept only a recognizable default whose exact codes match both the
            # resolver's policy and the executed selection. Literal checks below
            # still protect dates/categories the user actually requested.
            var = next((v for v in meta.variables if v.code == req.dimension), None)
            default = (defaults or {}).get(req.dimension, [])
            aliases = ({"latest", "latest period", "latest available", "latest available period", "most recent",
                        "most recent period", "nyjasta", "nyjasta timabil"} if var and var.time else
                       {"total", "all", "alls", "samtals"})
            if not (var and mention in aliases and default and set(req.values) == set(default)
                    and set(selected.get(req.dimension, [])) == set(default)):
                raise AnswerFailure("unverified", "ungrounded_filter", req.mention)
        if req.dimension not in selected or not req.values:
            raise AnswerFailure("unavailable", "requested_filter_unavailable", req.mention)
        var = meta.var(req.dimension)
        if not set(req.values) <= set(var.values):
            raise AnswerFailure("unverified", "invalid_filter_codes", req.mention)
        if not set(req.values) <= set(selected[req.dimension]):
            raise AnswerFailure("unverified", "requested_filter_changed", req.mention)

    # Only gender aliases here: topic-search expansion would conflate CPI levels and inflation, for example.
    words = [SYNONYMS.get(t, t) if SYNONYMS.get(t) in ("males", "females") else t for t in tokens(question)]
    padded = " " + " ".join(words) + " "
    for var in meta.variables:
        if var.time:
            groups = period_requirements(meta, var, question)
            if any(not group.intersection(selected[var.code]) for group in groups):
                raise AnswerFailure("unverified", "requested_period_changed", question)
            continue
        # "in Iceland" is a geography, not a request to select Icelandic citizenship or birthplace.
        origin = re.search(r"citizen|birth|origin|rikisfang|faedingarland|uppruni", normalize(var.text))
        explicit_origin = re.search(r"citizen|born|birth|origin|rikisborg|faedd|faeding|uppruna", normalize(question))
        mentions = []
        geography = re.search(r"municipal|region|area|town|sveitarfel|landshlut|byggd", normalize(var.text))
        for code, label in zip(var.values, var.value_texts):
            label = " ".join(tokens(label))
            if len(label) < 4 or re.fullmatch(r"total|all|alls|samtals|value|index", label):
                continue
            if origin and not explicit_origin and label in ("iceland", "island"):
                continue
            place_prefix = geography and " " not in label and any(
                len(word) >= 5 and (label.startswith(word) or word.startswith(label)) for word in words)
            if f" {label} " in padded or place_prefix:
                mentions.append((code, label))
        # A mention of "net income" should not also constrain a separate "income" option.
        required = [code for code, label in mentions if not any(
            label != other and f" {label} " in f" {other} " for _, other in mentions)]
        if not set(required) <= set(selected[var.code]):
            raise AnswerFailure("unverified", "requested_filter_changed", var.text)
