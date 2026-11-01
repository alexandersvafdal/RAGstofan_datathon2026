"""Step 1: split the question into lookups that each need one table."""

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from ..llm import LLM
from ..specification import QuestionSpec


class SubQuestion(BaseModel):
    question: str = Field(description="A self-contained question answerable from a single table, in English")
    search_query: str = Field(description="English keywords for finding the table: topic, measure, place, period")
    specification: QuestionSpec


class Plan(BaseModel):
    outcome: Literal["lookup", "clarify", "out_of_scope"] = "lookup"
    sub_questions: list[SubQuestion] = Field(default_factory=list, max_length=3)

    @model_validator(mode="after")
    def valid_outcome(self):
        if (self.outcome == "lookup") != bool(self.sub_questions):
            raise ValueError("lookup requires sub-questions; clarify/out_of_scope require an empty list")
        return self


SYSTEM = """You plan lookups in Statistics Iceland's official statistics tables.
First decide whether the WHOLE request is supported. Use outcome=lookup for statistical lookups, series,
differences, percentage changes, shares, rankings and ratios to population using these tables.
Use outcome=clarify if a measure or population has materially different interpretations (e.g. 'how rich are old
people': income or net wealth, and which age group?). Return no sub_questions for clarify.
Use outcome=out_of_scope for personal/private records, general non-statistical tasks, causal explanations or
predictions that cannot be read from a published forecast. Return no sub_questions for out_of_scope.
Unknown coverage or an unavailable requested period is NOT out_of_scope: use lookup and let metadata validation decide.
Never drop a requested part to make a question answerable. If the request needs an unsupported operation or more
than three distinct lookups, use out_of_scope for the whole request.
Split the user's question into the smallest number of sub-questions (1 to 3) where each can be answered from ONE table.
Most questions need exactly one. Only split when the question asks about clearly different topics
(for example population AND unemployment).
Never split a comparison within one measure: "how many more men than women", "Reykjavík vs Kópavogur",
"share of foreigners", "change from 2025 to 2026" are each ONE sub-question, because one table holds both sides.
Write sub-questions and search queries in English even if the user wrote Icelandic.
Keep place names in their Icelandic spelling (Reykjavík, Kópavogur, Akureyri).
For each part, write its structured specification BEFORE considering any candidate table. Include exactly one
measure and every requested definition/filter: statistic (mean/median/total/rate/index), population, geography,
period, unit, frequency, adjustment, price basis (current/constant prices), tax basis (gross/net).
Each requirement records its origin and an exact quote from the original question (user) or supplied context.
Translate value to canonical English, but do not translate the supporting quote. Never invent a required filter.
Allowed policy defaults only: geography=Iceland, period=latest, adjustment=unadjusted, statistic=total.
Defaults have origin=default and quote="". A mean, median, income measure, wealth measure or age threshold is never
a default: clarify if the wording does not determine it. Record any unresolved ambiguities instead of guessing.
Use operation=value for a published annual rate such as inflation or GDP growth; difference/pct_change are for
explicit comparisons, series for trends, rank for category listings. Do not omit a requirement when translating.
Icelandic words that are easy to mistranslate:
- búa, íbúar, mannfjöldi, hvað búa margir: population (people living somewhere)
- fjölga, fjölgaði, fjölgun: population increase; fækka, fækkaði: population decrease
- atvinnuleysi, atvinnuleysið: unemployment rate (not employment)
- atvinnuþátttaka: activity rate; starfandi: employed
- verðbólga: inflation (annual % change of the consumer price index); vísitala neysluverðs: consumer price index
- hagvöxtur: economic growth (GDP volume change); landsframleiðsla: GDP
- verg landsframleiðsla: gross domestic product at current prices ("verg" = gross, NOT real); á föstu verðlagi, raunvirði: at constant prices
- verðmæti: value in ISK (not a price index); útflutningur, útflutt: exports
- gistinætur: overnight stays; farþegar: passengers; fólksbílar: passenger cars; sjávarafurðir: marine products
- laun: wages; innflutningur: imports; útflutningur: exports
Do not answer the question."""


async def plan(llm: LLM, question: str, *, context: str = "") -> Plan:
    user = f"Question: {question}" + (f"\nContext supplied by the user: {context}" if context else "")
    return await llm.complete_json(SYSTEM, user, Plan)
