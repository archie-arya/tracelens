"""Auto-instrumentation for popular LLM client libraries.

Each integration patches the library's methods in place, so every client instance
(and every OpenAI-compatible provider like Groq, Together, OpenRouter, Ollama, vLLM)
is traced automatically. Calls nest under the active trace/span if there is one.
"""
from __future__ import annotations

import logging
from typing import Dict, Iterable, Optional

logger = logging.getLogger("tracelens")

_REGISTRY = {
    "openai": "tracelens.integrations.openai",
    "anthropic": "tracelens.integrations.anthropic",
    "google_genai": "tracelens.integrations.google_genai",
    "litellm": "tracelens.integrations.litellm",
}


def register_integration(name: str, module_path: str) -> None:
    """Register a third-party integration module exposing ``instrument() -> bool``."""
    _REGISTRY[name] = module_path


def instrument(libraries: Optional[Iterable[str]] = None) -> Dict[str, bool]:
    """Instrument the given libraries (default: all that are installed). Returns {name: patched}."""
    import importlib

    results: Dict[str, bool] = {}
    for name in (libraries or list(_REGISTRY)):
        try:
            mod = importlib.import_module(_REGISTRY[name])
            results[name] = bool(mod.instrument())
        except ImportError:
            results[name] = False
        except Exception as e:  # never break the host app
            logger.warning("tracelens: failed to instrument %s: %s", name, e)
            results[name] = False
    return results


def uninstrument() -> None:
    import importlib

    for path in _REGISTRY.values():
        try:
            importlib.import_module(path).uninstrument()
        except Exception:
            pass
