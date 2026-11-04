import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _path(name: str, default: str) -> Path:
    p = Path(os.getenv(name, default))
    return p if p.is_absolute() else ROOT / p


@dataclass(frozen=True)
class Settings:
    llm_base_url: str = os.getenv("LLM_BASE_URL", "http://localhost:11434/v1")
    llm_model: str = os.getenv("LLM_MODEL", "qwen2.5:7b")
    llm_api_key: str = os.getenv("LLM_API_KEY", "ollama")
    llm_timeout_s: float = float(os.getenv("LLM_TIMEOUT_S", "300"))
    # Reasoning models think before they answer. "none" turns that off for Qwen3-style models (2-3x faster here);
    # gpt-oss takes "low" / "medium" / "high". Empty: send nothing, the server's default.
    llm_reasoning_effort: str = os.getenv("LLM_REASONING_EFFORT", "none")   # off unless .env says otherwise
    # One model call reads the question (tables, options, period, operation, how to show it) and code does the rest
    # (hagbot/reader.py, hagbot/answer2.py). 0: the step-by-step pipeline for every question
    reader: bool = os.getenv("READER", "1") == "1"
    # "Answer now": a quick model with thinking off, for when the default is taking too long
    fast_model: str = os.getenv("FAST_MODEL", "gpt-oss:20b")
    # The models the picker offers, in order ("qwen3.6:35b-a3b,qwen3.8:27b,..."). Empty: every model the endpoint serves.
    models: str = os.getenv("MODELS", "")
    # Tokens the server gives one request (Ollama: OLLAMA_CONTEXT_LENGTH). 0: data/models.json says (hagbot/models.py)
    llm_context: int = int(os.getenv("LLM_CONTEXT", "0"))
    # The answer writer may do a final calculation on the facts ("how many times Seyðisfjörður fits in Reykjavík":
    # 138,753 ÷ 648), shown under the answer and worked out in code (hagbot/steps/compose.py). 0: off
    llm_calc: bool = os.getenv("LLM_CALC", "1") == "1"
    # The model's choice of categories and operation stands. 0: code overrules it from the question's wording (drops
    # categories the question does not name, forces the total, the latest period, a difference for "grow", ...)
    trust_selection: bool = os.getenv("TRUST_SELECTION", "1") == "1"

    data_source: str = os.getenv("DATA_SOURCE", "fixtures")
    px_base_url: str = os.getenv("PX_BASE_URL", "https://px.hagstofa.is")
    px_min_interval_s: float = float(os.getenv("PX_MIN_INTERVAL_S", "1.0"))

    catalog_path: Path = _path("CATALOG_PATH", "data/catalog.json")
    fixtures_dir: Path = _path("FIXTURES_DIR", "data/fixtures")
    local_data_dir: Path = _path("LOCAL_DATA_DIR", "data/release/data")  # the organizers' snapshot, for DATA_SOURCE=local
    cache_dir: Path = _path("CACHE_DIR", ".cache")

    retrieve_top_k: int = int(os.getenv("RETRIEVE_TOP_K", "15"))
    # Majority voting on the model's choices (table pick, categories). VOTES=1 is off. With VOTE_MODELS set
    # ("qwen3.8:27b,gpt-oss:20b,..."), each model votes once; otherwise the main model votes VOTES times, the first
    # at temperature 0 and the rest at VOTE_TEMPERATURE so they can differ.
    votes: int = int(os.getenv("VOTES", "1"))
    vote_models: str = os.getenv("VOTE_MODELS", "")
    vote_temperature: float = float(os.getenv("VOTE_TEMPERATURE", "0.7"))
    # Vector search beside keyword search, used when vectors built by scripts/build_vectors.py lie beside the catalog
    embed_base_url: str = os.getenv("EMBED_BASE_URL", "http://localhost:11434/v1")
    embed_model: str = os.getenv("EMBED_MODEL", "bge-m3")
    use_vectors: bool = os.getenv("USE_VECTORS", "0") == "1"  # off: on bare titles it lowered recall
    # e5 vector search, in-process (hagbot/e5.py). Needs sentence-transformers and torch; the model is ~2.2 GB
    use_e5: bool = os.getenv("USE_E5", "1") == "1"  # without its packages: a warning, then keyword search alone
    e5_model: str = os.getenv("E5_MODEL", "intfloat/multilingual-e5-large")
    e5_card: str = os.getenv("E5_CARD", "multi")  # "single" or "multi" passages per table, see hagbot/e5.py
    e5_device: str = os.getenv("E5_DEVICE", "")  # "", "cpu" or "cuda"
    tables_meta_path: Path = _path("TABLES_META_PATH", "data/tables_20261030_224153.json")


settings = Settings()
