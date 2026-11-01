"""HTTP API and static frontend. Run: uvicorn hagbot.api:app --reload"""

import asyncio
import json
import re
import time
from contextlib import asynccontextmanager

import os

import httpx
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import ROOT, settings
from .indicators import Indicators, indicators
from .pipeline import Pipeline
from .schemas import Answer, AskRequest
from .sources import table_url

pipeline: Pipeline | None = None


@asynccontextmanager
async def lifespan(_: FastAPI):
    global pipeline
    pipeline = Pipeline()
    yield
    await pipeline.llm.aclose()


app = FastAPI(title="RAGstofan", lifespan=lifespan)


@app.middleware("http")
async def revalidate_frontend(request, call_next):
    """Browsers must re-check the page and its files on every load, so nobody is shown an old copy of the interface."""
    response = await call_next(request)
    if not request.url.path.startswith("/api"):
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.get("/api/health")
async def health():
    return {
        "model": settings.llm_model,
        "llm_base_url": settings.llm_base_url,
        "data_source": settings.data_source,
        "tables_in_catalog": len(pipeline.catalog.entries),
    }


_models: tuple[float, list[str]] = (0.0, [])


@app.get("/api/models")
async def models():
    """The models a question can be asked with. The default is the one we measured best (LLM_MODEL)."""
    global _models
    if time.monotonic() - _models[0] > 60:
        try:
            _models = (time.monotonic(), await pipeline.llm.models())
        except Exception:   # the endpoint cannot list models: offer only the default
            _models = (time.monotonic(), [])
    # The default first, then the rest from the largest down: the parameter count is read from the tag ("27b", "0.8b")
    size = lambda m: (-float(x.group(1)) if (x := re.search(r"(\d+(?:\.\d+)?)b\b", m)) else 0.0, m)
    names = sorted((m for m in _models[1] if m != settings.llm_model), key=size)
    return {"default": settings.llm_model, "models": [settings.llm_model, *names], "fast": settings.fast_model}


@app.get("/api/indicators", response_model=Indicators)
async def get_indicators() -> Indicators:
    """Headline numbers for the dashboard, read straight from the data (no model): instant from the local snapshot."""
    return await indicators(pipeline.source)


async def _checked(req: AskRequest) -> AskRequest:
    """A model the server does not have is dropped, and the default answers."""
    if req.model and req.model not in (await models())["models"]:
        req = req.model_copy(update={"model": None})
    return req


def _log_question(req: AskRequest, answer: Answer, seconds: float) -> None:
    """One line per question asked, in logs/questions.jsonl: the raw material for new test questions. Never the
    history, only the question as typed and what came of it."""
    try:
        path = ROOT / "logs" / "questions.jsonl"
        path.parent.mkdir(exist_ok=True)
        with path.open("a") as f:
            f.write(json.dumps({"t": time.strftime("%Y-%m-%dT%H:%M:%S"), "question": req.question,
                                "language": answer.language, "model": answer.model, "status": answer.status,
                                "tables": [s.id for s in answer.sources], "seconds": round(seconds, 1)},
                               ensure_ascii=False) + "\n")
    except OSError:
        pass


@app.post("/api/ask", response_model=Answer)
async def ask(req: AskRequest) -> Answer:
    t0 = time.monotonic()
    answer = await pipeline.answer(await _checked(req))
    _log_question(req, answer, time.monotonic() - t0)
    return answer


@app.post("/api/ask/stream")
async def ask_stream(req: AskRequest) -> StreamingResponse:
    """The same answer as /api/ask, with each pipeline step sent the moment it finishes. One JSON object per line:
    {"type": "step", "name": ..., "ms": ..., "detail": ...} for each step, then {"type": "answer", "answer": {...}}."""
    queue: asyncio.Queue = asyncio.Queue()
    req = await _checked(req)
    rid = req.request_id

    async def run():
        t0 = time.monotonic()
        answer = await pipeline.answer(req, on_step=lambda st: queue.put_nowait({"type": "step", **st.model_dump()}))
        _log_question(req, answer, time.monotonic() - t0)
        if rid in _jobs:
            _jobs[rid]["answer"] = answer.model_dump()
        queue.put_nowait({"type": "answer", "answer": answer.model_dump()})

    _forget_old_jobs()
    task = asyncio.create_task(run())
    if rid:
        _jobs[rid] = {"task": task, "answer": None, "t": time.monotonic()}

    async def lines():
        try:
            while True:
                item = await queue.get()
                yield json.dumps(item, ensure_ascii=False) + "\n"
                if item["type"] == "answer":
                    break
        finally:
            # a client that gave a request id may only have gone to sleep: finish and keep the answer for it.
            # Without one, nobody can collect it, so stop.
            if not task.done() and not rid:
                task.cancel()

    return StreamingResponse(lines(), media_type="application/x-ndjson")


