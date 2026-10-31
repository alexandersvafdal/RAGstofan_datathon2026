"""Follow-up questions that lead somewhere from an answer, one of each kind:
  compare  - another group, region or period the same table has
  related  - a question one of the related tables on the answer's shortlist can answer
  deeper   - dig further into the same figure: a breakdown, its trend, when it peaked
  wider    - the bigger picture: per inhabitant, over decades, against something it connects to
Asked for after the answer is shown (/api/suggest), so it never adds to the answer's time. The model writes them from
the tables' own dimensions and the shortlist, so they can be answered; if it fails, simple ones are built directly."""

import re
from typing import Literal

from pydantic import BaseModel

from ..llm import LLM
from ..schemas import AnswerPart
from ..sources import TableMeta

Kind = Literal["compare", "related", "deeper", "wider"]
KINDS: tuple[Kind, ...] = ("compare", "related", "deeper", "wider")

SYSTEM = {
    "en": """You suggest follow-up questions for a chatbot that answers from Statistics Iceland's tables.
From a question, its answer, the table it came from and related tables, write four short follow-up questions in English,
one of each kind:
- compare: the same figure for another group, region, category or period that the table's dimensions offer;
- related: a question that one of the RELATED TABLES listed answers (name its subject, not its code);
- deeper: dig further into the same figure: a breakdown by a dimension, its trend over years, or when it peaked;
- wider: the bigger picture: per inhabitant, over decades, or against something it connects to.
Each question stands on its own (name the subject; never "it" or "this"), is at most 14 words, asks for numbers,
and is not the user's question.""",
    "is": """Þú stingur upp á framhaldsspurningum fyrir spjallmenni sem svarar úr töflum Hagstofu Íslands.
Út frá spurningu, svari, töflunni sem svarið kom úr og skyldum töflum skaltu skrifa fjórar stuttar framhaldsspurningar
á íslensku, eina af hverri gerð:
- compare: sama tala fyrir annan hóp, landshluta, flokk eða tímabil sem víddir töflunnar bjóða;
- related: spurning sem ein af SKYLDU TÖFLUNUM svarar (nefndu efnið, ekki kóðann);
- deeper: dýpra í sömu tölu: sundurliðun eftir vídd, þróun yfir ár, eða hvenær hún var hæst;
- wider: stærra samhengi: á hvern íbúa, yfir áratugi, eða í samanburði við það sem hún tengist.
Hver spurning stendur ein og sér (nefndu efnið; aldrei „það“ eða „þetta“), er í mesta lagi 14 orð, spyr um tölur
og er ekki spurning notandans.""",
}


class Suggestion(BaseModel):
    kind: Kind
    question: str


class Suggestions(BaseModel):
    suggestions: list[Suggestion]


def _table_block(meta: TableMeta, part: AnswerPart) -> str:
    lines = [f"Table {meta.id}: {meta.title}"]
    for v in meta.variables:
        chosen = ", ".join(part.selection.get(v.text, [])[:3])
        if v.time:
            lines.append(f"- {v.text} (time): {v.values[0]} to {v.values[-1]}; chosen: {chosen}")
        else:
            sample = ", ".join(v.value_texts[:8]) + (" …" if len(v.value_texts) > 8 else "")
            lines.append(f"- {v.text}: {sample}; chosen: {chosen}")
    related = [r for r in (part.ranking or []) if r.get("id") != meta.id][:6]
    if related:
        lines.append("RELATED TABLES:")
        lines += [f"- {r['title']}" for r in related]
    return "\n".join(lines)


async def suggest(llm: LLM, question: str, answer: str, parts: list[AnswerPart], metas: list[TableMeta],
                  lang: str) -> list[dict]:
    """[{"kind": "compare", "question": "..."}, ...], one of each kind where the model managed it."""
    tables = "\n\n".join(_table_block(m, p) for m, p in zip(metas, parts))
    user = f"Question: {question}\nAnswer: {answer}\n\n{tables}"
    try:
        got = await llm.complete_json(SYSTEM["is" if lang == "is" else "en"], user, Suggestions, retries=1)
        seen, out = set(), []
        for s in got.suggestions:
            q = re.sub(r"^\s*\d[.)]\s*", "", s.question).strip()
            if q and s.kind not in seen and q.lower().rstrip("?") != question.lower().rstrip("?"):
                seen.add(s.kind)
                out.append({"kind": s.kind, "question": q})
        if len(out) >= 2:
            return sorted(out, key=lambda s: KINDS.index(s["kind"]))
    except Exception:
        pass
    return fallback(parts, metas, lang)


def fallback(parts: list[AnswerPart], metas: list[TableMeta], lang: str) -> list[dict]:
    """Without the model: a comparison and a trend built from the first table's dimensions."""
    if not parts or not metas:
        return []
    part, meta = parts[0], metas[0]
    topic = meta.title.split(" by ")[0].split(" eftir ")[0].rstrip(" 0123456789-–").lower()
    out = []
    for v in meta.variables:
        others = [t for t in v.value_texts if not re.match(r"(?i)(total|alls|samtals)", t)]
        if not v.time and len(others) >= 2:
            out.append({"kind": "compare", "question": f"Berðu saman {topic}: {others[0]} og {others[1]}" if lang == "is"
                        else f"Compare {topic}: {others[0]} and {others[1]}"})
            break
    time_var = next((v for v in meta.variables if v.time), None)
    if time_var and len(part.selection.get(time_var.text, [])) <= 1:
        out.append({"kind": "deeper", "question": f"Hvernig hefur {topic} þróast síðustu 10 ár?" if lang == "is"
                    else f"How has {topic} changed over the last 10 years?"})
    return out
