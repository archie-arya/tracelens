"""Built-in model providers for replays. A candidate is written ``provider:model``, e.g. ``gemini:gemma-4-26b-a4b-it``,
``groq:llama-3.1-8b-instant``, ``ollama:llama3.1``, ``openai:gpt-4o-mini``, ``litellm:anthropic/claude-3-5-haiku``."""
from __future__ import annotations

import asyncio
import os
import time
from typing import Any, Dict, List, Optional

import httpx

from ..config import settings
from ..evals.base import ConfigField
from .base import Completion, Provider
from .registry import provider_registry, register_provider


class RateLimiter:
    """Simple async limiter: at most ``rpm`` request starts per rolling minute."""

    def __init__(self, rpm: int):
        self.rpm = max(1, int(rpm))
        self._starts: List[float] = []
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        while True:
            async with self._lock:
                t = time.monotonic()
                self._starts = [s for s in self._starts if t - s < 60]
                if len(self._starts) < self.rpm:
                    self._starts.append(t)
                    return
                wait = 60 - (t - self._starts[0]) + 0.05
            await asyncio.sleep(wait)


_LIMITERS: Dict[str, RateLimiter] = {}


def limiter_for(provider: Provider, override_rpm: Optional[int] = None) -> RateLimiter:
    rpm = override_rpm or int(os.environ.get(f"TRACELENS_OPTIMIZER_RPM_{provider.name.upper()}", provider.default_rpm))
    lim = _LIMITERS.get(provider.name)
    if lim is None or lim.rpm != rpm:
        lim = _LIMITERS[provider.name] = RateLimiter(rpm)
    return lim


def split_spec(spec: str) -> tuple:
    prov, sep, model = spec.partition(":")
    if not sep:  # bare model name: guess the provider
        m = spec.lower()
        prov, model = ("gemini" if m.startswith(("gemini", "gemma")) else "openai"), spec
    return prov.strip().lower(), model.strip()


async def call_with_retries(provider: Provider, model: str, messages: List[Dict[str, Any]], params: Dict[str, Any],
                            rpm: Optional[int] = None, retries: int = 3, timeout: float = 90.0) -> Completion:
    lim = limiter_for(provider, rpm)
    last = "unknown error"
    for attempt in range(retries + 1):
        await lim.acquire()
        t0 = time.perf_counter()
        try:
            c = await asyncio.wait_for(provider.complete(model, messages, params), timeout)
            if c.latency_ms is None:
                c.latency_ms = round((time.perf_counter() - t0) * 1000, 1)
            return c
        except httpx.HTTPStatusError as e:
            code = e.response.status_code
            last = f"HTTP {code}: {e.response.text[:200]}"
            if code in (429, 500, 502, 503, 504) and attempt < retries:
                ra = e.response.headers.get("retry-after")
                await asyncio.sleep(float(ra) if ra and ra.replace(".", "").isdigit() else min(60, 5 * 2 ** attempt))
                continue
            break
        except asyncio.TimeoutError:
            last = f"timeout after {timeout}s"
        except Exception as e:  # noqa: BLE001
            last = f"{type(e).__name__}: {e}"[:300]
        if attempt < retries:
            await asyncio.sleep(2 * (attempt + 1))
    return Completion(error=last)


def _text_content(c: Any) -> str:
    if c is None:
        return ""
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "\n".join(_text_content(p.get("text") if isinstance(p, dict) and "text" in p else p) for p in c)
    if isinstance(c, dict):
        return str(c.get("text") or c.get("content") or c)
    return str(c)


class OpenAICompatible(Provider):
    """Any ``/chat/completions`` API. Subclass and set ``base_url`` / ``key_env`` to add a new one."""
    base_url: str = "https://api.openai.com/v1"
    key_env: Optional[str] = "OPENAI_API_KEY"
    config_fields = [ConfigField("rpm", "number", 60, "requests per minute")]

    def _base(self) -> str:
        return self.base_url

    def _key(self) -> Optional[str]:
        return os.environ.get(self.key_env) if self.key_env else "none"

    def available(self) -> bool:
        return bool(self._key())

    async def complete(self, model: str, messages: List[Dict[str, Any]], params: Dict[str, Any]) -> Completion:
        body: Dict[str, Any] = {"model": model, "messages": [{"role": m.get("role", "user"), "content": _text_content(m.get("content"))} for m in messages]}
        for k in ("temperature", "top_p", "max_tokens", "stop", "seed"):
            if params.get(k) is not None:
                body[k] = params[k]
        if params.get("json"):
            body["response_format"] = {"type": "json_object"}
        t0 = time.perf_counter()
        async with httpx.AsyncClient(timeout=90) as c:
            r = await c.post(self._base().rstrip("/") + "/chat/completions", json=body, headers={"Authorization": f"Bearer {self._key()}"})
            r.raise_for_status()
            d = r.json()
        u = d.get("usage") or {}
        return Completion(text=(d["choices"][0]["message"].get("content") or ""), input_tokens=u.get("prompt_tokens"),
                          output_tokens=u.get("completion_tokens"), latency_ms=round((time.perf_counter() - t0) * 1000, 1), raw_model=d.get("model"))


