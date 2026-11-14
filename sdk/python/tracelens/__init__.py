"""TraceLens - plug-and-play LLM observability.

Two lines::

    import tracelens
    tracelens.init()            # reads TRACELENS_API_KEY / TRACELENS_HOST, auto-instruments installed LLM SDKs

Then optionally::

    @tracelens.observe()
    def answer(question): ...

    with tracelens.span("retrieve", type="retrieval") as s:
        s.update(output=docs)
"""
from __future__ import annotations

from typing import Any, Iterable, Optional, Union

from . import pricing, redact
from .client import (Observation, Trace, TraceLens, get_client, get_current_observation, get_current_observation_id,
                     get_current_trace, get_current_trace_id, set_client)
from .decorators import observe, trace_fn, update_current_observation, update_current_trace
from .exporters import BatchProcessor, ConsoleExporter, Exporter, FileExporter, HttpExporter
from .integrations import instrument, register_integration, uninstrument

__version__ = "0.1.0"
__all__ = [
    "init", "observe", "trace", "span", "generation", "event", "score", "flush", "shutdown", "instrument", "uninstrument",
    "update_current_trace", "update_current_observation", "update_current_span", "get_client", "get_current_trace_id",
    "get_current_observation_id", "get_current_trace", "get_current_observation", "TraceLens", "Trace", "Observation",
    "Exporter", "HttpExporter", "ConsoleExporter", "FileExporter", "BatchProcessor", "pricing", "redact", "register_integration",
    "trace_fn", "set_client", "optimize",
]

update_current_span = update_current_observation


def init(api_key: Optional[str] = None, host: Optional[str] = None, *, auto_instrument: Union[bool, Iterable[str]] = True, **kwargs: Any) -> TraceLens:
    """Configure the global client. All options of :class:`TraceLens` are accepted.

    ``auto_instrument=True`` patches every supported SDK that is installed (OpenAI & compatibles, Anthropic,
    Google Gen AI, LiteLLM). Pass a list like ``["openai"]`` to limit, or ``False`` to disable.
    """
    old = get_client() if _has_client() else None
    client = TraceLens(api_key=api_key, host=host, **kwargs)
    set_client(client)
    if old is not None:
        old.shutdown(1)
    if auto_instrument:
        instrument(None if auto_instrument is True else list(auto_instrument))  # type: ignore[arg-type]
    return client


def _has_client() -> bool:
    from . import client as _c
    return _c._client is not None


def trace(name: str, **kwargs: Any):
    """Context manager starting a new trace: ``with tracelens.trace("chat", user_id="u1") as t: ...``"""
    return get_client().trace(name, **kwargs)


def span(name: str, type: str = "span", **kwargs: Any):
    """Context manager for a child span (creates a trace if none is active)."""
    return get_client().span(name, type=type, **kwargs)


def generation(name: str, **kwargs: Any):
    """Context manager for a manual LLM call record: set ``usage``/``model``/``output`` via ``update``."""
    return get_client().generation(name, **kwargs)


def event(name: str, **kwargs: Any) -> Observation:
    return get_client().event(name, **kwargs)


def score(name: str, value: Any, trace_id: Optional[str] = None, observation_id: Optional[str] = None, comment: Optional[str] = None) -> None:
    """Attach a score (number, bool or label) to the current/given trace - e.g. user feedback."""
    get_client().score(name, value, trace_id=trace_id, observation_id=observation_id, comment=comment)


def flush(timeout: float = 10.0) -> bool:
    return get_client().flush(timeout)


def optimize(steps: Any, candidates: Any, **kwargs: Any) -> Any:
    """Cost Optimizer: replay a step's real traffic on cheaper models; see :mod:`tracelens.optimizer`."""
    from .optimizer import optimize as _optimize
    return _optimize(steps, candidates, **kwargs)


def shutdown() -> None:
    get_client().shutdown()
