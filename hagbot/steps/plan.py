"""Step 1: split the question into lookups that each need one table."""

import re
from typing import Literal

from pydantic import BaseModel, Field

from ..catalog import _same_word
from ..llm import LLM
from ..text import tokens


class SubQuestion(BaseModel):
    question: str = Field(description="A self-contained question answerable from a single table, in English")
    search_query: str = Field(description="English keywords for finding the table: topic, measure, place, period")
    # What the question says, read once by the model here and checked against its text in code (verify_facts), so
    # the later steps act on the user's words rather than on patterns of their own.
    places: list[str] = Field(default=[], description="Place names this sub-question names, exactly as the user wrote them (Reykjavík, Egilsstöðum); [] when none")
    period_kind: Literal["none", "year", "month", "quarter", "range"] = Field(
        default="none", description="The period the user names: a month (ágúst 2026), a quarter, a single year, a range of years/months (frá 2010 til 2026), or none")
    period: str = Field(default="", description="The period words exactly as the user wrote them (ágúst 2026, 2010 til 2026); empty when none")
    breakdown: bool = Field(default=False, description="true when the user wants the figures split by a category: 'by region', 'eftir aldri', 'all municipalities', 'each year'")
    breakdown_by: str = Field(default="", description="The user's own word for that category, as written ('countries', 'sveitarfélögum', 'age groups'); empty when none")
    all_places: list[str] = []   # set in code: the places of every sub-question of the same question


MAX_PARTS = 6


class Plan(BaseModel):
    sub_questions: list[SubQuestion] = Field(min_length=1, max_length=MAX_PARTS)
    per_inhabitant: int = Field(default=0, description="0, or when the question asks for a figure per inhabitant split "
                                "into two sub-questions (the figure, then the population): the number of inhabitants it "
                                "is per (1 for per inhabitant / per capita, 1000 for per 1,000 inhabitants)")


SYSTEM = """You plan lookups in Statistics Iceland's official statistics tables.
Split the user's question into the smallest number of sub-questions (1 to 6) where each can be answered from ONE table.
Most questions need exactly one. Only split when the question asks about clearly different topics
(for example population AND unemployment).
Never split a comparison within one measure: "how many more men than women", "Reykjavík vs Kópavogur",
"share of foreigners", "share of the population living in Reykjavík", "change from 2025 to 2026" are each ONE
sub-question, because one table holds both sides (a municipality table also has the whole country as a total).
A comparison of two different measures ("the wage index compared with the consumer price index", "launavísitala
borin saman við vísitölu neysluverðs", "exports and imports") is TWO sub-questions, one per measure, over the same
period; without a period, each asks for the development over the last years.
A figure per inhabitant ("per capita", "per 1,000 inhabitants", "á hvern íbúa", "á hverja 1.000 íbúa", "á mann"):
- When it is a rate statistics offices publish as such (GDP per capita, vehicles per 1,000 inhabitants, alcohol
  consumption per inhabitant) and the question does not name the population date, keep ONE sub-question asking for
  that rate, and per_inhabitant 0.
- Otherwise TWO sub-questions, in this order: the figure itself without "per ..." (the same place and period), then the
  population of that place on 1 January of that year ("How many people lived in Iceland on 1 January 2025?"); set
  per_inhabitant to the number it is per (1, or 1000 for "per 1,000"). The division is done in code.
Write sub-questions and search queries in English even if the user wrote Icelandic.
Keep place names in their Icelandic spelling (Reykjavík, Kópavogur, Akureyri).
For each sub-question also report what the user's own words say: the places named (as written), the period and its
kind, and whether a breakdown by a category is wanted and the user's word for it. Report only what is in the question;
never add a place or period.
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


async def plan(llm: LLM, question: str) -> Plan:
    p = await llm.complete_json(SYSTEM, f"Question: {question}", Plan)
    for sq in p.sub_questions:
        verify_facts(sq, question)
    every = list(dict.fromkeys(pl for sq in p.sub_questions for pl in sq.places))
    for sq in p.sub_questions:
        sq.all_places = every
    return p


def verify_facts(sq: SubQuestion, question: str) -> None:
    """A model's reading of the question must be anchored in its text: a place it reports stays only when a word of
    the question is that word (modulo inflection: Egilsstaðir / Egilstöðum), a period only when its words are there.
    Anything unanchored is dropped, so a hallucinated place or month cannot steer the later steps."""
    qtokens = tokens(question)
    sq.places = [pl for pl in sq.places if any(_same_word(t, w) or t[:5] == w[:5] for t in tokens(pl) if len(t) >= 5 for w in qtokens if len(w) >= 5)
                 or any(_same_word(t, w) for t in tokens(pl) if len(t) >= 4 for w in qtokens)]   # Egilsstaðir / Egilstöðum
    if sq.period and not all(w in qtokens or w.isdigit() and w in question for w in tokens(sq.period)):
        sq.period, sq.period_kind = "", "none"
    if sq.period_kind != "none" and not re.search(r"\b(?:18|19|20)\d\d\b", question) and sq.period_kind != "month":
        sq.period_kind = "none"   # a year or a range needs a year in the text; a month alone ("in August") may stand
    # the breakdown word must be in the text too (countries / country, sveitarfélögum / sveitarfélög), or be the
    # English name of an Icelandic dimension word in it (the model writes "countries" for "löndum")
    from .retrieve import _DIMENSION_IS
    qwords = [w for w in qtokens if len(w) >= 4] + [_DIMENSION_IS[w] for w in qtokens if w in _DIMENSION_IS]
    if sq.breakdown_by and not any(_same_word(t, w) or t[:5] == w[:5] for t in tokens(sq.breakdown_by) if len(t) >= 4
                                   for w in qwords):
        sq.breakdown_by = ""
    if sq.breakdown_by:
        sq.breakdown = True


_PERIOD = (r"\b(?:\d{1,2}\.?\s+)?(?:(?:january|february|march|april|may|june|july|august|september|october|november|december|"
           r"q[1-4]|first quarter|second quarter|third quarter|fourth quarter)\s+)?(?:18|19|20)\d\d\b")


def merge_period_parts(p: Plan) -> Plan:
    """One measure at two periods planned as two parts ("the CPI in September 2025" | "the CPI in September 2026",
    from "um hve mörg stig hækkaði vísitalan frá september 2025 til september 2026") becomes one part asking for
    both, so the change is computed from one table. Parts that differ in anything but the period stay apart."""
    if len(p.sub_questions) != 2:
        return p
    a, b = p.sub_questions
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
    return Plan(sub_questions=[SubQuestion(question=merged, search_query=a.search_query, places=a.places + [x for x in b.places if x not in a.places],
                                           period_kind="range", period=f"{first} to {second}", breakdown=a.breakdown or b.breakdown)])
