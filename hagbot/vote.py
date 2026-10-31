"""Majority voting on the model's choices. Several votes on the same question, asked in parallel; the most common
answer wins, a tie goes to the first vote (the main model at temperature 0). Only for choices from a fixed set,
where votes can be counted: a table ID, the categories of a dimension, an operation. Off with VOTES=1.

Votes come either from several models (VOTE_MODELS, one vote each: different models make different mistakes) or
from the main model sampled several times (VOTES, the first at temperature 0, the rest warmer).
"""

import asyncio
from collections import Counter
from collections.abc import Callable, Hashable
from typing import TypeVar

from pydantic import BaseModel

from .config import settings
from .llm import LLM, LLMError

T = TypeVar("T", bound=BaseModel)
_clients: dict[str, LLM] = {}


def voters(llm: LLM) -> list[tuple[LLM, float]]:
    """(client, temperature) for each vote."""
    models = [m.strip() for m in settings.vote_models.split(",") if m.strip()]
    if models:
        out, seen = [], set()
        for m in models:
            client = llm if m == llm.model else _clients.setdefault(m, LLM(base_url=llm.base_url, model=m))
            out.append((client, settings.vote_temperature if m in seen else 0.0))  # a model listed twice votes warmer
            seen.add(m)
        return out
    return [(llm, 0.0)] + [(llm, settings.vote_temperature)] * (max(settings.votes, 1) - 1)


def voting_on() -> bool:
    return settings.votes > 1 or bool(settings.vote_models.strip())


async def ask_all(llm: LLM, system: str, user: str, schema: type[T]) -> list[T]:
    """Every vote's answer, the failed ones left out (at least one must succeed)."""
    got = await asyncio.gather(*(c.complete_json(system, user, schema, temperature=t) for c, t in voters(llm)),
                               return_exceptions=True)
    ok = [g for g in got if isinstance(g, BaseModel)]
    if not ok:
        raise next((g for g in got if isinstance(g, Exception)), LLMError("no vote succeeded"))
    return ok


def majority(values: list[Hashable]) -> tuple[Hashable, int]:
    """The most common value and its count; a tie goes to the earliest vote."""
    counts = Counter(values)
    best = max(counts.values())
    return next(v for v in values if counts[v] == best), best


async def vote_json(llm: LLM, system: str, user: str, schema: type[T], key: Callable[[T], Hashable]) -> tuple[T, str]:
    """The winning answer and the agreement, e.g. "2/3"."""
    answers = await ask_all(llm, system, user, schema)
    win, n = majority([key(a) for a in answers])
    return next(a for a in answers if key(a) == win), f"{n}/{len(answers)}"
