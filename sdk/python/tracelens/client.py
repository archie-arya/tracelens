"""Core tracing primitives: client, traces, observations (spans / generations / events)."""
from __future__ import annotations

import atexit
import contextvars
import datetime as _dt
import json
import logging
import os
import random
import threading
import traceback
import urllib.request
import uuid
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

from . import pricing
from ._serialize import to_jsonable
from .exporters import BatchProcessor, ConsoleExporter, Exporter, HttpExporter
from .redact import redact_pii as _default_redactor

logger = logging.getLogger("tracelens")
_UNSET: Any = object()

OBSERVATION_TYPES = ("span", "generation", "event", "tool", "retrieval", "embedding", "agent", "chain")

_current_trace: contextvars.ContextVar[Optional["Trace"]] = contextvars.ContextVar("tracelens_trace", default=None)
_current_obs: contextvars.ContextVar[Optional["Observation"]] = contextvars.ContextVar("tracelens_obs", default=None)


def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def _iso(t: Optional[_dt.datetime]) -> Optional[str]:
    return t.isoformat().replace("+00:00", "Z") if t else None


def _new_id() -> str:
    return uuid.uuid4().hex


def _env_bool(name: str, default: Optional[bool] = None) -> Optional[bool]:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


class Trace:
    """A single end-to-end request / task. Holds nested observations."""

    def __init__(self, client: "TraceLens", name: str, *, id: Optional[str] = None, user_id: Optional[str] = None,
                 session_id: Optional[str] = None, tags: Optional[Iterable[str]] = None, metadata: Optional[Dict[str, Any]] = None,
                 input: Any = None, release: Optional[str] = None, environment: Optional[str] = None, sampled: bool = True):
        self.client = client
        self.id = id or _new_id()
        self.name = name
        self.user_id = user_id
        self.session_id = session_id
        self.tags: List[str] = list(dict.fromkeys(list(client.default_tags) + list(tags or [])))
        self.metadata: Dict[str, Any] = {**client.default_metadata, **(metadata or {})}
        self.input = input
        self.output: Any = None
        self.release = release or client.release
        self.environment = environment or client.environment
        self.start_time = _now()
        self.end_time: Optional[_dt.datetime] = None
        self.sampled = sampled
        self._ended = False

    def update(self, *, name: Optional[str] = None, user_id: Optional[str] = None, session_id: Optional[str] = None,
               tags: Optional[Iterable[str]] = None, metadata: Optional[Dict[str, Any]] = None, input: Any = _UNSET,
               output: Any = _UNSET, release: Optional[str] = None, environment: Optional[str] = None) -> "Trace":
        if name is not None: self.name = name
        if user_id is not None: self.user_id = user_id
        if session_id is not None: self.session_id = session_id
        if tags: self.tags = list(dict.fromkeys(self.tags + list(tags)))
        if metadata: self.metadata.update(metadata)
        if input is not _UNSET: self.input = input
        if output is not _UNSET: self.output = output
        if release is not None: self.release = release
        if environment is not None: self.environment = environment
        if self._ended:  # late update -> re-send (server upserts)
            self.client._emit("trace", self.to_body())
        return self

    def score(self, name: str, value: Any, comment: Optional[str] = None, observation_id: Optional[str] = None) -> None:
        self.client.score(name, value, trace_id=self.id, observation_id=observation_id, comment=comment)

    def end(self, output: Any = _UNSET) -> None:
        if output is not _UNSET:
            self.output = output
        if self._ended:
            return
        self._ended = True
        self.end_time = _now()
        if self.sampled:
            self.client._emit("trace", self.to_body())

    def to_body(self) -> Dict[str, Any]:
        c = self.client
        return {
            "id": self.id, "name": self.name, "user_id": self.user_id, "session_id": self.session_id,
            "tags": self.tags, "metadata": to_jsonable(self.metadata),
            "input": c._mask(self.input) if c.capture_io else None,
            "output": c._mask(self.output) if c.capture_io else None,
            "release": self.release, "environment": self.environment,
            "start_time": _iso(self.start_time), "end_time": _iso(self.end_time),
            "sdk": "python",
        }

    def __repr__(self) -> str:
        return f"<Trace {self.name!r} id={self.id}>"


