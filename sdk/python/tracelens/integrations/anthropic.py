"""Anthropic SDK instrumentation (messages API, sync/async, streaming)."""
from __future__ import annotations

import functools
from typing import Any, Dict

from ..client import get_client
from ._tools import anthropic_tool_definitions, messages_jsonable, parse_args, tool_metadata, with_tool_calls
from ._wrap import safe, unwrap_all, wrap

PARAMS = ("max_tokens", "temperature", "top_p", "top_k", "stop_sequences", "tool_choice")


def _start(kwargs: Dict[str, Any]):
    msgs = messages_jsonable(list(kwargs.get("messages") or []))
    if kwargs.get("system"):
        msgs = [{"role": "system", "content": kwargs["system"]}] + msgs
    return get_client().start_observation("anthropic.messages", type="generation", model=kwargs.get("model"), input=msgs,
                                          model_parameters={k: kwargs[k] for k in PARAMS if k in kwargs},
                                          metadata={"provider": "anthropic", **safe(lambda: tool_metadata(anthropic_tool_definitions(kwargs.get("tools"))), {})})


def _out(resp: Any) -> Any:
    blocks = getattr(resp, "content", None) or []
    texts = [b.text for b in blocks if getattr(b, "type", None) == "text"]
    tools = [{"id": getattr(b, "id", None), "name": b.name, "arguments": parse_args(b.input)} for b in blocks if getattr(b, "type", None) == "tool_use"]
    return with_tool_calls("".join(texts), tools)


def _end(obs: Any, resp: Any) -> None:
    u = getattr(resp, "usage", None)
    obs.end(output=_out(resp), usage={"input": getattr(u, "input_tokens", None), "output": getattr(u, "output_tokens", None)} if u else None,
            metadata={"stop_reason": getattr(resp, "stop_reason", None)})


class _Acc:
    def __init__(self, obs):
        self.obs, self.parts, self.inp, self.out = obs, [], None, None
        self.tools: dict = {}

    def feed(self, ev: Any) -> None:
        self.obs.mark_first_token()
        t = getattr(ev, "type", "")
        if t == "message_start":
            self.inp = safe(lambda: ev.message.usage.input_tokens)
        elif t == "content_block_start" and safe(lambda: ev.content_block.type) == "tool_use":
            cb = ev.content_block
            self.tools[safe(lambda: ev.index, len(self.tools))] = {"id": getattr(cb, "id", None), "name": getattr(cb, "name", ""), "json": ""}
        elif t == "content_block_delta":
            txt = safe(lambda: ev.delta.text)
            if txt:
                self.parts.append(txt)
            pj = safe(lambda: ev.delta.partial_json)
            if pj and safe(lambda: ev.index) in self.tools:
                self.tools[ev.index]["json"] += pj
        elif t == "message_delta":
            self.out = safe(lambda: ev.usage.output_tokens)

    def done(self, error=None):
        calls = [{"id": c["id"], "name": c["name"], "arguments": parse_args(c["json"])} for _, c in sorted(self.tools.items())]
        self.obs.end(output=with_tool_calls("".join(self.parts), calls), usage={"input": self.inp, "output": self.out}, error=error, metadata={"stream": True})


def _sync(orig):
    @functools.wraps(orig)
    def wrapper(self, *args, **kwargs):
        if not get_client().enabled:
            return orig(self, *args, **kwargs)
        obs = _start(kwargs)
        try:
            resp = orig(self, *args, **kwargs)
        except BaseException as e:
            obs.end(error=e)
            raise
        if kwargs.get("stream"):
            acc = _Acc(obs)

            def gen():
                try:
                    for ev in resp:
                        acc.feed(ev)
                        yield ev
                except BaseException as e:
                    acc.done(e)
                    raise
                acc.done()
            return gen()
        _end(obs, resp)
        return resp
    return wrapper


def _async(orig):
    @functools.wraps(orig)
    async def wrapper(self, *args, **kwargs):
        if not get_client().enabled:
            return await orig(self, *args, **kwargs)
        obs = _start(kwargs)
        try:
            resp = await orig(self, *args, **kwargs)
        except BaseException as e:
            obs.end(error=e)
            raise
        if kwargs.get("stream"):
            acc = _Acc(obs)

            async def gen():
                try:
                    async for ev in resp:
                        acc.feed(ev)
                        yield ev
                except BaseException as e:
                    acc.done(e)
                    raise
                acc.done()
            return gen()
        _end(obs, resp)
        return resp
    return wrapper


def instrument() -> bool:
    from anthropic.resources import messages as m

    mod = getattr(m, "messages", m)  # newer SDKs: anthropic.resources.messages.messages
    ok = wrap(mod.Messages, "create", _sync)
    wrap(mod.AsyncMessages, "create", _async)
    return ok


def uninstrument() -> None:
    unwrap_all("anthropic")
