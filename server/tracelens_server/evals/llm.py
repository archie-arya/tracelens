"""Provider-agnostic judge LLM client with a fallback chain.

Configure with ``TRACELENS_JUDGE_MODELS="gemini:gemini-flash-lite-latest,groq:llama-3.1-8b-instant,ollama:llama3.1"``.
Providers: ``gemini`` (GEMINI_API_KEY), ``openai`` (OPENAI_API_KEY + OPENAI_BASE_URL - any OpenAI-compatible API),
``groq`` (GROQ_API_KEY), ``ollama`` (OLLAMA_BASE_URL, no key).
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import httpx

from ..config import settings

logger = logging.getLogger("tracelens.judge")


class JudgeUnavailable(RuntimeError):
    pass


def _parse_json(text: str) -> Dict[str, Any]:
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        text = m.group(1)
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            return json.loads(m.group(0))
        raise


class JudgeLLM:
    def __init__(self, chain: Optional[List[str]] = None, timeout: float = 45.0):
        self.chain: List[Tuple[str, str]] = []
        for spec in chain or settings.judge_models:
            prov, _, model = spec.partition(":")
            if model:
                self.chain.append((prov.strip().lower(), model.strip()))
        self.timeout = timeout
        self._cooldown: Dict[Tuple[str, str], float] = {}

    def available(self) -> bool:
        return any(self._has_creds(p) for p, _ in self.chain)

    @staticmethod
    def _has_creds(provider: str) -> bool:
        return {"gemini": bool(settings.gemini_api_key), "openai": bool(settings.openai_api_key), "groq": bool(settings.groq_api_key), "ollama": True}.get(provider, False)

    async def complete_json(self, system: str, prompt: str) -> Tuple[Dict[str, Any], str]:
        """Return (parsed_json, "provider:model"). Tries each model in the chain until one works."""
        errors = []
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for prov, model in self.chain:
                if not self._has_creds(prov) or self._cooldown.get((prov, model), 0) > time.time():
                    continue
                for attempt in range(2):
                    try:
                        text = await (self._gemini(client, model, system, prompt) if prov == "gemini" else self._openai(client, prov, model, system, prompt))
                        return _parse_json(text), f"{prov}:{model}"
                    except httpx.HTTPStatusError as e:
                        code = e.response.status_code
                        errors.append(f"{prov}:{model} HTTP {code}")
                        if code in (404, 400, 403):
                            self._cooldown[(prov, model)] = time.time() + 3600
                            break
                        if code == 429:
                            self._cooldown[(prov, model)] = time.time() + 60
                            break
                    except Exception as e:
                        errors.append(f"{prov}:{model} {type(e).__name__}: {e}")
                    if attempt == 0:
                        await _sleep(1.0)
        raise JudgeUnavailable("; ".join(errors) or "no judge model configured (set GEMINI_API_KEY or TRACELENS_JUDGE_MODELS)")

    async def _gemini(self, client: httpx.AsyncClient, model: str, system: str, prompt: str) -> str:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        body = {"systemInstruction": {"parts": [{"text": system}]}, "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0, "responseMimeType": "application/json", "maxOutputTokens": 1024}}
        r = await client.post(url, json=body, headers={"x-goog-api-key": settings.gemini_api_key})
        r.raise_for_status()
        data = r.json()
        return "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])

    async def _openai(self, client: httpx.AsyncClient, prov: str, model: str, system: str, prompt: str) -> str:
        base, key = {"openai": (settings.openai_base_url, settings.openai_api_key), "groq": ("https://api.groq.com/openai/v1", settings.groq_api_key),
                     "ollama": (settings.ollama_base_url, "ollama")}[prov]
        body = {"model": model, "temperature": 0, "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
                "response_format": {"type": "json_object"}}
        r = await client.post(base.rstrip("/") + "/chat/completions", json=body, headers={"Authorization": f"Bearer {key}"})
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]


async def _sleep(s: float) -> None:
    import asyncio
    await asyncio.sleep(s)


_judge: Optional[JudgeLLM] = None


def get_judge() -> JudgeLLM:
    global _judge
    if _judge is None:
        _judge = JudgeLLM()
    return _judge


def set_judge(j: Optional[JudgeLLM]) -> None:
    global _judge
    _judge = j
