"""Step 7: write the answer from the facts, then check it did not invent numbers."""

import re

from pydantic import BaseModel

from ..llm import LLM
from ..schemas import AnswerPart
from ..text import numbers_in


class Written(BaseModel):
    text: str


SYSTEM = {
    "en": "You write short answers (1–3 sentences) about official statistics in English.",
    "is": "Þú skrifar stutt svör (1–3 setningar) um opinbera tölfræði á íslensku.",
}
RULES = """Use ONLY the facts given, with numbers written exactly as formatted, including units.
Say which period the numbers are for. Name the source as Statistics Iceland (Hagstofa Íslands) and the table ID.
If facts come from different tables or dates, say so. Do not add numbers that are not in the facts."""


def _facts_block(parts: list[AnswerPart]) -> str:
    lines = []
    for p in parts:
        lines.append(f"Table {p.table.id}: {p.table.title}")
        lines += [f"  - {f.label}: {f.formatted}{' ' + f.unit if f.unit not in ('', '%') else ''}" for f in p.facts]
    return "\n".join(lines)


def _allowed_numbers(parts: list[AnswerPart]) -> set[float]:
    allowed: set[float] = set()
    for p in parts:
        texts = [p.table.id, p.table.title] + [f.label for f in p.facts] + [f.formatted for f in p.facts]
        texts += [v for vals in p.selection.values() for v in vals]
        for t in texts:
            allowed.update(numbers_in(t))
        for f in p.facts:
            allowed.update({f.value, round(f.value, 1), round(f.value), abs(round(f.value, 1))})
    return allowed


def verify(text: str, parts: list[AnswerPart]) -> bool:
    allowed = _allowed_numbers(parts)
    return all(any(abs(n - a) < 0.051 for a in allowed) for n in numbers_in(text))


_PERIOD = re.compile(r"\b(?:18|19|20)\d\d(?:M\d\d|Q\d)?\b")


def _series_sentence(p: AnswerPart, lang: str) -> str | None:
    """A trend in one sentence per line: where it started, where it is now, and its highest point."""
    periods = [_PERIOD.search(f.label) for f in p.facts]
    if len(p.facts) <= 3 or not all(periods):
        return None
    lines: dict[str, list] = {}  # one line per series: the label without its period
    for f, m in zip(p.facts, periods):
        lines.setdefault(re.sub(r"(\s*,)+", ",", _PERIOD.sub("", f.label)).strip(" ,"), []).append((m.group(0), f))
    out = []
    for measure, pts in lines.items():
        (p0, first), (p1, last) = pts[0], pts[-1]
        pk, peak = max(pts, key=lambda x: x[1].value)
        if lang == "is":
            out.append(f"{measure}: {first.formatted} ({p0}), {last.formatted} ({p1}); hæst {peak.formatted} ({pk})")
        else:
            out.append(f"{measure}: {first.formatted} in {p0}, {last.formatted} in {p1}; highest {peak.formatted} in {pk}")
    return ". ".join(out)


def template(parts: list[AnswerPart], lang: str) -> str:
    src = "Heimild: Hagstofa Íslands" if lang == "is" else "Source: Statistics Iceland"
    out = []
    for p in parts:
        facts = _series_sentence(p, lang) or "; ".join(f"{f.label}: {f.formatted}" for f in p.facts[-3:])
        out.append(f"{facts}. {src}, {p.table.id}.")
    return " ".join(out)


async def compose(llm: LLM, question: str, parts: list[AnswerPart], lang: str) -> tuple[str, str]:
    """Returns (text, "model" | "template"). The template only restates facts, so both are number-checked."""
    user = f"Question: {question}\n\nFacts:\n{_facts_block(parts)}"
    try:
        written = await llm.complete_json(SYSTEM[lang] + "\n" + RULES, user, Written, retries=1)
        text = re.sub(r"\s+", " ", written.text).strip()
        # the model sometimes drifts into another script mid-sentence (seen: Chinese inside Icelandic)
        if verify(text, parts) and not re.search(r"[\u0400-\u04ff\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]", text):
            return text, "model"
    except Exception:
        pass
    return template(parts, lang), "template"
