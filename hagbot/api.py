"""HTTP API and static frontend. Run: uvicorn hagbot.api:app --reload"""

import asyncio
import json
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


@app.get("/api/health")
async def health():
    return {
        "model": settings.llm_model,
        "llm_base_url": settings.llm_base_url,
        "data_source": settings.data_source,
        "tables_in_catalog": len(pipeline.catalog.entries),
    }


@app.get("/api/indicators", response_model=Indicators)
async def get_indicators() -> Indicators:
    """Headline numbers for the dashboard, read straight from the data (no model): instant from the local snapshot."""
    return await indicators(pipeline.source)


@app.post("/api/ask", response_model=Answer)
async def ask(req: AskRequest) -> Answer:
    return await pipeline.answer(req)


@app.post("/api/ask/stream")
async def ask_stream(req: AskRequest) -> StreamingResponse:
    """The same answer as /api/ask, with each pipeline step sent the moment it finishes. One JSON object per line:
    {"type": "step", "name": ..., "ms": ..., "detail": ...} for each step, then {"type": "answer", "answer": {...}}."""
    queue: asyncio.Queue = asyncio.Queue()

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
