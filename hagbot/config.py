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

    data_source: str = os.getenv("DATA_SOURCE", "fixtures")
    px_base_url: str = os.getenv("PX_BASE_URL", "https://px.hagstofa.is")
    px_min_interval_s: float = float(os.getenv("PX_MIN_INTERVAL_S", "1.0"))

    catalog_path: Path = _path("CATALOG_PATH", "data/catalog.json")
    fixtures_dir: Path = _path("FIXTURES_DIR", "data/fixtures")
    local_data_dir: Path = _path("LOCAL_DATA_DIR", "data/release/data")  # the organizers' snapshot, for DATA_SOURCE=local
    cache_dir: Path = _path("CACHE_DIR", ".cache")

    retrieve_top_k: int = int(os.getenv("RETRIEVE_TOP_K", "15"))
    # Vector search beside keyword search, used when vectors built by scripts/build_vectors.py lie beside the catalog
    embed_base_url: str = os.getenv("EMBED_BASE_URL", "http://localhost:11434/v1")
    embed_model: str = os.getenv("EMBED_MODEL", "bge-m3")
    use_vectors: bool = os.getenv("USE_VECTORS", "0") == "1"  # off: on bare titles it lowered recall


settings = Settings()
