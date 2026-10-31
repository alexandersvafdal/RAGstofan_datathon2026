"""HTTP API and static frontend. Run: uvicorn hagbot.api:app --reload"""

import asyncio
import json
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
    names = _models[1]
    return {"default": settings.llm_model, "models": [settings.llm_model, *(m for m in names if m != settings.llm_model)],
            "fast": settings.fast_model, "reasoning_default": settings.llm_reasoning_effort or "high",
            "reasoning": ["none", "low", "medium", "high"]}


@app.get("/api/indicators", response_model=Indicators)
async def get_indicators() -> Indicators:
    """Headline numbers for the dashboard, read straight from the data (no model): instant from the local snapshot."""
    return await indicators(pipeline.source)


async def _checked(req: AskRequest) -> AskRequest:
    """A model the server does not have is dropped, and the default answers."""
    if req.model and req.model not in (await models())["models"]:
        req = req.model_copy(update={"model": None})
    return req


@app.post("/api/ask", response_model=Answer)
async def ask(req: AskRequest) -> Answer:
    return await pipeline.answer(await _checked(req))


@app.post("/api/ask/stream")
async def ask_stream(req: AskRequest) -> StreamingResponse:
    """The same answer as /api/ask, with each pipeline step sent the moment it finishes. One JSON object per line:
    {"type": "step", "name": ..., "ms": ..., "detail": ...} for each step, then {"type": "answer", "answer": {...}}."""
    queue: asyncio.Queue = asyncio.Queue()
    req = await _checked(req)

    async def run():
        answer = await pipeline.answer(req, on_step=lambda st: queue.put_nowait({"type": "step", **st.model_dump()}))
        queue.put_nowait({"type": "answer", "answer": answer.model_dump()})

    async def lines():
        task = asyncio.create_task(run())
        try:
            while True:
                item = await queue.get()
                yield json.dumps(item, ensure_ascii=False) + "\n"
                if item["type"] == "answer":
                    break
        finally:
            if not task.done():
                task.cancel()

    return StreamingResponse(lines(), media_type="application/x-ndjson")


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