# Answers asked for with a request id, kept 15 minutes so a phone that slept through the end can collect them
_jobs: dict[str, dict] = {}
JOB_TTL = 15 * 60


def _forget_old_jobs():
    for k in [k for k, j in _jobs.items() if time.monotonic() - j["t"] > JOB_TTL]:
        _jobs.pop(k, None)


@app.get("/api/ask/{request_id}")
async def ask_result(request_id: str):
    """{"status": "running"} until the answer is ready, then {"status": "done", "answer": {...}}."""
    job = _jobs.get(request_id)
    if not job:
        raise HTTPException(404, "unknown or expired request")
    if job["answer"] is not None:
        return {"status": "done", "answer": job["answer"]}
    if job["task"].done():   # it failed or was stopped
        raise HTTPException(410, "the question was stopped")
    return {"status": "running"}


@app.delete("/api/ask/{request_id}")
async def ask_cancel(request_id: str):
    """Stop a question that is no longer wanted ("Answer now" asks again with the fast model)."""
    job = _jobs.pop(request_id, None)
    if job and not job["task"].done():
        job["task"].cancel()
    return {"cancelled": bool(job)}


# Voice runs as its own service (voice/server.py, port 8001, own GPU environment). The website reaches it through
# these routes so it only ever talks to one address; the app calls the service directly.
VOICE_URL = os.getenv("VOICE_URL", "http://127.0.0.1:8001")


async def _voice(method: str, path: str, **kwargs) -> httpx.Response:
    try:
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.request(method, f"{VOICE_URL}{path}", **kwargs)
    except httpx.HTTPError as e:
        raise HTTPException(503, f"voice service unavailable: {type(e).__name__}") from e
    if r.status_code >= 400:
        raise HTTPException(r.status_code, r.text[:300])
    return r


@app.get("/api/voices")
async def voices():
    return (await _voice("GET", "/voices")).json()


class SpeakRequest(BaseModel):
    text: str
    language: str = "en"
    voice: str | None = None


@app.post("/api/tts")
async def tts(req: SpeakRequest):
    r = await _voice("POST", "/tts", json=req.model_dump())
    return Response(r.content, media_type="audio/wav")


@app.post("/api/stt")
async def stt(audio: UploadFile = File(...), language: str | None = Form(None)):
    files = {"audio": (audio.filename or "question.webm", await audio.read(), audio.content_type or "application/octet-stream")}
    return (await _voice("POST", "/stt", files=files, data={"language": language} if language else None)).json()


def _table_summary(e) -> dict:
    return {"id": e.id, "title": e.title_en, "title_is": e.title_is, "path": e.path, "dimensions": e.dimensions,
            "first_period": e.first_period, "last_period": e.last_period, "unit": e.unit,
            "url": table_url(e.path, e.id)}


@app.get("/api/tables")
def search_tables(q: str, limit: int = 8):
    """The catalogue's best matches for a search, as the retriever ranks them (keywords, BM25 and vectors).
    A plain def: the search is synchronous, so FastAPI runs it off the event loop."""
    return {"tables": [_table_summary(e) | {"score": round(s, 4)} for e, s in pipeline.catalog.search(q, k=max(1, min(limit, 25)))]}


@app.get("/api/tables/{table_id}")
def describe_table(table_id: str):
    e = pipeline.catalog.get(table_id)
    if not e:
        raise HTTPException(404, f"no table {table_id}")
    return _table_summary(e) | {"about": e.about_en, "values": e.values[:200], "values_total": len(e.values)}


@app.post("/api/suggest")
async def suggest(ans: Answer) -> dict:
    """Three follow-up questions for an answer the user is already reading (the client asks once it has shown it)."""
    return {"questions": await pipeline.suggest(ans)}


class PromptRequest(BaseModel):
    prompt: str


@app.post("/spyrja_px_api", response_model=Answer)
async def spyrja_px_api(req: PromptRequest) -> Answer:
    """The Track 2 brief's endpoint and body: curl -d '{"prompt": "..."}'. Several questions in one prompt are split
    by the planner and answered together."""
    return await pipeline.answer(AskRequest(question=req.prompt))


app.mount("/", StaticFiles(directory=ROOT / "frontend", html=True), name="frontend")
