"""Step 1: split the question into lookups that each need one table."""

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
"share of foreigners", "change from 2025 to 2026" are each ONE sub-question, because one table holds both sides.
Write sub-questions and search queries in English even if the user wrote Icelandic.
Keep place names in their Icelandic spelling (Reykjavík, Kópavogur, Akureyri).
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


async def plan(llm: LLM, question: str) -> Plan:
    return await llm.complete_json(SYSTEM, f"Question: {question}", Plan)
