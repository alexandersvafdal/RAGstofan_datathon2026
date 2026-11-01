"""How much text a model can take in one call: the smaller of what the model supports and what the server runs it with.

data/models.json keeps each model's own context length, read from the server, and the server's context per request
(Ollama's OLLAMA_CONTEXT_LENGTH; the Spark runs every model at 16,384). A model missing from the file is looked up on
the server (Ollama's /api/show). LLM_CONTEXT in .env overrides the server's number.

Refresh the file from the server (run where the server is reachable):
    python -m hagbot.models
"""

import json
from functools import lru_cache

import httpx

from .config import ROOT, settings

MODELS_FILE = ROOT / "data" / "models.json"
FALLBACK = 8192   # nothing known about the model or the server: assume a small context


def _ollama_root() -> str:
    """http://host:11434/v1 -> http://host:11434, where Ollama's own API lives."""
    return settings.llm_base_url.rstrip("/").removesuffix("/v1")


def _saved() -> dict:
    try:
        return json.loads(MODELS_FILE.read_text())
    except (OSError, ValueError):
        return {}


@lru_cache(maxsize=None)
def _ask_server(model: str) -> int | None:
    """The model's own context length from Ollama's /api/show, or None when the server does not say."""
    try:
        r = httpx.post(f"{_ollama_root()}/api/show", json={"model": model}, timeout=5)
        r.raise_for_status()
        info = r.json().get("model_info", {})
    except (httpx.HTTPError, ValueError):
        return None
    lengths = [v for k, v in info.items() if k.endswith(".context_length") and "original" not in k]
    return max(lengths) if lengths else None


def usable_context(model: str) -> int:
    """Tokens one call to this model can hold, prompt and answer together."""
    saved = _saved()
    supports = saved.get("models", {}).get(model, {}).get("context") or _ask_server(model)
    server = settings.llm_context or saved.get("server_context")
    known = [n for n in (supports, server) if n]
    return min(known) if known else FALLBACK


def refresh() -> dict:
    """Read every model's context length from the server, and the context loaded models run with, into the file."""
    root = _ollama_root()
    names = [m["name"] for m in httpx.get(f"{root}/api/tags", timeout=10).json().get("models", [])]
    _ask_server.cache_clear()
    models = {n: {"context": c} for n in names if (c := _ask_server(n))}
    loaded = [m.get("context_length") for m in httpx.get(f"{root}/api/ps", timeout=10).json().get("models", [])]
    server = min((c for c in loaded if c), default=None) or _saved().get("server_context")
    data = {"about": "Context per model from the server (python -m hagbot.models). server_context: the context the "
                     "server runs each request with (Ollama OLLAMA_CONTEXT_LENGTH), read from the models loaded at "
                     "the time. LLM_CONTEXT in .env overrides it.",
            "server_context": server, "models": models}
    MODELS_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    return data


if __name__ == "__main__":
    d = refresh()
    print(f"server context {d['server_context']}; {len(d['models'])} models written to {MODELS_FILE}")
    for name, m in d["models"].items():
        print(f"  {name}: supports {m['context']}, usable {usable_context(name)}")
