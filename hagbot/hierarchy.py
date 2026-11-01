"""Category hierarchies read from the labels themselves, for any table.

Statistics Iceland writes the classification code at the start of a category's label: COICOP "01 Food and
non-alcoholic beverages" > "011 Food" > "0111 Cereals", NACE "A - Agriculture" > "A: 01 & 02 - Crop and animal
production". The code gives the level and, by prefix, the children. Nothing here names a table.
"""

import re

from .sources import Variable
from .text import normalize

# a leading code: digits ("01", "0111"), a letter or letter span ("A", "B-C"), optionally ": 01 & 02" under a letter
_CODE = re.compile(r"^\s*(\d{2,}|[A-Z](?:\s*[-–]\s*[A-Z])?(?::\s*[\d.&,\s]+?)?)(?=\s*[-–:]\s|\s+\S)")
_TOTAL = re.compile(r"^(total|alls|samtals|all|cpi|vnv)\b", re.I)


def code_of(label: str) -> str | None:
    m = _CODE.match(label)
    return re.sub(r"\s+", "", m.group(1)) if m else None


def level(code: str) -> int:
    """Digits: their count ("01" is 2, "011" is 3). Letters: 1, or 2 under a letter ("A:01&02")."""
    return len(code) if code.isdigit() else 2 if ":" in code else 1


def coded(var: Variable) -> dict[str, str]:
    """Value code -> classification code, when most of the dimension's categories carry one; else empty."""
    codes = {v: c for v, t in zip(var.values, var.value_texts) if (c := code_of(t))}
    rest = [t for t in var.value_texts if not _TOTAL.match(t)]
    return codes if len(codes) >= 4 and len(codes) >= 0.6 * max(len(rest), 1) else {}


def _is_child(parent: str, child: str) -> bool:
    if parent.isdigit():
        return child.isdigit() and child.startswith(parent) and len(child) == len(parent) + 1
    return ":" in child and child.split(":")[0] == parent


def children(var: Variable, parent: str | None) -> list[str]:
    """The value codes one level below `parent` (a value code), or the top level when parent is None, in table order.
    Letter spans that sum other sections ("B-C") are left out of the top level so nothing is counted twice."""
    codes = coded(var)
    if not codes:
        return []
    if parent is None:
        top = min(level(c) for c in codes.values())
        return [v for v, c in codes.items() if level(c) == top and "-" not in c]
    pc = codes.get(parent)
    if not pc:
        return []
    return [v for v, c in codes.items() if _is_child(pc, c)]


def named(var: Variable, question: str) -> str | None:
    """The category of this dimension the question names, by its full label (as a drill-down asks) or by its words;
    the longest match wins, so "011 Food" does not shadow "01 Food and non-alcoholic beverages"."""
    q = normalize(question)
    best, size = None, 0
    for v, t in zip(var.values, var.value_texts):
        if not code_of(t):
            continue
        name = normalize(re.sub(_CODE, "", t).strip(" -–:"))
        full = normalize(t)
        for cand in (full, name):
            if len(cand) >= 4 and cand in q and len(cand) > size:
                best, size = v, len(cand)
    return best
