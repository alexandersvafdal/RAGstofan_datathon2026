"""Model-agnostic chat client for any OpenAI-compatible endpoint (Ollama, vLLM on LUMI, hosted APIs)."""

import copy
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
        self.temperature = 0.0                                 # for calls that do not set their own
        self.reasoning_effort = settings.llm_reasoning_effort  # "" sends nothing: the server's default

    def with_options(self, model: str | None = None, temperature: float | None = None,
                     reasoning_effort: str | None = None) -> "LLM":
        """The same endpoint and connection with another model, temperature or thinking length; its own call count."""
        other = copy.copy(self)
        other.calls = 0
        if model:
            other.model = model
        if temperature is not None:
            other.temperature = temperature
        if reasoning_effort is not None:
            other.reasoning_effort = reasoning_effort
        return other

    async def models(self) -> list[str]:
        """The models the endpoint serves (OpenAI-compatible /models: Ollama, vLLM), embedding models left out."""
        r = await self.client.get(f"{self.base_url}/models", timeout=5)
        r.raise_for_status()
        return sorted(m["id"] for m in r.json().get("data", []) if "embed" not in m["id"])

    async def chat(self, messages: list[dict], *, json_mode: bool = False, temperature: float | None = None) -> str:
        body = {"model": self.model, "messages": messages,
                "temperature": self.temperature if temperature is None else temperature}
        if self.reasoning_effort:
            effort = self.reasoning_effort
            if effort == "none" and "gpt-oss" in self.model:  # gpt-oss only takes low / medium / high
                effort = "low"
            if effort == "none" and settings.llm_template_kwargs:
                body["chat_template_kwargs"] = {"enable_thinking": False}
            else:
                body["reasoning_effort"] = effort
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        self.calls += 1
        try:
            r = await self.client.post(f"{self.base_url}/chat/completions", json=body)
            if r.status_code >= 500:
                # Ollama aborts a prediction stuck repeating itself ("token repeat limit reached"); at temperature 0
                # the same prompt loops the same way, so the retry samples a little. Any other 500: once more as is.
                again = {**body, "temperature": max(0.3, body["temperature"])} if "repeat limit" in r.text else body
                r = await self.client.post(f"{self.base_url}/chat/completions", json=again)
            r.raise_for_status()
        except httpx.TimeoutException as e:  # its message is empty; say what happened
            raise LLMError(f"the language model did not answer within {settings.llm_timeout_s:.0f} s") from e
        except httpx.HTTPStatusError as e:   # the server's own words say why (context length, a bad request)
            raise LLMError(f"LLM request failed: {e.response.status_code} {e.response.text[:300]}".strip()) from e
        except httpx.HTTPError as e:
            raise LLMError(f"LLM request failed: {type(e).__name__} {e}".strip()) from e
        return r.json()["choices"][0]["message"]["content"]

    async def complete_json(self, system: str, user: str, schema: type[T], *, retries: int = 2, temperature: float | None = None) -> T:
        """Ask for JSON matching `schema`. On a parse or validation error, show the model the error and retry."""
        messages = [
            {"role": "system", "content": system + "\n\nReply with one JSON object only. JSON schema:\n"
             + json.dumps(schema.model_json_schema(), ensure_ascii=False)},
            {"role": "user", "content": user},
        ]
        last_err = None
        for attempt in range(retries + 1):
            # the last try samples a little: at temperature 0 a model that answered wrongly answers the same way
            t = temperature if attempt < retries else max(0.3, self.temperature if temperature is None else temperature)
            raw = await self.chat(messages, json_mode=True, temperature=t)
            try:
                return schema.model_validate(_unwrap_schema_echo(_extract_json(raw)))
            except (ValueError, ValidationError) as e:
                last_err = e
                messages += [
                    {"role": "assistant", "content": raw},
                    {"role": "user", "content": f"That was invalid: {e}. Reply again with corrected JSON only."},
                ]
        raise LLMError(f"Model did not return valid {schema.__name__}: {last_err}")

    async def aclose(self):
        await self.client.aclose()


def _unwrap_schema_echo(data):
    """A model now and then answers in the shape of the schema it was shown: {"properties": {"table_id": "none"}} or
    {"properties": {"table_id": {"default": "none"}}}. Take the fields out of it; anything else passes through."""
    if isinstance(data, dict) and isinstance(data.get("properties"), dict) and not (set(data) - {"properties", "type", "required", "title"}):
        out = {}
        for k, v in data["properties"].items():
            if isinstance(v, dict) and set(v) & {"default", "const", "value"} \
                    and set(v) <= {"default", "const", "value", "type", "description", "title", "enum"}:
                v = next(v[key] for key in ("default", "const", "value") if key in v)
            out[k] = v
        return out
    return data


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
