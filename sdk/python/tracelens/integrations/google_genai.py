"""Google Gen AI SDK (``google-genai``: ``from google import genai``) instrumentation."""
from __future__ import annotations

import functools
from typing import Any, Dict, Optional

from ..client import get_client
from ._wrap import safe, unwrap_all, wrap


def _contents(contents: Any) -> Any:
    if isinstance(contents, str):
        return [{"role": "user", "content": contents}]
    return contents


def _config(cfg: Any) -> Dict[str, Any]:
    if cfg is None:
        return {}
    d = cfg if isinstance(cfg, dict) else safe(lambda: cfg.model_dump(exclude_none=True), {}) or {}
    keep = ("temperature", "top_p", "top_k", "max_output_tokens", "candidate_count", "stop_sequences", "response_mime_type", "seed")
    return {k: d[k] for k in keep if k in d and d[k] is not None}


def _usage(resp: Any) -> Optional[Dict[str, Any]]:
    um = getattr(resp, "usage_metadata", None)
    if um is None:
        return None
    out_tokens = (getattr(um, "candidates_token_count", None) or 0) + (getattr(um, "thoughts_token_count", None) or 0)
    return {"input": getattr(um, "prompt_token_count", None), "output": out_tokens, "total": getattr(um, "total_token_count", None)}


def _text(resp: Any) -> Any:
    t = safe(lambda: resp.text)
    if t is not None:
        return t
    # no plain text (e.g. a thinking model hit max_output_tokens): keep visible parts only, never the model's thoughts
    parts = safe(lambda: resp.candidates[0].content.parts) or []
    visible = "".join((getattr(p, "text", None) or "") for p in parts if not getattr(p, "thought", False))
    return visible or None


def _start(model: Any, kwargs: Dict[str, Any], args: tuple):
    contents = kwargs.get("contents", args[0] if args else None)
    cfg = kwargs.get("config")
    sys_instr = safe(lambda: cfg.get("system_instruction") if isinstance(cfg, dict) else getattr(cfg, "system_instruction", None))
    inp = _contents(contents)
    if sys_instr:
        inp = [{"role": "system", "content": safe(lambda: str(sys_instr))}] + (inp if isinstance(inp, list) else [inp])
    return get_client().start_observation("gemini.generate_content", type="generation", model=str(model or "").replace("models/", ""),
                                          input=inp, model_parameters=_config(cfg), metadata={"provider": "gemini"})


def _end(obs: Any, resp: Any) -> None:
    fr = safe(lambda: str(resp.candidates[0].finish_reason))
    obs.end(output=_text(resp), usage=_usage(resp), metadata={"finish_reason": fr} if fr else None)


def _sync(orig):
    @functools.wraps(orig)
    def wrapper(self, *args, **kwargs):
        if not get_client().enabled:
            return orig(self, *args, **kwargs)
        obs = _start(kwargs.get("model"), kwargs, args)
        try:
            resp = orig(self, *args, **kwargs)
        except BaseException as e:
            obs.end(error=e)
            raise
        _end(obs, resp)
        return resp
    return wrapper


def _async(orig):
    @functools.wraps(orig)
    async def wrapper(self, *args, **kwargs):
        if not get_client().enabled:
            return await orig(self, *args, **kwargs)
        obs = _start(kwargs.get("model"), kwargs, args)
        try:
            resp = await orig(self, *args, **kwargs)
        except BaseException as e:
            obs.end(error=e)
            raise
        _end(obs, resp)
        return resp
    return wrapper


def _sync_stream(orig):
    @functools.wraps(orig)
    def wrapper(self, *args, **kwargs):
        if not get_client().enabled:
            yield from orig(self, *args, **kwargs)
            return
        obs = _start(kwargs.get("model"), kwargs, args)
        parts, last = [], None
        try:
            for chunk in orig(self, *args, **kwargs):
                obs.mark_first_token()
                parts.append(safe(lambda: chunk.text) or "")
                last = chunk
                yield chunk
        except BaseException as e:
            obs.end(output="".join(parts), error=e)
            raise
        obs.end(output="".join(parts), usage=_usage(last) if last is not None else None, metadata={"stream": True})
    return wrapper


def _async_stream(orig):
    @functools.wraps(orig)
    async def wrapper(self, *args, **kwargs):
        stream = await orig(self, *args, **kwargs)
        if not get_client().enabled:
            return stream
        obs = _start(kwargs.get("model"), kwargs, args)

        async def gen():
            parts, last = [], None
            try:
                async for chunk in stream:
                    obs.mark_first_token()
                    parts.append(safe(lambda: chunk.text) or "")
                    last = chunk
                    yield chunk
            except BaseException as e:
                obs.end(output="".join(parts), error=e)
                raise
            obs.end(output="".join(parts), usage=_usage(last) if last is not None else None, metadata={"stream": True})
        return gen()
    return wrapper


def instrument() -> bool:
    from google.genai import models

    ok = wrap(models.Models, "generate_content", _sync)
    wrap(models.Models, "generate_content_stream", _sync_stream)
    wrap(models.AsyncModels, "generate_content", _async)
    wrap(models.AsyncModels, "generate_content_stream", _async_stream)
    return ok


def uninstrument() -> None:
    unwrap_all("google.genai")
