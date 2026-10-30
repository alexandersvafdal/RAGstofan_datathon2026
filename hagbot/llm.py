"""Model-agnostic chat client for any OpenAI-compatible endpoint (Ollama, vLLM on LUMI, hosted APIs)."""

import json
import re
import time
from typing import TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from .config import settings

T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    pass


class LLM:
    def __init__(self, base_url: str | None = None, model: str | None = None, api_key: str | None = None):
        self.base_url = (base_url or settings.llm_base_url).rstrip("/")
        self.model = model or settings.llm_model
        self.client = httpx.AsyncClient(
            timeout=settings.llm_timeout_s,
            headers={"Authorization": f"Bearer {api_key or settings.llm_api_key}"},
        )
        self.calls = 0

    async def chat(self, messages: list[dict], *, json_mode: bool = False, temperature: float = 0.0) -> str:
        body = {"model": self.model, "messages": messages, "temperature": temperature}
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        self.calls += 1
        try:
            r = await self.client.post(f"{self.base_url}/chat/completions", json=body)
            r.raise_for_status()
        except httpx.TimeoutException as e:  # its message is empty; say what happened
            raise LLMError(f"the language model did not answer within {settings.llm_timeout_s:.0f} s") from e
        except httpx.HTTPError as e:
            raise LLMError(f"LLM request failed: {type(e).__name__} {e}".strip()) from e
        return r.json()["choices"][0]["message"]["content"]

    async def complete_json(self, system: str, user: str, schema: type[T], *, retries: int = 2) -> T:
        """Ask for JSON matching `schema`. On a parse or validation error, show the model the error and retry."""
        messages = [
            {"role": "system", "content": system + "\n\nReply with one JSON object only. JSON schema:\n"
             + json.dumps(schema.model_json_schema(), ensure_ascii=False)},
            {"role": "user", "content": user},
        ]
        last_err = None
        for _ in range(retries + 1):
            raw = await self.chat(messages, json_mode=True)
            try:
                return schema.model_validate(_extract_json(raw))
            except (ValueError, ValidationError) as e:
                last_err = e
                messages += [
                    {"role": "assistant", "content": raw},
                    {"role": "user", "content": f"That was invalid: {e}. Reply again with corrected JSON only."},
                ]
        raise LLMError(f"Model did not return valid {schema.__name__}: {last_err}")

    async def aclose(self):
        await self.client.aclose()


def _extract_json(raw: str) -> dict:
    raw = raw.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", raw, re.S)
    if fence:
        raw = fence.group(1)
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object found")
    return json.loads(raw[start : end + 1])


class Timer:
    def __init__(self):
        self.t0 = time.perf_counter()

    def ms(self) -> int:
        return int((time.perf_counter() - self.t0) * 1000)
