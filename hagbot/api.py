"""HTTP API and static frontend. Run: uvicorn hagbot.api:app --reload"""

import asyncio
import json
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
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
    return {"default": settings.llm_model, "models": [settings.llm_model, *(m for m in names if m != settings.llm_model)]}


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


class PromptRequest(BaseModel):
    prompt: str


@app.post("/spyrja_px_api", response_model=Answer)
async def spyrja_px_api(req: PromptRequest) -> Answer:
    """The Track 2 brief's endpoint and body: curl -d '{"prompt": "..."}'. Several questions in one prompt are split
    by the planner and answered together."""
    return await pipeline.answer(AskRequest(question=req.prompt))


app.mount("/", StaticFiles(directory=ROOT / "frontend", html=True), name="frontend")
