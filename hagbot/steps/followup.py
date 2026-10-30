"""Follow-up questions: "what about last year?", "and in Reykjavík?", "hvað með árið áður?".

A follow-up is rewritten into a question that stands on its own, using the previous question and answer, before the
normal pipeline runs. The model is asked only when the wording suggests a follow-up, so new questions cost nothing
extra and are never changed.
"""

import re

from pydantic import BaseModel, Field

from ..llm import LLM
from ..schemas import Turn
from ..text import normalize, tokens

# Wording that leans on what was asked before: openers, pronouns, and periods relative to an earlier answer
_FOLLOW = re.compile(
    r"^(what about|how about|and |and\b|same |also |but |then |in that case|"
    r"hvad med|en |og |sama |lika |einnig )|\b(last year|the year before|previous year|year before|that year|the same|it|that|those|them|"
    r"i fyrra|arid adur|arinu adur|sama ar|thad|thar|theim|thvi|thessu|thessi)\b"
)
_MAX_WORDS = 3  # "In 2024?", "Og konur?": too short to stand on its own


class Rewritten(BaseModel):
    question: str = Field(description="the follow-up as a complete question that needs no earlier context")


SYSTEM = """You rewrite a follow-up question so that it can be understood without the conversation before it.
Keep the user's language (English or Icelandic). Take the topic, place, measure and table subject from the previous
question and answer; change only what the follow-up changes (a year, a month, a place, a group).
"Last year" / "í fyrra" / "the year before" means the year before the period in the previous answer.
If the question is already complete on its own, return it unchanged. Do not answer it."""


def looks_like_followup(question: str, history: list[Turn]) -> bool:
    if not history:
        return False
    q = normalize(question).strip()
    return len(tokens(question)) <= _MAX_WORDS or bool(_FOLLOW.search(q))


async def rewrite(llm: LLM, question: str, history: list[Turn]) -> str:
    last = history[-1]
    context = (f"Previous question: {last.question}\nPrevious answer: {last.answer}"
               + (f" (tables: {', '.join(last.tables)})" if last.tables else ""))
    out = await llm.complete_json(SYSTEM, f"{context}\n\nFollow-up: {question}", Rewritten)
    return out.question.strip() or question
