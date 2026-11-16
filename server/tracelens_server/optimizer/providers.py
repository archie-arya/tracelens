"""Built-in model providers for replays. A candidate is written ``provider:model``, e.g. ``gemini:gemma-4-26b-a4b-it``,
``groq:llama-3.1-8b-instant``, ``ollama:llama3.1``, ``openai:gpt-4o-mini``, ``litellm:anthropic/claude-3-5-haiku``."""
from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any, Dict, List, Optional

import httpx

from ..config import settings
from ..evals.base import ConfigField
from .base import Completion, Provider
from .registry import provider_registry, register_provider
from .tools import parse_args


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


def _json_args(a: Any) -> str:
    return a if isinstance(a, str) else json.dumps(a if a is not None else {}, ensure_ascii=False)


def openai_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Canonical optimizer messages -> OpenAI chat wire format (assistant tool_calls and tool results included)."""
    out: List[Dict[str, Any]] = []
    for m in messages:
        role = m.get("role", "user")
        if role == "tool":
            out.append({"role": "tool", "tool_call_id": m.get("tool_call_id") or "call_0", "content": _text_content(m.get("content"))})
        elif m.get("tool_calls"):
            out.append({"role": "assistant", "content": _text_content(m.get("content")) or None,
                        "tool_calls": [{"id": c.get("id") or f"call_{i}", "type": "function",
                                        "function": {"name": c["name"], "arguments": _json_args(c.get("arguments"))}}
                                       for i, c in enumerate(m["tool_calls"])]})
        else:
            out.append({"role": role, "content": _text_content(m.get("content"))})
    return out


def openai_tools(params: Dict[str, Any]) -> Dict[str, Any]:
    tools = params.get("tools") or []
    if not tools:
        return {}
    body: Dict[str, Any] = {"tools": [{"type": "function", "function": {"name": t["name"], "description": t.get("description") or "",
                                                                         "parameters": t.get("parameters") or {"type": "object", "properties": {}}}}
                                      for t in tools]}
    tc = params.get("tool_choice")
    if tc in ("auto", "required", "none"):
        body["tool_choice"] = tc
    elif tc == "any":
        body["tool_choice"] = "required"
    return body


def parse_openai_tool_calls(msg: Dict[str, Any]) -> Optional[List[Dict[str, Any]]]:
    tcs = msg.get("tool_calls") or []
    calls = [{"id": t.get("id"), "name": (t.get("function") or {}).get("name"), "arguments": parse_args((t.get("function") or {}).get("arguments"))}
             for t in tcs if (t.get("function") or {}).get("name")]
    return calls


class OpenAICompatible(Provider):
    """Any ``/chat/completions`` API. Subclass and set ``base_url`` / ``key_env`` to add a new one."""
    base_url: str = "https://api.openai.com/v1"
    key_env: Optional[str] = "OPENAI_API_KEY"
    supports_tools = True
    config_fields = [ConfigField("rpm", "number", 60, "requests per minute")]

    def _base(self) -> str:
        return self.base_url

    def _key(self) -> Optional[str]:
        return os.environ.get(self.key_env) if self.key_env else "none"

    def available(self) -> bool:
        return bool(self._key())

    async def complete(self, model: str, messages: List[Dict[str, Any]], params: Dict[str, Any]) -> Completion:
        body: Dict[str, Any] = {"model": model, "messages": openai_messages(messages), **openai_tools(params)}
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
        msg = d["choices"][0]["message"]
        return Completion(text=(msg.get("content") or ""), input_tokens=u.get("prompt_tokens"),
                          output_tokens=u.get("completion_tokens"), latency_ms=round((time.perf_counter() - t0) * 1000, 1), raw_model=d.get("model"),
                          tool_calls=parse_openai_tool_calls(msg) if params.get("tools") else None)


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
    supports_tools = True

    def available(self) -> bool:
        return bool(settings.gemini_api_key)

    async def complete(self, model: str, messages: List[Dict[str, Any]], params: Dict[str, Any]) -> Completion:
        system = "\n".join(_text_content(m.get("content")) for m in messages if m.get("role") in ("system", "developer"))
        contents: List[Dict[str, Any]] = []
        for m in messages:
            role = m.get("role", "user")
            if role in ("system", "developer"):
                continue
            if role == "tool":  # recorded tool result, sent back as data (never executed)
                part = {"functionResponse": {"name": m.get("name") or "tool", "response": _gemini_response(m.get("content"))}}
                if contents and contents[-1]["role"] == "user" and all("functionResponse" in p for p in contents[-1]["parts"]):
                    contents[-1]["parts"].append(part)
                else:
                    contents.append({"role": "user", "parts": [part]})
                continue
            text = _text_content(m.get("content"))
            parts: List[Dict[str, Any]] = [{"text": text}] if text else []
            for i, c in enumerate(m.get("tool_calls") or []):
                fc: Dict[str, Any] = {"functionCall": {"name": c["name"], "args": c.get("arguments") if isinstance(c.get("arguments"), dict) else {}}}
                # recorded history carries no thought signatures (and may come from another model); Gemini 3 requires one,
                # and this documented placeholder skips the check
                fc["thoughtSignature"] = "skip_thought_signature_validator"
                parts.append(fc)
            if not parts:
                continue
            contents.append({"role": "model" if role == "assistant" else "user", "parts": parts})
        cfg: Dict[str, Any] = {}
        if params.get("temperature") is not None:
            cfg["temperature"] = params["temperature"]
        if params.get("top_p") is not None:
            cfg["topP"] = params["top_p"]
        if params.get("max_tokens"):
            cfg["maxOutputTokens"] = params["max_tokens"]
        if params.get("json") and not params.get("tools"):
            cfg["responseMimeType"] = "application/json"
        body: Dict[str, Any] = {"contents": contents or [{"role": "user", "parts": [{"text": ""}]}], "generationConfig": cfg}
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        if params.get("tools"):
            body["tools"] = [{"functionDeclarations": [{"name": t["name"], "description": t.get("description") or "",
                                                        "parameters": gemini_schema(t.get("parameters") or {"type": "object", "properties": {}})}
                                                       for t in params["tools"]]}]
            mode = {"required": "ANY", "any": "ANY", "none": "NONE", "auto": "AUTO"}.get(str(params.get("tool_choice") or ""))
            if mode:
                body["toolConfig"] = {"functionCallingConfig": {"mode": mode}}
        t0 = time.perf_counter()
        async with httpx.AsyncClient(timeout=90) as c:
            r = await c.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent", json=body,
                             headers={"x-goog-api-key": settings.gemini_api_key or ""})
            r.raise_for_status()
            d = r.json()
        parts = ((d.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
        u = d.get("usageMetadata") or {}
        out_tokens = (u.get("candidatesTokenCount") or 0) + (u.get("thoughtsTokenCount") or 0)
        calls = [{"id": p["functionCall"].get("id"), "name": p["functionCall"].get("name"), "arguments": p["functionCall"].get("args") or {}}
                 for p in parts if isinstance(p.get("functionCall"), dict)]
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        finish = (d.get("candidates") or [{}])[0].get("finishReason")
        if not text and not calls and finish == "MALFORMED_FUNCTION_CALL":  # the model tried to call a tool and produced invalid output
            text = "[malformed function call]"
        return Completion(text=text, input_tokens=u.get("promptTokenCount"),
                          output_tokens=out_tokens or None, latency_ms=round((time.perf_counter() - t0) * 1000, 1), raw_model=d.get("modelVersion"),
                          tool_calls=calls if params.get("tools") else None)


_GEMINI_SCHEMA_KEYS = {"type", "format", "description", "nullable", "enum", "properties", "required", "items", "minItems", "maxItems",
                       "minimum", "maximum", "anyOf", "propertyOrdering", "title", "minLength", "maxLength", "pattern"}


def gemini_schema(s: Any) -> Any:
    """JSON schema -> the OpenAPI subset accepted by Gemini's functionDeclarations.parameters."""
    if isinstance(s, list):
        return [gemini_schema(x) for x in s]
    if not isinstance(s, dict):
        return s
    out: Dict[str, Any] = {}
    for k, v in s.items():
        if k not in _GEMINI_SCHEMA_KEYS:
            continue
        if k == "properties" and isinstance(v, dict):
            out[k] = {pk: gemini_schema(pv) for pk, pv in v.items()}
        elif k == "type" and isinstance(v, list):  # ["string", "null"]
            types = [t for t in v if t != "null"]
            out["type"] = types[0] if types else "string"
            if "null" in v:
                out["nullable"] = True
        else:
            out[k] = gemini_schema(v)
    return out


