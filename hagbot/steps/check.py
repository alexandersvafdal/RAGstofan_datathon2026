"""After the answer is written: does it answer the question? A second reading by the model, with the question, the
table used, every option chosen, the unit and the sentence. Three outcomes: it answers (the text stands), it is
uncertain (the text opens with a qualification and the reason), or it is wrong (the text opens by saying no answer
was found and that what follows is connected)."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from ..llm import LLM
from ..text import fmt_number
from ..schemas import AnswerPart

SYSTEM = """You check an answer written from a Statistics Iceland table against the question it was meant to answer.
You see the question, the table used with the option chosen in every dimension, the labels of the figures read, and
the answer. Say which one holds:
- answers: the answer gives what the question asks, for the subject, the measure, the place and the period asked.
  The text is short on purpose; a trend asked for is shown by the chart, the text need only give its ends.
  "Today", "now", "latest" and a question naming no period are answered by the latest period the table has.
- uncertain: it is on the subject but may not match exactly: a related measure, a neighbouring period or group, a
  broader or narrower table than asked.
- wrong: it is about something else than the question asks.
Every number in the answer was already checked against the table; judge the match, not the arithmetic. Give a short
reason."""


class Verdict(BaseModel):
    fit: Literal["answers", "uncertain", "wrong"] = Field(description="how the answer matches the question")
    reason: str = Field(default="", description="a few words on why")


def _shown(parts: list[AnswerPart]) -> str:
    out = []
    for p in parts:
        out.append(f"Table {p.table.id}: {p.table.title} (data to {p.table.updated})" if p.table.updated else f"Table {p.table.id}: {p.table.title}")
        for dim, labels in p.selection.items():
            # a long list shows both ends, so the period range is read right ("2025M09 … 2026M09, 13 in all")
            shown = ", ".join(labels) if len(labels) <= 6 else f"{', '.join(labels[:3])} … {', '.join(labels[-3:])} ({len(labels)} in all)"
            out.append(f"  {dim}: {shown}")
        if p.unit:
            out.append(f"  unit: {p.unit}")
        if p.computation:
            c = p.computation   # the result too, in its unit: the checker judges the match, it need not redo the sum
            out.append(f"  computed in code: {c.expression} = {fmt_number(c.result, 'en')}{' ' + c.unit if c.unit else ''}")
        if p.chart:
            out.append("  a chart of every figure read is shown with the answer")
        # what was read, by label only (period, category, measure): the numbers were checked in code already, and
        # shown here they tempt the model into re-checking the arithmetic against a partial list
        facts = p.facts if len(p.facts) <= 8 else [*p.facts[:4], None, *p.facts[-4:]]   # both ends of a long series
        out += [f"  - {f.label}" if f else f"  - … ({len(p.facts)} figures in all)" for f in facts]
    return "\n".join(out)


async def check_answer(llm: LLM, question: str, parts: list[AnswerPart], text: str) -> Verdict:
    # the model's training ends before the data does: without the date, 2026 reads as the future and "today" as unmet
    today = f"Today is {date.today().isoformat()}; every period up to today is recorded, and the latest one is 'today's' figure."
    user = f"{today}\n\nQuestion: {question}\n\nUsed:\n{_shown(parts)}\n\nAnswer: {text}"
    return await llm.complete_json(SYSTEM, user, Verdict, retries=1)