class Observation:
    """A unit of work inside a trace: span, generation (LLM call), tool, retrieval, event..."""

    def __init__(self, trace: Trace, name: str, type: str = "span", *, parent_id: Optional[str] = None, id: Optional[str] = None,
                 input: Any = None, metadata: Optional[Dict[str, Any]] = None, model: Optional[str] = None,
                 model_parameters: Optional[Dict[str, Any]] = None, level: str = "DEFAULT"):
        self.trace = trace
        self.id = id or _new_id()
        self.parent_id = parent_id
        self.name = name
        self.type = type if type in OBSERVATION_TYPES else "span"
        self.input = input
        self.output: Any = None
        self.metadata: Dict[str, Any] = dict(metadata or {})
        self.model = model
        self.model_parameters = model_parameters
        self.prompt_tokens: Optional[int] = None
        self.completion_tokens: Optional[int] = None
        self.total_tokens: Optional[int] = None
        self.cost: Optional[float] = None
        self.level = level
        self.status_message: Optional[str] = None
        self.start_time = _now()
        self.completion_start_time: Optional[_dt.datetime] = None
        self.end_time: Optional[_dt.datetime] = None
        self._ended = False

    @property
    def trace_id(self) -> str:
        return self.trace.id

    def update(self, *, name: Optional[str] = None, input: Any = _UNSET, output: Any = _UNSET, metadata: Optional[Dict[str, Any]] = None,
               model: Optional[str] = None, model_parameters: Optional[Dict[str, Any]] = None, usage: Optional[Dict[str, Any]] = None,
               cost: Optional[float] = None, level: Optional[str] = None, status_message: Optional[str] = None,
               completion_start_time: Optional[_dt.datetime] = None) -> "Observation":
        if name is not None: self.name = name
        if input is not _UNSET: self.input = input
        if output is not _UNSET: self.output = output
        if metadata: self.metadata.update(metadata)
        if model is not None: self.model = model
        if model_parameters is not None: self.model_parameters = model_parameters
        if usage: self.set_usage(**usage)
        if cost is not None: self.cost = cost
        if level is not None: self.level = level
        if status_message is not None: self.status_message = status_message
        if completion_start_time is not None: self.completion_start_time = completion_start_time
        return self

    def set_usage(self, input: Optional[int] = None, output: Optional[int] = None, total: Optional[int] = None, **aliases: Any) -> None:
        input = input if input is not None else aliases.get("prompt_tokens", aliases.get("input_tokens"))
        output = output if output is not None else aliases.get("completion_tokens", aliases.get("output_tokens"))
        total = total if total is not None else aliases.get("total_tokens")
        if input is not None: self.prompt_tokens = int(input)
        if output is not None: self.completion_tokens = int(output)
        if total is not None: self.total_tokens = int(total)
        elif self.prompt_tokens is not None or self.completion_tokens is not None:
            self.total_tokens = (self.prompt_tokens or 0) + (self.completion_tokens or 0)

    def mark_first_token(self) -> None:
        if self.completion_start_time is None:
            self.completion_start_time = _now()

    def record_error(self, error: BaseException) -> None:
        self.level = "ERROR"
        self.status_message = f"{type(error).__name__}: {error}"[:2000]
        self.metadata["exception"] = {"type": type(error).__name__, "message": str(error)[:2000],
                                      "traceback": "".join(traceback.format_exception(type(error), error, error.__traceback__))[-4000:]}

    def score(self, name: str, value: Any, comment: Optional[str] = None) -> None:
        self.trace.client.score(name, value, trace_id=self.trace.id, observation_id=self.id, comment=comment)

    def end(self, output: Any = _UNSET, error: Optional[BaseException] = None, **kwargs: Any) -> None:
        if self._ended:
            return
        if output is not _UNSET:
            self.output = output
        if kwargs:
            self.update(**kwargs)
        if error is not None:
            self.record_error(error)
        self._ended = True
        self.end_time = _now()
        if self.cost is None and self.model and (self.prompt_tokens is not None or self.completion_tokens is not None):
            self.cost = pricing.calculate(self.model, self.prompt_tokens, self.completion_tokens)
        if self.trace.sampled:
            self.trace.client._emit("observation", self.to_body())

    def to_body(self) -> Dict[str, Any]:
        c = self.trace.client
        return {
            "id": self.id, "trace_id": self.trace.id, "parent_id": self.parent_id, "type": self.type, "name": self.name,
            "input": c._mask(self.input) if c.capture_io else None,
            "output": c._mask(self.output) if c.capture_io else None,
            "metadata": to_jsonable(self.metadata), "model": self.model, "model_parameters": to_jsonable(self.model_parameters),
            "prompt_tokens": self.prompt_tokens, "completion_tokens": self.completion_tokens, "total_tokens": self.total_tokens,
            "cost": self.cost, "level": self.level, "status_message": self.status_message,
            "start_time": _iso(self.start_time), "end_time": _iso(self.end_time),
            "completion_start_time": _iso(self.completion_start_time),
        }

    def __repr__(self) -> str:
        return f"<Observation {self.type}:{self.name!r} id={self.id}>"


