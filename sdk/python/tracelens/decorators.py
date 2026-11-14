"""``@observe`` - trace any function (sync, async, generators) with one line."""
from __future__ import annotations

import functools
import inspect
from typing import Any, Callable, Dict, Optional, TypeVar, overload

from .client import _current_obs, _current_trace, get_client

F = TypeVar("F", bound=Callable[..., Any])


def _capture_args(fn: Callable[..., Any], args: tuple, kwargs: dict) -> Any:
    try:
        sig = inspect.signature(fn)
        bound = sig.bind_partial(*args, **kwargs)
        data: Dict[str, Any] = {}
        for k, v in bound.arguments.items():
            if k in ("self", "cls"):
                continue
            param = sig.parameters.get(k)
            if param is not None and param.kind == param.VAR_KEYWORD:
                data.update(v)
            else:
                data[k] = v
        return data
    except Exception:
        return {"args": list(args), "kwargs": kwargs}


@overload
def observe(func: F) -> F: ...
@overload
def observe(func: None = None, *, name: Optional[str] = None, type: str = "span", capture_input: bool = True,
            capture_output: bool = True, **span_kwargs: Any) -> Callable[[F], F]: ...


def observe(func: Optional[Callable[..., Any]] = None, *, name: Optional[str] = None, type: str = "span",
            capture_input: bool = True, capture_output: bool = True, **span_kwargs: Any) -> Any:
    """Decorate a function to record it as a span (or ``type="generation"|"tool"|"retrieval"|"agent"``).

    The outermost decorated call creates the trace; nested calls become child spans.
    Works with ``def``, ``async def``, generators and async generators.

    ``span_kwargs`` can include ``user_id``, ``session_id``, ``tags``, ``metadata``, ``model``.
    """

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        span_name = name or getattr(fn, "__qualname__", getattr(fn, "__name__", "function")).split("<locals>.")[-1]

        def _open(args: tuple, kwargs: dict):
            kw = dict(span_kwargs)
            if capture_input:
                kw["input"] = _capture_args(fn, args, kwargs)
            return get_client().span(span_name, type=type, **kw)

        if inspect.isasyncgenfunction(fn):
            @functools.wraps(fn)
            async def agen_wrapper(*args: Any, **kwargs: Any) -> Any:
                items = []
                async with _open(args, kwargs) as obs:
                    async for item in fn(*args, **kwargs):
                        items.append(item)
                        yield item
                    if capture_output:
                        obs.output = _join(items)
            return agen_wrapper

        if inspect.isgeneratorfunction(fn):
            @functools.wraps(fn)
            def gen_wrapper(*args: Any, **kwargs: Any) -> Any:
                items = []
                with _open(args, kwargs) as obs:
                    for item in fn(*args, **kwargs):
                        items.append(item)
                        yield item
                    if capture_output:
                        obs.output = _join(items)
            return gen_wrapper

        if inspect.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                async with _open(args, kwargs) as obs:
                    result = await fn(*args, **kwargs)
                    if capture_output:
                        obs.output = result
                    return result
            return async_wrapper

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            with _open(args, kwargs) as obs:
                result = fn(*args, **kwargs)
                if capture_output:
                    obs.output = result
                return result
        return wrapper

    if func is not None and callable(func):
        return decorator(func)
    return decorator


def _join(items: list) -> Any:
    if items and all(isinstance(i, str) for i in items):
        return "".join(items)
    return items


trace_fn = observe  # alias: @tracelens.trace_fn


def update_current_trace(**kwargs: Any) -> None:
    t = _current_trace.get()
    if t is not None:
        t.update(**kwargs)


def update_current_observation(**kwargs: Any) -> None:
    o = _current_obs.get()
    if o is not None:
        o.update(**kwargs)
