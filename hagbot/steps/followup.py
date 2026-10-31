"""Follow-up questions: "what about last year?", "and in Reykjavík?", "hvað með árið áður?".

A follow-up is rewritten into a question that stands on its own, using the previous question and answer, before the
normal pipeline runs. The model is asked only when the wording suggests a follow-up, so new questions cost nothing
extra and are never changed.
"""

import re

from pydantic import BaseModel, Field

from ..catalog import STOPWORDS
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
# Words that say how to look at something but not what: a question made only of these (and stopwords, years, months)
# has no subject of its own, e.g. "how has the trend been the last few years?" or "hvernig hefur þróunin verið?"
_NO_SUBJECT = re.compile(
    r"(trend|been|last|few|years?|recent|recently|past|over|time|chang|develop|evolv|going|look|like|data|numbers?|"
    r"figures?|show|tell|me|about|it|details?|compar|breakdown|since|then|before|ago|per|month|monthly|quarter|"
    r"throun|throast|sidustu|undanfarin|ar|arin|ara|arum|breyst|breyting|tolur|tolurnar|gogn|syndu|segdu|mer|"
    r"verid|hefur|hafa|hvernig|manud|manadarlega|arsfjord|sidan|adur)\w*$")
_MONTH_OR_YEAR = re.compile(r"^((18|19|20)\d\d|jan\w*|feb\w*|mar\w*|apr\w*|may|mai|jun\w*|jul\w*|aug\w*|agu\w*|"
                            r"sep\w*|oct\w*|okt\w*|nov\w*|dec\w*|des\w*)$")


# "Put them in the same chart", "gefðu mér þessi tvö rit í sömu mynd": how to show the previous answer, not new data.
# The previous question is asked again as it was; an answer with series from two tables draws them on one chart.
_DISPLAY = re.compile(r"\b(same|one|single|a single) (chart|graph|figure|plot|picture)|\bside by side|\btogether\b|"
                      r"\bsomu (mynd|grafi|riti)|\bsama (graf|rit)|\beinu (riti|grafi)|\beinni mynd|\bsaman a (einu|einni)")

_DISPLAY_WORDS = re.compile(r"(same|one|single|chart|graph|figure|plot|picture|side|together|both|them|put|draw|combin|"
                            r"these|those|two|can|could|you|please|somu|mynd|graf|rit|sama|einu|einni|saman|baed|"
                            r"thessi|thett|thad|thau|tvo|tvau|gefdu|settu|sameina|teikn|vinsamlegast)\w*$")


def display_only(question: str) -> bool:
    """A request to show the previous answer differently that names nothing new ("put them in one chart"); "add GDP
    to the same chart" names something, so it is rewritten like any follow-up."""
    if not _DISPLAY.search(normalize(question)):
        return False
    return not [t for t in tokens(question) if t not in STOPWORDS and not _NO_SUBJECT.match(t)
                and not _DISPLAY_WORDS.match(t)]


class Rewritten(BaseModel):
    question: str = Field(description="the follow-up as a complete question that needs no earlier context")


SYSTEM = """You rewrite a follow-up question so that it can be understood without the conversation before it.
Keep the user's language (English or Icelandic). Take the topic, place, measure and table subject from the previous
question and answer; change only what the follow-up changes (a year, a month, a place, a group).
Keep the previous question's period ("the last 10 years", "since 2016", "síðustu 10 ár") unless the follow-up changes it.
Name the measure in words; never write table ids in the question.
"Last year" / "í fyrra" / "the year before" means the year before the period in the previous answer.
If the question is already complete on its own, return it unchanged. Do not answer it."""


def looks_like_followup(question: str, history: list[Turn]) -> bool:
    if not history:
        return False
    q = normalize(question).strip()
    subject = [t for t in tokens(question)
               if t not in STOPWORDS and not _NO_SUBJECT.match(t) and not _MONTH_OR_YEAR.match(t)]
    return len(tokens(question)) <= _MAX_WORDS or bool(_FOLLOW.search(q)) or not subject or display_only(question)


async def rewrite(llm: LLM, question: str, history: list[Turn]) -> str:
    last = history[-1]
    if display_only(question):
        return last.question
    context = (f"Previous question: {last.question}\nPrevious answer: {last.answer}"
               + (f" (tables: {', '.join(last.tables)})" if last.tables else ""))
    out = await llm.complete_json(SYSTEM, f"{context}\n\nFollow-up: {question}", Rewritten)
    return out.question.strip() or question
