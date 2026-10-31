"""Build a validation set from Statistics Iceland's own news releases: real topics, real phrasing, answers stated by
Statistics Iceland itself.

    python -m scripts.build_news_valset scrape --limit 40     # fetch the newest English releases (10 s apart)
    python -m scripts.build_news_valset draft                 # model writes questions; code keeps only checked ones
    python -m scripts.build_news_valset add eval/holdout_news_drafts.json   # hand-written questions, same check

1. scrape: the newest articles from statice.is/publications/news-archive, cached in .cache/news/ (the site's
   robots.txt asks for 10 s between requests, and that is kept).
2. draft: for each article the language model proposes up to two questions, each answered by ONE figure that the
   article states, with the period written out, in English and Icelandic. A question is kept only if its figure
   appears verbatim in the article text, so the answer key is Statistics Iceland's wording, not the model's.
   The scoring tolerance follows the article's rounding: "roughly 1,039,000" allows ±500, "5.4%" allows ±0.05.

Output: eval/holdout_news.jsonl in the eval format (expected_tables empty: any table may answer). This set is for
measuring only. Do not fix the bot against its questions, or it stops telling us anything.
"""

import argparse
import asyncio
import html
import json
import re
import time
from pathlib import Path

import httpx
from pydantic import BaseModel, Field

from hagbot.llm import LLM, LLMError

BASE = "https://statice.is"
LIST = f"{BASE}/publications/news-archive/"
CACHE = Path(".cache/news")
OUT = Path("eval/holdout_news.jsonl")
AGENT = "RAGstofan-datathon2026 (validation set; contact via github.com/alexandersvafdal/RAGstofan_datathon2026)"
DELAY_S = 10  # robots.txt: Crawl-delay: 10


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def parse_article(page: str) -> dict:
    """The release's headline and text: everything inside <article class="single-article">, nothing from the menus."""
    page = re.sub(r"<script.*?</script>|<style.*?</style>|<!--.*?-->", "", page, flags=re.S)
    title = re.search(r'id="article-detail-page-title"[^>]*>(.*?)</h2>', page, re.S)
    body = re.search(r'<article class="single-article">(.*?)</article>', page, re.S)
    date = re.search(r"\b(\d{1,2} (?:January|February|March|April|May|June|July|August|September|October|November|"
                     r"December) 20\d\d)\b", page, re.I)
    paras = [_text(p) for p in re.findall(r"<(?:p|li|h3|h4)[^>]*>(.*?)</(?:p|li|h3|h4)>", body.group(1), re.S)] if body else []
    paras = [p for p in paras if len(p) > 20 and p != "Statistics"]
    return {"title": _text(title.group(1)) if title else "", "date": date.group(1) if date else "", "text": "\n".join(paras)}


def scrape(limit: int) -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    client = httpx.Client(headers={"User-Agent": AGENT}, timeout=30, follow_redirects=True)
    links = list(dict.fromkeys(re.findall(r'href="(/publications/news-archive/[a-z-]+/[^"]+/)"', client.get(LIST).text)))
    print(f"{len(links)} articles listed, fetching the newest {limit}", flush=True)
    for i, link in enumerate(links[:limit], 1):
        f = CACHE / (link.strip("/").replace("/", "__") + ".json")
        if f.exists():
            continue
        time.sleep(DELAY_S)
        r = client.get(BASE + link)
        if r.status_code != 200:
            print(f"  {r.status_code} {link}", flush=True)
            continue
        f.with_suffix(".html").write_text(r.text)  # kept, so the parser can change without fetching again
        art = {"url": BASE + link, **parse_article(r.text)}
        f.write_text(json.dumps(art, ensure_ascii=False, indent=1))
        print(f"  {i:3}/{limit} {art['date']:18} {art['title'][:70]}", flush=True)


class Drafted(BaseModel):
    question_en: str = Field(description="what a member of the public would ask, in plain English, with the period written out")
    question_is: str = Field(description="the same question in natural Icelandic")
    figure: str = Field(description="the answer, copied EXACTLY as written in the article (e.g. '1,039,000', '5.4%', '513,000')")
    unit: str = Field(description="what the figure counts: people, nights, %, ISK million, index points, ...")


class Drafts(BaseModel):
    questions: list[Drafted] = Field(max_length=2)