def _gemini_response(content: Any) -> Dict[str, Any]:
    v = parse_args(content) if isinstance(content, str) else content
    return v if isinstance(v, dict) else {"result": v}


@register_provider
class LiteLLMProvider(Provider):
    name = "litellm"
    description = "Any of LiteLLM's 100+ providers (pip install litellm); e.g. litellm:anthropic/claude-3-5-haiku-latest"
    supports_tools = True

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
        kw.update(openai_tools(params))
        r = await litellm.acompletion(model=model, messages=openai_messages(messages), **kw)
        u = getattr(r, "usage", None)
        cost = None
        try:
            cost = litellm.completion_cost(completion_response=r)
        except Exception:
            pass
        msg = r.choices[0].message
        calls = None
        if params.get("tools"):
            calls = [{"id": getattr(t, "id", None), "name": t.function.name, "arguments": parse_args(t.function.arguments)}
                     for t in (getattr(msg, "tool_calls", None) or [])]
        return Completion(text=msg.content or "", input_tokens=getattr(u, "prompt_tokens", None),
                          output_tokens=getattr(u, "completion_tokens", None), latency_ms=round((time.perf_counter() - t0) * 1000, 1), cost=cost,
                          tool_calls=calls)


def get_provider(name: str) -> Optional[Provider]:
    return provider_registry.get(name)
