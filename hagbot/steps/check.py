"""After the answer is written: does it answer the question? A second reading by the model, with the question, the
table used, every option chosen, the unit and the sentence. Two things are judged: the table is on the subject asked,
and the sentence answers the question as put (its measure, its place, its period). When either fails, the user is
told that no answer was found and that this is the closest, rather than a confident wrong figure."""

from pydantic import BaseModel, Field

from ..llm import LLM
from ..schemas import AnswerPart

SYSTEM = """You check an answer written from a Statistics Iceland table against the question it was meant to answer.
You see the question, the table used (title and the option chosen in every dimension), the figures read, and the answer.
Judge two things, strictly on what is shown:
1. table_fits: the table is about the subject the question asks about (prisoners, not religious registrations;
   marine exports by country, not goods by commodity). A table on the subject that merely lacks a breakdown still fits.
2. answers: the answer gives what was asked, for the measure asked (a count or tonnes when "how much / how many",
   a value when "value"), for the place and the group asked, and for the period asked when one was named. An answer
   that says the figures do not contain what was asked, or that gives a neighbouring period without being asked for
   it, does not answer. The latest period is right when the question names none; a month or year the table lacks,
   reported as such with the latest figure, is acceptable only when the answer says so plainly.
The figures read are listed by their labels only, a sample of both ends of a long series; the option lists give the full
range. Every number in the answer was checked against the table in code already: do not judge the numbers, only
whether the subject, measure, place and period match the question. Give a short reason."""


class Verdict(BaseModel):
    table_fits: bool = Field(description="the table used is on the subject asked")
    answers: bool = Field(description="the answer gives what the question asked, for the measure, place and period asked")
    reason: str = Field(default="", description="a few words on what is wrong, or 'fits'")


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
            out.append(f"  computed: {p.computation.expression}")
        # what was read, by label only (period, category, measure): the numbers were checked in code already, and
        # shown here they tempt the model into re-checking the arithmetic against a partial list
        facts = p.facts if len(p.facts) <= 8 else [*p.facts[:4], None, *p.facts[-4:]]   # both ends of a long series
        out += [f"  - {f.label}" if f else f"  - … ({len(p.facts)} figures in all)" for f in facts]
    return "\n".join(out)


async def check_answer(llm: LLM, question: str, parts: list[AnswerPart], text: str) -> Verdict:
    user = f"Question: {question}\n\nUsed:\n{_shown(parts)}\n\nAnswer: {text}"
    return await llm.complete_json(SYSTEM, user, Verdict, retries=1)
