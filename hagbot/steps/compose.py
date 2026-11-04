"""Step 7: write the answer from the facts, then check it did not invent numbers."""

import ast
import operator
import re
from datetime import date

from pydantic import BaseModel, Field

from ..config import settings
from ..llm import LLM
from ..schemas import AnswerPart
from ..text import fmt_number, numbers_in


class Written(BaseModel):
    text: str


class WrittenCalc(Written):
    calculations: list[str] = Field(default_factory=list, description="the arithmetic behind every number in the text "
                                    "that is not in the facts, one per number, written plainly: [\"138753 / 648\"]; "
                                    "empty when none")


SYSTEM = {
    "en": "You write short answers (1–3 sentences) about official statistics in English.",
    "is": "Þú skrifar stutt svör (1–3 setningar) um opinbera tölfræði á íslensku.",
}
RULES = """Use ONLY the facts given, with numbers written exactly as formatted, including units.
Say which period the numbers are for. Name the source as Statistics Iceland (Hagstofa Íslands) and the table ID.
If facts come from different tables or dates, say so. Do not add numbers that are not in the facts.
Do not call a figure a projection, forecast or estimate unless the table title says so: these are recorded statistics."""
CALC = """If the question asks for a number the facts do not give directly but that follows from them (how many times one
fits in another, a ratio, a sum, a difference, a figure per inhabitant), write the arithmetic in `calculations`,
one per number you give (138753 / 648; a gap in two years is two: 3834 - 2359 and 8227 - 6061), and give the
results in the text, rounded sensibly. The working is shown under the answer. Otherwise leave `calculations` empty."""

_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv}


def _evaluate(expr: str) -> float | None:
    """The model's arithmetic worked out in code: + − × ÷ and brackets on plain numbers."""
    def ev(n):
        if isinstance(n, ast.BinOp) and type(n.op) in _OPS:
            return _OPS[type(n.op)](ev(n.left), ev(n.right))
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, ast.USub):
            return -ev(n.operand)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return float(n.value)
        raise ValueError("not arithmetic")
    try:
        return ev(ast.parse(_plain(expr), mode="eval").body)
    except Exception:
        return None


def _plain(expr: str) -> str:
    return expr.replace("×", "*").replace("÷", "/").replace("−", "-").replace(",", "")


def _working(expr: str, result: float, lang: str) -> str:
    """"138.753 ÷ 648 = 214,1", the numbers in the answer's own format."""
    shown = re.sub(r"\d+(?:\.\d+)?", lambda m: fmt_number(float(m.group(0)), lang), _plain(expr))
    shown = re.sub(r"(?<=[\d)])\s*([*/+-])\s*", r" \1 ", shown)   # "138753/648": spaced like 138,753 ÷ 648
    shown = shown.replace("*", "×").replace("/", "÷").replace("-", "−")
    return f"{shown} = {fmt_number(result, lang, None if abs(result) >= 100 else 2)}"


def _result_numbers(x: float) -> set[float]:
    """The ways the result may be written: 214.12…, 214.1, 214, in either locale."""
    out = {x, abs(x)}
    for d in (0, 1, 2):
        out.add(round(x, d))
        for lang in ("en", "is"):
            out.update(numbers_in(fmt_number(x, lang, d)))
    return out


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


_SCALE = re.compile(r"\b(thousand|million|billion|trillion|þúsund|milljón|milljónir|milljarð|milljarðar|bn|mn)\w*", re.I)


def unverified(text: str, parts: list[AnswerPart], extra: set[float] = frozenset()) -> list[str]:
    """What fails the number check: numbers in the text that are not in the facts (or `extra`, the calculations'
    numbers and results), or a scale word ("10.254 milljónir") the facts do not have, which turns a right number
    wrong. Empty: the text passes."""
    allowed = _allowed_numbers(parts) | set(extra)
    bad = [m for m in re.findall(r"[-−]?\d[\d.,]*", text)
           if not all(any(abs(n - a) < 0.051 for a in allowed) for n in numbers_in(m))]
    facts_text = " ".join(f"{p.table.title} {p.unit} {f.label} {f.unit}" for p in parts for f in p.facts)
    if (m := _SCALE.search(text)) and not _SCALE.search(facts_text):
        bad.append(m.group(0))
    return bad


def verify(text: str, parts: list[AnswerPart], extra: set[float] = frozenset()) -> bool:
    return not unverified(text, parts, extra)


_PERIOD = re.compile(r"\b(?:18|19|20)\d\d(?:M\d\d|Q\d)?\b")


