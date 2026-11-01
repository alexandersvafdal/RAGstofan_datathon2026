"""Step 1: split the question into lookups that each need one table."""

import re

from pydantic import BaseModel, Field

from ..llm import LLM


class SubQuestion(BaseModel):
    question: str = Field(description="A self-contained question answerable from a single table, in English")
    search_query: str = Field(description="English keywords for finding the table: topic, measure, place, period")


class Plan(BaseModel):
    sub_questions: list[SubQuestion] = Field(min_length=1, max_length=3)


SYSTEM = """You plan lookups in Statistics Iceland's official statistics tables.
Split the user's question into the smallest number of sub-questions (1 to 3) where each can be answered from ONE table.
Most questions need exactly one. Only split when the question asks about clearly different topics
(for example population AND unemployment).
Never split a comparison within one measure: "how many more men than women", "Reykjavík vs Kópavogur",
"share of foreigners", "share of the population living in Reykjavík", "change from 2025 to 2026" are each ONE
sub-question, because one table holds both sides (a municipality table also has the whole country as a total).
A comparison of two different measures ("the wage index compared with the consumer price index", "launavísitala
borin saman við vísitölu neysluverðs", "exports and imports") is TWO sub-questions, one per measure, over the same
period; without a period, each asks for the development over the last years.
Write sub-questions and search queries in English even if the user wrote Icelandic.
Keep place names in their Icelandic spelling (Reykjavík, Kópavogur, Akureyri).
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
    return await llm.complete_json(SYSTEM, f"Question: {question}", Plan)


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
    return Plan(sub_questions=[SubQuestion(question=merged, search_query=a.search_query)])
