"""Pluggable model pricing table (USD per 1M tokens).

Override or extend at runtime::

    import tracelens
    tracelens.pricing.register("my-finetune", input=0.5, output=1.5)
    tracelens.pricing.set_calculator(lambda model, inp, out: ...)   # full control

Prices are list prices at time of writing and only used for cost estimates.
"""
from __future__ import annotations

import re
from typing import Callable, Dict, Optional, Tuple

# model prefix -> (input $/1M, output $/1M). Longest prefix wins.
_PRICES: Dict[str, Tuple[float, float]] = {
    # OpenAI
    "gpt-5-nano": (0.05, 0.40), "gpt-5-mini": (0.25, 2.00), "gpt-5": (1.25, 10.00),
    "gpt-4.1-nano": (0.10, 0.40), "gpt-4.1-mini": (0.40, 1.60), "gpt-4.1": (2.00, 8.00),
    "gpt-4o-mini": (0.15, 0.60), "gpt-4o": (2.50, 10.00), "gpt-4-turbo": (10.0, 30.0),
    "gpt-3.5-turbo": (0.50, 1.50), "o4-mini": (1.10, 4.40), "o3-mini": (1.10, 4.40), "o3": (2.00, 8.00), "o1": (15.0, 60.0),
    "text-embedding-3-small": (0.02, 0.0), "text-embedding-3-large": (0.13, 0.0),
    # Anthropic
    "claude-opus-4": (15.0, 75.0), "claude-sonnet-4": (3.0, 15.0), "claude-3-7-sonnet": (3.0, 15.0),
    "claude-3-5-sonnet": (3.0, 15.0), "claude-3-5-haiku": (0.80, 4.0), "claude-haiku-4": (1.0, 5.0),
    "claude-3-haiku": (0.25, 1.25), "claude-3-opus": (15.0, 75.0),
    # Google
    "gemini-2.5-pro": (1.25, 10.0), "gemini-2.5-flash-lite": (0.10, 0.40), "gemini-2.5-flash": (0.30, 2.50),
    "gemini-2.0-flash-lite": (0.075, 0.30), "gemini-2.0-flash": (0.10, 0.40), "gemini-1.5-pro": (1.25, 5.0),
    "gemini-1.5-flash": (0.075, 0.30), "gemini-flash-lite-latest": (0.10, 0.40), "gemini-flash-latest": (0.30, 2.50),
    "gemini-pro-latest": (1.25, 10.0), "gemini-3-flash": (0.50, 3.0), "gemini-3.1-flash-lite": (0.10, 0.40),
    "gemini-3-pro": (2.0, 12.0), "gemini-3-flash-preview": (0.50, 3.0), "gemma-": (0.0, 0.0), "gemini-3.1-pro": (2.0, 12.0), "text-embedding-004": (0.0, 0.0),
    # Groq / open models (Groq list prices)
    "llama-3.1-8b-instant": (0.05, 0.08), "llama-3.3-70b-versatile": (0.59, 0.79), "llama3-70b": (0.59, 0.79),
    "llama3-8b": (0.05, 0.08), "mixtral-8x7b": (0.24, 0.24), "gemma2-9b-it": (0.20, 0.20),
    "deepseek-chat": (0.27, 1.10), "deepseek-reasoner": (0.55, 2.19), "mistral-large": (2.0, 6.0),
    "mistral-small": (0.10, 0.30), "qwen": (0.10, 0.30),
}
_PROVIDER_PREFIX = re.compile(r"^(models/|openai/|anthropic/|gemini/|google/|groq/|together_ai/|ollama/|openrouter/[^/]+/|[a-z0-9_-]+/)")
_calculator: Optional[Callable[[str, int, int], Optional[float]]] = None


def register(model: str, input: float, output: float) -> None:  # noqa: A002
    """Register/override price for a model prefix (USD per 1M tokens)."""
    _PRICES[model.lower()] = (float(input), float(output))


def set_calculator(fn: Optional[Callable[[str, int, int], Optional[float]]]) -> None:
    """Replace cost calculation entirely: ``fn(model, input_tokens, output_tokens) -> usd | None``."""
    global _calculator
    _calculator = fn


def table() -> Dict[str, Tuple[float, float]]:
    return dict(_PRICES)


def lookup(model: Optional[str]) -> Optional[Tuple[float, float]]:
    if not model:
        return None
    m = model.lower().strip()
    candidates = [m, _PROVIDER_PREFIX.sub("", m)]
    for cand in candidates:
        best = None
        for prefix in _PRICES:
            if cand.startswith(prefix) and (best is None or len(prefix) > len(best)):
                best = prefix
        if best:
            return _PRICES[best]
    return None


def calculate(model: Optional[str], input_tokens: Optional[int], output_tokens: Optional[int]) -> Optional[float]:
    if _calculator is not None:
        try:
            return _calculator(model or "", input_tokens or 0, output_tokens or 0)
        except Exception:
            return None
    p = lookup(model)
    if p is None or (input_tokens is None and output_tokens is None):
        return None
    return round(((input_tokens or 0) * p[0] + (output_tokens or 0) * p[1]) / 1_000_000, 10)