class _TraceContext:
    """``with tracelens.trace(...)`` / ``async with`` support."""

    def __init__(self, client: "TraceLens", name: str, kwargs: Dict[str, Any]):
        self.client, self.name, self.kwargs = client, name, kwargs
        self.trace: Optional[Trace] = None

    def __enter__(self) -> Trace:
        self.trace = self.client.start_trace(self.name, **self.kwargs)
        self._t1 = _current_trace.set(self.trace)
        self._t2 = _current_obs.set(None)
        return self.trace

    def __exit__(self, exc_type, exc, tb) -> None:
        assert self.trace is not None
        if exc is not None:
            self.trace.metadata.setdefault("error", f"{exc_type.__name__}: {exc}"[:2000])
        _current_obs.reset(self._t2)
        _current_trace.reset(self._t1)
        self.trace.end()

    async def __aenter__(self) -> Trace:
        return self.__enter__()

    async def __aexit__(self, exc_type, exc, tb) -> None:
        self.__exit__(exc_type, exc, tb)


class _ObservationContext:
    """``with tracelens.span(...)`` / ``async with`` support. Creates a trace automatically if needed."""

    def __init__(self, client: "TraceLens", name: str, type: str, kwargs: Dict[str, Any], trace_kwargs: Optional[Dict[str, Any]] = None):
        self.client, self.name, self.type, self.kwargs = client, name, type, kwargs
        self.trace_kwargs = trace_kwargs or {}
        self.obs: Optional[Observation] = None
        self._auto_trace: Optional[Trace] = None

    def __enter__(self) -> Observation:
        trace = _current_trace.get()
        if trace is None:
            trace = self._auto_trace = self.client.start_trace(self.name, input=self.kwargs.get("input"), **self.trace_kwargs)
            self._tt = _current_trace.set(trace)
        self.obs = self.client.start_observation(self.name, type=self.type, trace=trace, **self.kwargs)
        self._to = _current_obs.set(self.obs)
        return self.obs

    def __exit__(self, exc_type, exc, tb) -> None:
        obs = self.obs
        assert obs is not None
        if exc is not None and not isinstance(exc, GeneratorExit):
            obs.record_error(exc)
        obs.end()
        _current_obs.reset(self._to)
        if self._auto_trace is not None:
            if self._auto_trace.output is None:
                self._auto_trace.output = obs.output
            if self._auto_trace.input is None:
                self._auto_trace.input = obs.input
            if exc is not None:
                self._auto_trace.metadata.setdefault("error", f"{exc_type.__name__}: {exc}"[:2000])
            _current_trace.reset(self._tt)
            self._auto_trace.end()

    async def __aenter__(self) -> Observation:
        return self.__enter__()

    async def __aexit__(self, exc_type, exc, tb) -> None:
        self.__exit__(exc_type, exc, tb)