def _series_sentence(p: AnswerPart, lang: str) -> str | None:
    """A trend in one sentence per line: where it started, where it is now, and its highest point."""
    facts = p.facts[:-1] if p.computation else p.facts   # the calculated change is said on its own, below
    periods = [_PERIOD.search(f.label) for f in facts]
    if len(facts) <= 3 or not all(periods) or len({m.group(0) for m in periods}) < 2:
        return None   # few facts, or categories at one period (a list, not a trend)
    lines: dict[str, list] = {}  # one line per series: the label without its period
    for f, m in zip(facts, periods):
        lines.setdefault(re.sub(r"(\s*,)+", ",", _PERIOD.sub("", f.label)).strip(" ,"), []).append((m.group(0), f))
    out = []
    for measure, pts in lines.items():
        # a line that starts at exactly 0 and has values later did not exist yet: it starts where its values do
        start = next((i for i, (_, f) in enumerate(pts) if f.value), 0)
        pts = pts[start:]
        (p0, first), (p1, last) = pts[0], pts[-1]
        pk, peak = max(pts, key=lambda x: x[1].value)
        if lang == "is":
            out.append(f"{measure}: {first.formatted} ({p0}), {last.formatted} ({p1}); hæst {peak.formatted} ({pk})")
        else:
            out.append(f"{measure}: {first.formatted} in {p0}, {last.formatted} in {p1}; highest {peak.formatted} in {pk}")
    if p.computation:
        out.append(f"{p.facts[-1].label}: {p.facts[-1].formatted}")
    return ". ".join(out)


def template(parts: list[AnswerPart], lang: str) -> str:
    src = "Heimild: Hagstofa Íslands" if lang == "is" else "Source: Statistics Iceland"
    out = []
    for p in parts:
        if p.computation and p.computation.op == "ratio":  # "…, per inhabitant: 25.3 (9,850,428 / 389,444)"
            r = p.facts[-1]
            out.append(f"{r.label}: {r.formatted} ({p.computation.expression}). {src}, {p.table.id}.")
            continue
        listed = p.facts if len(p.facts) <= 15 and not p.computation else p.facts[-3:]   # a category list: all of it
        facts = _series_sentence(p, lang) or "; ".join(f"{f.label}: {f.formatted}" for f in listed)
        out.append(f"{facts}. {src}, {p.table.id}.")
    return " ".join(out)


async def compose(llm: LLM, question: str, parts: list[AnswerPart], lang: str) -> tuple[str, str, str]:
    """Returns (text, "model" | "template", a note: the calculations done, or why the model's text was not used). The
    template only restates facts, so both are number-checked; the numbers of each calculation and its result (worked
    out in code) are allowed, and the working is added to the text."""
    # a long series: hand the model its endpoints and peak, or it tends to describe only part of the period
    summary = [x for x in (_series_sentence(p, lang) for p in parts) if x]
    user = f"Question: {question}\n\nFacts:\n{_facts_block(parts)}" + \
           (f"\n\nSummary of the series (cover the whole period):\n" + "\n".join(summary) if summary else "")
    try:
        # the model's training ends before the data does, so 2026 looks like the future to it ("projected to reach")
        today = f"Today is {date.today().isoformat()}. Every period up to today is in the past: the figures are recorded, not forecast."
        rules = RULES + ("\n" + CALC if settings.llm_calc else "")
        written = await llm.complete_json(SYSTEM[lang] + "\n" + rules + "\n" + today, user,
                                          WrittenCalc if settings.llm_calc else Written, retries=1)
        text = re.sub(r"\s+", " ", written.text).strip()
        extra, done, workings = set(), [], []
        for calc in (c.strip() for c in getattr(written, "calculations", []) if c.strip()):
            if (result := _evaluate(calc)) is None:
                done.append(f"{calc} (not arithmetic)")
                continue
            extra |= _result_numbers(result) | {n for t in numbers_in(_plain(calc)) for n in _result_numbers(t)}
            workings.append(_working(calc, result, lang))
            done.append(f"{calc} = {fmt_number(result, lang, 2)}")
        if workings:
            text = f"{text} {'Útreikningur' if lang == 'is' else 'Calculation'}: {'; '.join(workings)}."
        note = "calculations " + "; ".join(done) if done else ""
        bad = unverified(text, parts, extra)
        # the model sometimes drifts into another script mid-sentence (seen: Chinese inside Icelandic)
        script = re.search(r"[\u0400-\u04ff\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]", text)
        if not bad and not script:
            return text, "model", note
        why = f"numbers not in the facts {bad}" if bad else "another script"
        note = "; ".join(x for x in (note, f"model text not used ({why}): {text}") if x)
    except Exception as e:
        note = f"model text not used ({type(e).__name__}: {e})"
    return template(parts, lang), "template", note
