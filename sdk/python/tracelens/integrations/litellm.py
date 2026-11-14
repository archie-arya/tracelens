"""LiteLLM instrumentation (100+ providers through one API). Call ``tracelens.init`` before ``from litellm import completion``
or use ``litellm.completion(...)``."""
from __future__ import annotations

import functools

from ..client import get_client
from ._wrap import safe, wrap
from .openai import _message_out, _params, _usage


def _start(kwargs):
    model = kwargs.get("model", "")
    provider = model.split("/")[0] if "/" in model else "litellm"
    return get_client().start_observation(f"litellm.{provider}", type="generation", model=model, input=kwargs.get("messages"),
                                          model_parameters=_params(kwargs), metadata={"provider": provider, "via": "litellm"})


def _end(obs, resp):
    obs.end(output=safe(lambda: _message_out(resp)), usage=_usage(getattr(resp, "usage", None)))


def _sync(orig):
    @functools.wraps(orig)
    def wrapper(*args, **kwargs):
        if not get_client().enabled or kwargs.get("stream"):
            return orig(*args, **kwargs)
        if args and "model" not in kwargs:
            kwargs["model"] = args[0]
            args = args[1:]
        obs = _start(kwargs)
        try:
            resp = orig(*args, **kwargs)
        except BaseException as e:
            obs.end(error=e)
            raise
        _end(obs, resp)
        return resp
    return wrapper


def _async(orig):
    @functools.wraps(orig)
    async def wrapper(*args, **kwargs):
        if not get_client().enabled or kwargs.get("stream"):
            return await orig(*args, **kwargs)
        if args and "model" not in kwargs:
            kwargs["model"] = args[0]
            args = args[1:]
        obs = _start(kwargs)
        try:
            resp = await orig(*args, **kwargs)
        except BaseException as e:
            obs.end(error=e)
            raise
        _end(obs, resp)
        return resp
    return wrapper


_orig = {}


def instrument() -> bool:
    import litellm

    _orig["completion"], _orig["acompletion"] = litellm.completion, litellm.acompletion
    ok = wrap(litellm, "completion", _sync)
    wrap(litellm, "acompletion", _async)
    return ok


def uninstrument() -> None:
    try:
        import litellm
        for k, v in _orig.items():
            setattr(litellm, k, v)
    except ImportError:
        pass
