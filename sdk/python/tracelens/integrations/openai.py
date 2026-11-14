"""OpenAI SDK (>=1.0) instrumentation. Also covers every OpenAI-compatible API used through the
``openai`` client: Groq, Together, OpenRouter, Fireworks, DeepSeek, Ollama (/v1), vLLM, LM Studio,
Gemini's OpenAI endpoint, Azure OpenAI..."""
from __future__ import annotations

import functools
from typing import Any, Dict, Optional

from ..client import get_client
from ._wrap import safe, unwrap_all, wrap

PARAMS = ("temperature", "top_p", "max_tokens", "max_completion_tokens", "n", "stop", "presence_penalty",
          "frequency_penalty", "seed", "response_format", "tool_choice", "reasoning_effort")

_PROVIDERS = (("groq", "groq"), ("together", "together"), ("openrouter", "openrouter"), ("fireworks", "fireworks"),
              ("deepseek", "deepseek"), ("mistral", "mistral"), ("generativelanguage.googleapis", "gemini"),
              ("azure", "azure-openai"), ("localhost:11434", "ollama"), ("11434", "ollama"), ("perplexity", "perplexity"),
              ("x.ai", "xai"), ("cerebras", "cerebras"), ("api.openai.com", "openai"))


def provider_from(resource: Any) -> str:
    url = str(safe(lambda: resource._client.base_url, "") or "")
    for needle, name in _PROVIDERS:
        if needle in url:
            return name
    return "openai-compatible" if url else "openai"


def _params(kwargs: Dict[str, Any]) -> Dict[str, Any]:
    return {k: kwargs[k] for k in PARAMS if k in kwargs and kwargs[k] is not None}


def _usage(u: Any) -> Optional[Dict[str, int]]:
    if u is None:
        return None
    return {"input": getattr(u, "prompt_tokens", None), "output": getattr(u, "completion_tokens", None), "total": getattr(u, "total_tokens", None)}


def _message_out(resp: Any) -> Any:
    choices = getattr(resp, "choices", None) or []
    if not choices:
        return None
    msg = choices[0].message
    out: Dict[str, Any] = {"role": getattr(msg, "role", "assistant"), "content": getattr(msg, "content", None)}
    tcs = getattr(msg, "tool_calls", None)
    if tcs:
        out["tool_calls"] = [{"id": t.id, "name": t.function.name, "arguments": t.function.arguments} for t in tcs]
    if len(choices) == 1 and not tcs:
        return out["content"]
    return out


def _start(resource: Any, kwargs: Dict[str, Any], kind: str):
    provider = provider_from(resource)
    return get_client().start_observation(
        f"{provider}.{kind}", type="embedding" if kind == "embeddings" else "generation",
        model=kwargs.get("model"), input=kwargs.get("messages", kwargs.get("input", kwargs.get("prompt"))),
        model_parameters=_params(kwargs), metadata={"provider": provider, **({"tools": [t.get("function", {}).get("name") for t in kwargs["tools"] if isinstance(t, dict)]} if kwargs.get("tools") else {})},
    )


class _StreamProxy:
    """Wraps a (sync) OpenAI stream to accumulate text/usage, ending the generation when exhausted."""

    def __init__(self, stream: Any, obs: Any):
        self._stream, self._obs, self._parts, self._usage, self._model = stream, obs, [], None, None

    def __iter__(self):
        try:
            for chunk in self._stream:
                self._consume(chunk)
                yield chunk
        except BaseException as e:
            self._finish(e)
            raise
        self._finish()

    def __next__(self):  # pragma: no cover - rarely used directly
        try:
            chunk = next(self._stream)
        except StopIteration:
            self._finish()
            raise
        self._consume(chunk)
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._finish()
        return safe(lambda: self._stream.__exit__(*exc), False)

    def __getattr__(self, item):
        return getattr(self._stream, item)

    def _consume(self, chunk: Any) -> None:
        self._obs.mark_first_token()
        self._model = getattr(chunk, "model", None) or self._model
        for c in getattr(chunk, "choices", None) or []:
            d = getattr(c, "delta", None)
            if d is not None and getattr(d, "content", None):
                self._parts.append(d.content)
        if getattr(chunk, "usage", None):
            self._usage = _usage(chunk.usage)

    def _finish(self, error: Optional[BaseException] = None) -> None:
        if self._model and not self._obs.model:
            self._obs.model = self._model
        self._obs.end(output="".join(self._parts), usage=self._usage, error=error, metadata={"stream": True})


class _AsyncStreamProxy(_StreamProxy):
    def __aiter__(self):
        return self._agen()

    async def _agen(self):
        try:
            async for chunk in self._stream:
                self._consume(chunk)
                yield chunk
        except BaseException as e:
            self._finish(e)
            raise
        self._finish()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        self._finish()
        fn = getattr(self._stream, "__aexit__", None)
        return (await fn(*exc)) if fn else False


def _sync_factory(kind: str):
    def factory(orig):
        @functools.wraps(orig)
        def wrapper(self, *args, **kwargs):
            client = get_client()
            if not client.enabled:
                return orig(self, *args, **kwargs)
            obs = _start(self, kwargs, kind)
            try:
                resp = orig(self, *args, **kwargs)
            except BaseException as e:
                obs.end(error=e)
                raise
            if kwargs.get("stream"):
                return _StreamProxy(resp, obs)
            _finish(obs, resp, kind)
            return resp
        return wrapper
    return factory


def _async_factory(kind: str):
    def factory(orig):
        @functools.wraps(orig)
        async def wrapper(self, *args, **kwargs):
            client = get_client()
            if not client.enabled:
                return await orig(self, *args, **kwargs)
            obs = _start(self, kwargs, kind)
            try:
                resp = await orig(self, *args, **kwargs)
            except BaseException as e:
                obs.end(error=e)
                raise
            if kwargs.get("stream"):
                return _AsyncStreamProxy(resp, obs)
            _finish(obs, resp, kind)
            return resp
        return wrapper
    return factory


def _finish(obs: Any, resp: Any, kind: str) -> None:
    model = getattr(resp, "model", None)
    if model:
        obs.model = model
    if kind == "chat":
        out = safe(lambda: _message_out(resp))
        fr = safe(lambda: resp.choices[0].finish_reason)
        obs.end(output=out, usage=_usage(getattr(resp, "usage", None)), metadata={"finish_reason": fr} if fr else None)
    elif kind == "completions":
        obs.end(output=safe(lambda: resp.choices[0].text), usage=_usage(getattr(resp, "usage", None)))
    else:  # embeddings
        u = getattr(resp, "usage", None)
        obs.end(output={"embeddings": len(getattr(resp, "data", []) or []), "dims": safe(lambda: len(resp.data[0].embedding))},
                usage={"input": getattr(u, "prompt_tokens", None), "output": 0} if u else None)


def instrument() -> bool:
    import openai  # noqa: F401
    from openai.resources.chat import completions as cc

    ok = wrap(cc.Completions, "create", _sync_factory("chat"))
    wrap(cc.AsyncCompletions, "create", _async_factory("chat"))
    try:
        from openai.resources import completions as legacy, embeddings as emb
        wrap(legacy.Completions, "create", _sync_factory("completions"))
        wrap(legacy.AsyncCompletions, "create", _async_factory("completions"))
        wrap(emb.Embeddings, "create", _sync_factory("embeddings"))
        wrap(emb.AsyncEmbeddings, "create", _async_factory("embeddings"))
    except Exception:
        pass
    return ok


def uninstrument() -> None:
    unwrap_all("openai")