SYSTEM = """You write test questions for a chatbot that answers from Statistics Iceland's tables.
From the news release, pick up to two figures that the release states plainly, and write the question each answers.
Rules:
- One figure per question. The question must name the measure, the place (Iceland unless the release says
  otherwise) and the exact period (e.g. "in September 2026"), so it can be answered without the article.
- Prefer headline figures: totals, counts, levels, 12-month changes. Avoid figures that need the article's context
  ("compared with the previous month in the South region").
- Copy the figure exactly as the article writes it, digits and separators included.
- Write the questions the way a curious person would ask, not like a table title."""


def _number(figure: str) -> tuple[float, float] | None:
    """('1,039,000' -> 1039000, abs tolerance 500); ('5.4%' -> 5.4, 0.05); None if no number."""
    m = re.search(r"-?\d[\d,]*(?:\.\d+)?", figure)
    if not m:
        return None
    s = m.group(0).replace(",", "")
    value = float(s)
    decimals = len(s.split(".")[1]) if "." in s else 0
    if decimals:
        tol = 0.5 * 10 ** -decimals
    else:
        zeros = len(s) - len(s.rstrip("0"))
        tol = 0.5 * 10 ** zeros if zeros else 0.5
    return value, tol


async def draft() -> None:
    llm = LLM()
    rows, kept, dropped = [], 0, 0
    try:
        for f in sorted(CACHE.glob("*.json")):
            art = json.loads(f.read_text())
            if len(art["text"]) < 200:
                continue
            try:
                d = await llm.complete_json(SYSTEM, f"Released: {art['date']}\nTitle: {art['title']}\n\n{art['text'][:6000]}", Drafts)
            except LLMError as e:
                print(f"  model failed on {f.name}: {e}", flush=True)
                continue
            for i, q in enumerate(d.questions):
                num = _number(q.figure)
                # the answer key must be Statistics Iceland's own words: the figure has to be in the article
                if not num or q.figure.strip().rstrip("%").strip() not in art["text"]:
                    dropped += 1
                    continue
                value, tol = num
                slug = f.stem.split("__")[-1][:40]
                for lang, text in (("en", q.question_en), ("is", q.question_is)):
                    rows.append({"id": f"news-{slug}-{i}-{lang}", "level": "news", "language": lang, "question": text,
                                 "expected_tables": [], "expected_values": [value],
                                 "tolerance": round(max(tol / abs(value), 1e-6), 6) if value else 0.005,
                                 "figure_as_published": q.figure, "unit": q.unit, "source": art["url"],
                                 "released": art["date"]})
                kept += 1
            print(f"  {f.stem[:60]:60} kept {sum(1 for r in rows if f.stem.split('__')[-1][:40] in r['id']) // 2}", flush=True)
    finally:
        await llm.aclose()
    OUT.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    print(f"\n{OUT}: {kept} questions kept ({len(rows)} with both languages), {dropped} dropped (figure not in the article)")


def rows_for(slug: str, art: dict, i: int, q: dict) -> list[dict] | None:
    """Both language versions of one question, or None if its figure is not written in the article."""
    num = _number(q["figure"])
    if not num or q["figure"].strip().rstrip("%").strip() not in art["text"]:
        return None
    value, tol = num
    return [{"id": f"news-{slug[:40]}-{i}-{lang}", "level": "news", "language": lang, "question": q[f"question_{lang}"],
             "expected_tables": [], "expected_values": [value],
             "tolerance": round(max(tol / abs(value), 1e-6), 6) if value else 0.005,
             "figure_as_published": q["figure"], "unit": q.get("unit", ""), "source": art["url"], "released": art["date"]}
            for lang in ("en", "is")]


def add(drafts_path: str) -> None:
    """Hand-written drafts: {"<article slug>": [{"question_en", "question_is", "figure", "unit"}, ...], ...}"""
    arts = {f.stem.split("__")[-1]: json.loads(f.read_text()) for f in CACHE.glob("*.json")}
    rows, dropped = [], []
    for slug, qs in json.loads(Path(drafts_path).read_text()).items():
        for i, q in enumerate(qs):
            r = rows_for(slug, arts[slug], i, q) if slug in arts else None
            (rows.extend(r) if r else dropped.append(f"{slug}: {q['figure']}"))
    OUT.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    print(f"{OUT}: {len(rows) // 2} questions ({len(rows)} with both languages)")
    for d in dropped:
        print("  dropped, figure not in the article:", d)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["scrape", "draft", "add"])
    ap.add_argument("drafts", nargs="?")
    ap.add_argument("--limit", type=int, default=40)
    a = ap.parse_args()
    if a.stage == "scrape":
        scrape(a.limit)
    elif a.stage == "add":
        add(a.drafts)
    else:
        asyncio.run(draft())
