"""LangChain / LangGraph callback handler.

    from tracelens.integrations.langchain import TraceLensCallbackHandler
    chain.invoke(inputs, config={"callbacks": [TraceLensCallbackHandler()]})
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from uuid import UUID

from ..client import Observation, Trace, _current_obs, _current_trace, get_client
from ._wrap import safe

try:
    from langchain_core.callbacks import BaseCallbackHandler
except ImportError:  # pragma: no cover
    try:
        from langchain.callbacks.base import BaseCallbackHandler  # type: ignore
    except ImportError:
        BaseCallbackHandler = object  # type: ignore


class TraceLensCallbackHandler(BaseCallbackHandler):  # type: ignore[misc]
    raise_error = False
    run_inline = True

    def __init__(self, trace_name: Optional[str] = None, user_id: Optional[str] = None, session_id: Optional[str] = None,
                 tags: Optional[List[str]] = None, metadata: Optional[Dict[str, Any]] = None):
        super().__init__()
        self.trace_name, self.user_id, self.session_id = trace_name, user_id, session_id
        self.tags, self.metadata = tags, metadata
        self._obs: Dict[UUID, Observation] = {}
        self._root_traces: Dict[UUID, Trace] = {}

    # ------------------------------------------------------------ helpers
    def _start(self, run_id: UUID, parent_run_id: Optional[UUID], name: str, type: str, **kw: Any) -> None:
        client = get_client()
        parent = self._obs.get(parent_run_id) if parent_run_id else None
        if parent is not None:
            trace = parent.trace
        else:
            trace = _current_trace.get()
            parent = _current_obs.get() if trace is not None else None
            if trace is None:
                trace = client.start_trace(self.trace_name or name, user_id=self.user_id, session_id=self.session_id,
                                           tags=self.tags, metadata=self.metadata, input=kw.get("input"))
                self._root_traces[run_id] = trace
        self._obs[run_id] = client.start_observation(name, type=type, trace=trace, parent=parent, **kw)

    def _end(self, run_id: UUID, output: Any = None, error: Optional[BaseException] = None, **kw: Any) -> None:
        obs = self._obs.pop(run_id, None)
        if obs is None:
            return
        obs.end(output=output, error=error, **kw)
        trace = self._root_traces.pop(run_id, None)
        if trace is not None:
            trace.update(output=output)
            trace.end()

    @staticmethod
    def _name(serialized: Optional[Dict[str, Any]], kwargs: Dict[str, Any], default: str) -> str:
        if kwargs.get("name"):
            return kwargs["name"]
        if serialized:
            return serialized.get("name") or (serialized.get("id") or [default])[-1]
        return default

    # ------------------------------------------------------------ chains / agents
    def on_chain_start(self, serialized, inputs, *, run_id, parent_run_id=None, tags=None, metadata=None, **kwargs):
        name = self._name(serialized, kwargs, "chain")
        self._start(run_id, parent_run_id, name, "agent" if "agent" in name.lower() else "chain", input=inputs,
                    metadata={"tags": tags, **(metadata or {})} if (tags or metadata) else None)

    def on_chain_end(self, outputs, *, run_id, **kwargs):
        self._end(run_id, outputs)

    def on_chain_error(self, error, *, run_id, **kwargs):
        self._end(run_id, error=error)

    # ------------------------------------------------------------ LLMs
    def _llm_start(self, serialized, payload, run_id, parent_run_id, kwargs):
        inv = kwargs.get("invocation_params") or {}
        model = inv.get("model") or inv.get("model_name") or (kwargs.get("metadata") or {}).get("ls_model_name")
        params = {k: inv[k] for k in ("temperature", "max_tokens", "top_p") if k in inv}
        self._start(run_id, parent_run_id, self._name(serialized, kwargs, "llm"), "generation", input=payload, model=model,
                    model_parameters=params, metadata={"provider": (kwargs.get("metadata") or {}).get("ls_provider")})

    def on_llm_start(self, serialized, prompts, *, run_id, parent_run_id=None, **kwargs):
        self._llm_start(serialized, prompts, run_id, parent_run_id, kwargs)

    def on_chat_model_start(self, serialized, messages, *, run_id, parent_run_id=None, **kwargs):
        msgs = [[{"role": getattr(m, "type", "user"), "content": getattr(m, "content", str(m))} for m in batch] for batch in messages]
        self._llm_start(serialized, msgs[0] if len(msgs) == 1 else msgs, run_id, parent_run_id, kwargs)

    def on_llm_new_token(self, token, *, run_id, **kwargs):
        obs = self._obs.get(run_id)
        if obs is not None:
            obs.mark_first_token()

    def on_llm_end(self, response, *, run_id, **kwargs):
        gens = safe(lambda: response.generations[0], []) or []
        text = safe(lambda: gens[0].text) if gens else None
        msg = safe(lambda: gens[0].message)
        out_usage = safe(lambda: msg.usage_metadata) or {}
        llm_out = getattr(response, "llm_output", None) or {}
        tu = llm_out.get("token_usage") or llm_out.get("usage") or {}
        usage = {"input": out_usage.get("input_tokens") or tu.get("prompt_tokens") or tu.get("input_tokens"),
                 "output": out_usage.get("output_tokens") or tu.get("completion_tokens") or tu.get("output_tokens")}
        model = llm_out.get("model_name") or safe(lambda: msg.response_metadata.get("model_name"))
        obs = self._obs.get(run_id)
        if obs is not None and model and not obs.model:
            obs.model = model
        self._end(run_id, text if text else safe(lambda: msg.content), usage=usage if any(usage.values()) else None)

    def on_llm_error(self, error, *, run_id, **kwargs):
        self._end(run_id, error=error)

    # ------------------------------------------------------------ tools / retrievers
    def on_tool_start(self, serialized, input_str, *, run_id, parent_run_id=None, inputs=None, **kwargs):
        self._start(run_id, parent_run_id, self._name(serialized, kwargs, "tool"), "tool", input=inputs or input_str)

    def on_tool_end(self, output, *, run_id, **kwargs):
        self._end(run_id, safe(lambda: output.content, output) if not isinstance(output, str) else output)

    def on_tool_error(self, error, *, run_id, **kwargs):
        self._end(run_id, error=error)

    def on_retriever_start(self, serialized, query, *, run_id, parent_run_id=None, **kwargs):
        self._start(run_id, parent_run_id, self._name(serialized, kwargs, "retriever"), "retrieval", input=query)

    def on_retriever_end(self, documents, *, run_id, **kwargs):
        self._end(run_id, [{"content": getattr(d, "page_content", str(d)), "metadata": getattr(d, "metadata", {})} for d in documents])

    def on_retriever_error(self, error, *, run_id, **kwargs):
        self._end(run_id, error=error)


CallbackHandler = TraceLensCallbackHandler
