"""HTTP API and static frontend. Run: uvicorn hagbot.api:app --reload"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .config import ROOT, settings
from .pipeline import Pipeline
from .schemas import Answer, AskRequest

pipeline: Pipeline | None = None


@asynccontextmanager
async def lifespan(_: FastAPI):
    global pipeline
    pipeline = Pipeline()
    yield
    await pipeline.llm.aclose()


app = FastAPI(title="Hagbot", lifespan=lifespan)


@app.get("/api/health")
async def health():
    return {
        "model": settings.llm_model,
        "llm_base_url": settings.llm_base_url,
        "data_source": settings.data_source,
        "tables_in_catalog": len(pipeline.catalog.entries),
    }


@app.post("/api/ask", response_model=Answer)
async def ask(req: AskRequest) -> Answer:
    return await pipeline.answer(req)


app.mount("/", StaticFiles(directory=ROOT / "frontend", html=True), name="frontend")
