"""Step 1: split the question into lookups that each need one table."""

import re
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from ..llm import LLM
from ..specification import QuestionSpec
from ..text import normalize


class SubQuestion(BaseModel):
    question: str = Field(description="A self-contained question answerable from a single table, in English")
    search_query: str = Field(description="English keywords for finding the table: topic, measure, place, period")
    specification: QuestionSpec


class Combination(BaseModel):
    operation: Literal["difference", "pct_change", "share"]
    first: int = Field(ge=0, description="Index of the part/numerator or minuend (the value being compared)")
    second: int = Field(ge=0, description="Index of the whole/denominator or subtrahend (the comparison base)")


class Plan(BaseModel):
    outcome: Literal["lookup", "clarify", "out_of_scope"] = "lookup"
    sub_questions: list[SubQuestion] = Field(default_factory=list, max_length=3)
    combination: Combination | None = None

    @model_validator(mode="after")
    def valid_outcome(self):
        if (self.outcome == "lookup") != bool(self.sub_questions):
            raise ValueError("lookup requires sub-questions; clarify/out_of_scope require an empty list")
        if self.combination and (len(self.sub_questions) != 2 or self.combination.first == self.combination.second
                                 or {self.combination.first, self.combination.second} != {0, 1}):
            raise ValueError("a combination requires exactly two distinct sub-question indices, 0 and 1")
        return self


SYSTEM = """You plan lookups in Statistics Iceland's official statistics tables.
First decide whether the WHOLE request is supported. Use outcome=lookup for statistical lookups, series,
differences, percentage changes, sums of named categories, shares, rankings and ratios to population using these tables.
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
If two lookups are operands of one requested calculation, set combination with their explicit indices: first is
the part/minuend, second the whole/base. Difference is first minus second, pct_change is (first-second)/second*100,
share is first/second*100. Never infer operand roles from which number is smaller. Otherwise combination is null.
Each operand sub-question uses operation=value; the cross-table operation belongs to combination.
Never split a comparison within one measure: "how many more men than women", "Reykjavík vs Kópavogur",
"share of foreigners", "share of the population living in Reykjavík", "change from 2025 to 2026" are each ONE
sub-question, because one table holds both sides (a municipality table also has the whole country as a total).
"How many people live in Kópavogur and Hafnarfjörður together?" is a supported lookup: ONE population
sub-question with operation=sum and separate geography requirements for both named towns.
A comparison of two different measures ("the wage index compared with the consumer price index", "launavísitala
borin saman við vísitölu neysluverðs", "exports and imports") is TWO sub-questions, one per measure, over the same
period; without a period, each asks for the development over the last years.
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
explicit comparisons, sum for adding named categories, series for trends, rank for category listings.
Do not omit a requirement when translating.
Icelandic words that are easy to mistranslate:
- búa, íbúar, mannfjöldi, hvað búa margir: population (people living somewhere)
- fjölga, fjölgaði, fjölgun: population increase; fækka, fækkaði: population decrease
- innflytjendur: immigrants (immigrant background, the population-by-origin table), NOT foreign citizens or foreign-born;
  erlendir ríkisborgarar: foreign citizens
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


_PERIOD = (r"\b(?:\d{1,2}\.?\s+)?(?:(?:january|february|march|april|may|june|july|august|september|october|november|december|"
           r"q[1-4]|first quarter|second quarter|third quarter|fourth quarter)\s+)?(?:18|19|20)\d\d\b")


def merge_population_sum(p: Plan, question: str) -> Plan:
    """Coalesce a split population sum only when every other definition agrees.

    This recovers a model's unnecessary split, without changing a scope decision
    or adding unlike statistics/rates. The resulting table must still represent
    every named place and pass the usual metadata/selection checks.
    """
    if (p.outcome != "lookup" or p.combination is not None or len(p.sub_questions) < 2
            or not re.search(r"\b(together|combined|altogether|samtals|samanlagt|til samans)\b", normalize(question))):
        return p
    parts = p.sub_questions
    def definition(part):
        return sorted((r.facet, normalize(r.value), r.origin) for r in part.specification.requirements
                      if r.facet != "geography")
    for part in parts:
        spec = part.specification
        measures = [normalize(r.value) for r in spec.requirements if r.facet == "measure"]
        places = [r for r in spec.requirements if r.facet == "geography"]
        if (measures != ["population"] or spec.operation not in ("value", "sum") or spec.ambiguities
                or not places or any(r.origin == "default" for r in places) or definition(part) != definition(parts[0])):
            return p
        if any((r.facet == "statistic" and normalize(r.value) not in {"total", "count", "number"}) or
               (r.facet == "unit" and normalize(r.value) not in {"persons", "people", "inhabitants"})
               for r in spec.requirements):
            return p
    requirements = {}
    for part in parts:
        for req in part.specification.requirements:
            requirements.setdefault((req.facet, normalize(req.value), req.origin), req)
    spec = parts[0].specification.model_copy(update={"requirements": list(requirements.values()), "operation": "sum"})
    return Plan(sub_questions=[SubQuestion(question=question, search_query=" ".join(dict.fromkeys(
        part.search_query for part in parts)), specification=spec)])


def merge_period_parts(p: Plan, question: str = "") -> Plan:
    """One measure at two periods planned as two parts ("the CPI in September 2025" | "the CPI in September 2026",
    from "um hve mörg stig hækkaði vísitalan frá september 2025 til september 2026") becomes one part asking for
    both, so the change is computed from one table. Parts that differ in anything but the period stay apart."""
    if len(p.sub_questions) != 2 or p.combination is not None:
        return p
    a, b = p.sub_questions
    def definitions(part):
        return sorted((r.facet, r.value.lower(), r.origin) for r in part.specification.requirements if r.facet != "period")
    if definitions(a) != definitions(b) or a.specification.operation != b.specification.operation:
        return p
    pa, pb = re.findall(_PERIOD, a.question, re.I), re.findall(_PERIOD, b.question, re.I)
    if len(pa) != 1 or len(pb) != 1 or pa[0].lower() == pb[0].lower():
        return p
    strip = lambda s: re.sub(r"\s+", " ", re.sub(_PERIOD, "", s, flags=re.I)).strip(" ?.").lower()
    if strip(a.question) != strip(b.question):
        return p
    first, second = (pa[0], pb[0]) if pa[0][-4:] <= pb[0][-4:] else (pb[0], pa[0])
    merged = re.sub(r"\b(?:in|on|for|at)\s+" + re.escape(pa[0]), f"from {first} to {second}", a.question, flags=re.I)
    if merged == a.question:
        merged = re.sub(re.escape(pa[0]), f"from {first} to {second}", a.question, flags=re.I)
    from .select import guess_op
    operation = guess_op(question) if question else None
    operation = operation if operation in ("difference", "pct_change") else a.specification.operation
    requirements = {}
    for requirement in [*a.specification.requirements, *b.specification.requirements]:
        requirements.setdefault((requirement.facet, requirement.value.lower(), requirement.origin), requirement)
    specification = a.specification.model_copy(update={"requirements": list(requirements.values()), "operation": operation})
    return Plan(sub_questions=[SubQuestion(question=merged, search_query=a.search_query, specification=specification)])