class TraceLens:
    """The SDK client. Usually created once via :func:`tracelens.init`."""

    def __init__(self, api_key: Optional[str] = None, host: Optional[str] = None, *, enabled: Optional[bool] = None,
                 sample_rate: Optional[float] = None, mask: Optional[Callable[[Any], Any]] = None, redact_pii: bool = False,
                 exporters: Optional[Sequence[Exporter]] = None, batch_size: int = 100, flush_interval: float = 1.0,
                 max_queue: int = 10_000, timeout: float = 5.0, release: Optional[str] = None, environment: Optional[str] = None,
                 default_tags: Optional[Iterable[str]] = None, default_metadata: Optional[Dict[str, Any]] = None,
                 capture_io: bool = True, debug: Optional[bool] = None):
        self.api_key = api_key or os.environ.get("TRACELENS_API_KEY")
        self.host = (host or os.environ.get("TRACELENS_HOST") or "http://localhost:8000").rstrip("/")
        self.sample_rate = float(sample_rate if sample_rate is not None else os.environ.get("TRACELENS_SAMPLE_RATE", 1.0))
        self.release = release or os.environ.get("TRACELENS_RELEASE")
        self.environment = environment or os.environ.get("TRACELENS_ENVIRONMENT")
        self.default_tags = list(default_tags or [])
        self.default_metadata = dict(default_metadata or {})
        self.capture_io = capture_io
        self.timeout = timeout
        maskers = []
        if redact_pii:
            maskers.append(_default_redactor)
        if mask:
            maskers.append(mask)
        self._maskers = maskers
        debug = debug if debug is not None else _env_bool("TRACELENS_DEBUG", False)
        if debug:
            logging.basicConfig()
            logger.setLevel(logging.DEBUG)

        exps: List[Exporter] = list(exporters or [])
        if self.api_key:
            exps.append(HttpExporter(self.host, self.api_key, timeout=timeout))
        if debug and not any(isinstance(e, ConsoleExporter) for e in exps):
            exps.append(ConsoleExporter())
        env_enabled = _env_bool("TRACELENS_ENABLED")
        want = enabled if enabled is not None else (env_enabled if env_enabled is not None else True)
        self.enabled = bool(want) and bool(exps)
        if not self.enabled:
            logger.info("tracelens: no API key/exporter configured - running in no-op mode")
        self._processor: Optional[BatchProcessor] = BatchProcessor(exps, batch_size, flush_interval, max_queue) if self.enabled else None
        self._lock = threading.Lock()
        atexit.register(self.shutdown)

    # ---------------------------------------------------------------- internals
    def _mask(self, value: Any) -> Any:
        value = to_jsonable(value)
        for m in self._maskers:
            try:
                value = m(value)
            except Exception as e:  # never break the app because of a mask fn
                logger.debug("tracelens mask failed: %s", e)
        return value

    def _emit(self, kind: str, body: Dict[str, Any]) -> None:
        if self._processor is not None:
            self._processor.enqueue({"type": kind, "body": body})

    # ---------------------------------------------------------------- public API
    def start_trace(self, name: str, **kwargs: Any) -> Trace:
        sampled = self.enabled and (self.sample_rate >= 1.0 or random.random() < self.sample_rate)
        return Trace(self, name, sampled=sampled, **kwargs)

    def start_observation(self, name: str, type: str = "span", trace: Optional[Trace] = None, parent: Optional[Observation] = None, **kwargs: Any) -> Observation:
        """Start an observation without changing context (call ``.end()`` yourself)."""
        trace = trace or _current_trace.get()
        auto = False
        if trace is None:
            trace = self.start_trace(name, input=kwargs.get("input"))
            auto = True
        parent = parent or _current_obs.get()
        if parent is not None and parent.trace is not trace:
            parent = None
        obs = Observation(trace, name, type, parent_id=parent.id if parent else kwargs.pop("parent_id", None), **{k: v for k, v in kwargs.items() if k != "parent_id"})
        if auto:  # standalone observation -> end trace together with it
            orig_end = obs.end

            def _end(*a: Any, **k: Any) -> None:
                orig_end(*a, **k)
                trace.update(output=obs.output)
                trace.end()
            obs.end = _end  # type: ignore[method-assign]
        return obs

    def trace(self, name: str, **kwargs: Any) -> _TraceContext:
        return _TraceContext(self, name, kwargs)

    def span(self, name: str, type: str = "span", **kwargs: Any) -> _ObservationContext:
        trace_kwargs = {k: kwargs.pop(k) for k in ("user_id", "session_id", "tags") if k in kwargs}
        return _ObservationContext(self, name, type, kwargs, trace_kwargs)

    def generation(self, name: str, **kwargs: Any) -> _ObservationContext:
        return self.span(name, type="generation", **kwargs)

    def event(self, name: str, **kwargs: Any) -> Observation:
        obs = self.start_observation(name, type="event", **kwargs)
        obs.end()
        return obs

    def score(self, name: str, value: Any, trace_id: Optional[str] = None, observation_id: Optional[str] = None, comment: Optional[str] = None) -> None:
        trace_id = trace_id or get_current_trace_id()
        if not trace_id:
            logger.warning("tracelens.score: no trace_id and no active trace")
            return
        body: Dict[str, Any] = {"id": _new_id(), "trace_id": trace_id, "observation_id": observation_id, "name": name, "comment": comment, "source": "sdk"}
        if isinstance(value, bool):
            body["value"] = 1.0 if value else 0.0
        elif isinstance(value, (int, float)):
            body["value"] = float(value)
        else:
            body["string_value"] = str(value)
        self._emit("score", body)

    def api(self, method: str, path: str, body: Any = None, timeout: Optional[float] = None) -> Any:
        """Call the TraceLens REST API with this client's key (used by experiments & helpers)."""
        data = json.dumps(to_jsonable(body)).encode() if body is not None else None
        req = urllib.request.Request(self.host + path, data=data, method=method,
                                     headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout or max(self.timeout, 30)) as r:
            raw = r.read()
        return json.loads(raw) if raw else None

    def flush(self, timeout: float = 10.0) -> bool:
        return self._processor.flush(timeout) if self._processor else True

    def shutdown(self, timeout: float = 5.0) -> None:
        if self._processor is not None:
            self._processor.shutdown(timeout)
            self._processor = None


# ------------------------------------------------------------------- module-level helpers
_client: Optional[TraceLens] = None
_client_lock = threading.Lock()


def get_client() -> TraceLens:
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                _client = TraceLens()
    return _client


def set_client(client: Optional[TraceLens]) -> None:
    global _client
    _client = client


def get_current_trace() -> Optional[Trace]:
    return _current_trace.get()


def get_current_observation() -> Optional[Observation]:
    return _current_obs.get()


def get_current_trace_id() -> Optional[str]:
    t = _current_trace.get()
    return t.id if t else None


def get_current_observation_id() -> Optional[str]:
    o = _current_obs.get()
    return o.id if o else None