@register_provider
class OpenAIProvider(OpenAICompatible):
    name = "openai"
    description = "OpenAI or any OpenAI-compatible API (OPENAI_API_KEY, OPENAI_BASE_URL)"

    def _base(self) -> str:
        return settings.openai_base_url or self.base_url

    def _key(self) -> Optional[str]:
        return settings.openai_api_key


@register_provider
class GroqProvider(OpenAICompatible):
    name = "groq"
    description = "Groq (GROQ_API_KEY); free tier available"
    base_url = "https://api.groq.com/openai/v1"
    default_rpm = 25

    def _key(self) -> Optional[str]:
        return settings.groq_api_key


@register_provider
class OllamaProvider(OpenAICompatible):
    name = "ollama"
    description = "Local models via Ollama (OLLAMA_BASE_URL, default http://localhost:11434/v1); always $0"
    local = True
    default_rpm = 600

    def _base(self) -> str:
        return settings.ollama_base_url

    def _key(self) -> Optional[str]:
        return "ollama"


def _simple(name_: str, url: str, env: str, desc: str) -> None:
    cls = type(f"{name_.title()}Provider", (OpenAICompatible,), {"name": name_, "base_url": url, "key_env": env, "description": desc})
    register_provider(cls)


_simple("openrouter", "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY", "OpenRouter (OPENROUTER_API_KEY), hundreds of models incl. free ones")
_simple("together", "https://api.together.xyz/v1", "TOGETHER_API_KEY", "Together AI (TOGETHER_API_KEY)")
_simple("deepseek", "https://api.deepseek.com/v1", "DEEPSEEK_API_KEY", "DeepSeek (DEEPSEEK_API_KEY)")
_simple("fireworks", "https://api.fireworks.ai/inference/v1", "FIREWORKS_API_KEY", "Fireworks (FIREWORKS_API_KEY)")
_simple("mistral", "https://api.mistral.ai/v1", "MISTRAL_API_KEY", "Mistral (MISTRAL_API_KEY)")


@register_provider
class GeminiProvider(Provider):
    name = "gemini"
    description = "Google Gemini / Gemma via the Gemini API (GEMINI_API_KEY); free tier available"
    default_rpm = 12

    def available(self) -> bool:
        return bool(settings.gemini_api_key)

    async def complete(self, model: str, messages: List[Dict[str, Any]], params: Dict[str, Any]) -> Completion:
        system = "\n".join(_text_content(m.get("content")) for m in messages if m.get("role") in ("system", "developer"))
        contents = []
        for m in messages:
            role = m.get("role", "user")
            if role in ("system", "developer"):
                continue
            text = _text_content(m.get("content"))
            if not text:
                continue
            contents.append({"role": "model" if role == "assistant" else "user", "parts": [{"text": text}]})
        cfg: Dict[str, Any] = {}
        if params.get("temperature") is not None:
            cfg["temperature"] = params["temperature"]
        if params.get("top_p") is not None:
            cfg["topP"] = params["top_p"]
        if params.get("max_tokens"):
            cfg["maxOutputTokens"] = params["max_tokens"]
        if params.get("json"):
            cfg["responseMimeType"] = "application/json"
        body: Dict[str, Any] = {"contents": contents or [{"role": "user", "parts": [{"text": ""}]}], "generationConfig": cfg}
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        t0 = time.perf_counter()
        async with httpx.AsyncClient(timeout=90) as c:
            r = await c.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent", json=body,
                             headers={"x-goog-api-key": settings.gemini_api_key or ""})
            r.raise_for_status()
            d = r.json()
        parts = ((d.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
        u = d.get("usageMetadata") or {}
        out_tokens = (u.get("candidatesTokenCount") or 0) + (u.get("thoughtsTokenCount") or 0)
        return Completion(text="".join(p.get("text", "") for p in parts if not p.get("thought")), input_tokens=u.get("promptTokenCount"),
                          output_tokens=out_tokens or None, latency_ms=round((time.perf_counter() - t0) * 1000, 1), raw_model=d.get("modelVersion"))


@register_provider
class LiteLLMProvider(Provider):
    name = "litellm"
    description = "Any of LiteLLM's 100+ providers (pip install litellm); e.g. litellm:anthropic/claude-3-5-haiku-latest"

    def available(self) -> bool:
        try:
            import litellm  # noqa: F401
            return True
        except Exception:
            return False

    async def complete(self, model: str, messages: List[Dict[str, Any]], params: Dict[str, Any]) -> Completion:
        import litellm
        kw = {k: params[k] for k in ("temperature", "top_p", "max_tokens", "stop") if params.get(k) is not None}
        t0 = time.perf_counter()
        r = await litellm.acompletion(model=model, messages=messages, **kw)
        u = getattr(r, "usage", None)
        cost = None
        try:
            cost = litellm.completion_cost(completion_response=r)
        except Exception:
            pass
        return Completion(text=r.choices[0].message.content or "", input_tokens=getattr(u, "prompt_tokens", None),
                          output_tokens=getattr(u, "completion_tokens", None), latency_ms=round((time.perf_counter() - t0) * 1000, 1), cost=cost)


def get_provider(name: str) -> Optional[Provider]:
    return provider_registry.get(name)
