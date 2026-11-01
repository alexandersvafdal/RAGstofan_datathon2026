"""Compose from bound claims; numbers, labels, units and periods stay together."""

from pydantic import BaseModel

from ..claims import build_claims, render_claims
from ..schemas import AnswerPart
from ..outcomes import AnswerFailure


class Written(BaseModel):
    """Legacy writer schema retained for import compatibility."""
    text: str


def template(parts: list[AnswerPart], lang: str) -> str:
    return render_claims(build_claims(parts, lang), parts, lang)


def verify(text: str, parts: list[AnswerPart]) -> bool:
    """Matching numbers alone cannot certify unrestricted prose."""
    try:
        return any(text == template(parts, language) for language in ("en", "is"))
    except AnswerFailure:
        return False


async def compose(llm, question: str, parts: list[AnswerPart], lang: str, *, claims=None) -> tuple[str, str]:
    # No writer-model call: a plausible sentence can attach a correct number
    # to the wrong statistical definition, population, period or unit.
    return render_claims(claims if claims is not None else build_claims(parts, lang), parts, lang), "template"
