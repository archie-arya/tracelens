"""Normalize tool (function) definitions and tool calls across providers, so the server can replay and score
tool-calling steps. Everything here is best-effort and never raises: tracing must not break the app.

Recorded on each LLM span:

* ``metadata.tools``             - names of the tools offered (kept for backwards compatibility)
* ``metadata.tool_definitions``  - ``[{"name", "description", "parameters"}]`` with the JSON schema of each tool
* ``output.tool_calls``          - ``[{"id", "name", "arguments"}]`` for the calls the model returned
* ``input``                      - the full message list, including earlier assistant tool calls and tool results
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from ._wrap import safe


def _get(o: Any, key: str, default: Any = None) -> Any:
    if isinstance(o, dict):
        return o.get(key, default)
    return getattr(o, key, default)


def _dump(o: Any) -> Any:
    """pydantic objects -> plain dicts (without None fields); dicts/lists recurse."""
    if o is None or isinstance(o, (str, int, float, bool)):
        return o
    if isinstance(o, dict):
        return {k: _dump(v) for k, v in o.items() if v is not None}
    if isinstance(o, (list, tuple)):
        return [_dump(v) for v in o]
    for attr in ("model_dump", "to_dict", "dict"):
        fn = getattr(o, attr, None)
        if callable(fn):
            try:
                d = fn(exclude_none=True) if attr == "model_dump" else fn()
                return _dump(d)
            except TypeError:
                try:
                    return _dump(fn())
                except Exception:
                    pass
            except Exception:
                pass
    return o


def parse_args(a: Any) -> Any:
    """Tool arguments arrive as a JSON string (OpenAI) or a dict (Anthropic, Gemini). Return a dict when possible."""
    if isinstance(a, str):
        try:
            return json.loads(a) if a.strip() else {}
        except Exception:
            return a
    return _dump(a) if a is not None else {}


def _defn(name: Any, description: Any, parameters: Any) -> Optional[Dict[str, Any]]:
    if not name:
        return None
    d: Dict[str, Any] = {"name": str(name)}
    if description:
        d["description"] = str(description)
    d["parameters"] = _dump(parameters) if parameters is not None else {"type": "object", "properties": {}}
    return d


def openai_tool_definitions(kwargs: Dict[str, Any]) -> List[Dict[str, Any]]:
    """From OpenAI ``tools=[{"type": "function", "function": {...}}]`` or legacy ``functions=[...]``."""
    out: List[Dict[str, Any]] = []
    for t in kwargs.get("tools") or []:
        t = _dump(t)
        if not isinstance(t, dict):
            continue
        f = t.get("function") if isinstance(t.get("function"), dict) else (t if "name" in t else None)
        if f is None:
            out.append({"name": str(t.get("type") or "tool"), "builtin": True})  # e.g. web_search: no schema to replay
            continue
        d = _defn(f.get("name"), f.get("description"), f.get("parameters"))
        if d:
            out.append(d)
    for f in kwargs.get("functions") or []:
        f = _dump(f)
        d = _defn(_get(f, "name"), _get(f, "description"), _get(f, "parameters")) if isinstance(f, dict) else None
        if d:
            out.append(d)
    return out


def anthropic_tool_definitions(tools: Any) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for t in tools or []:
        t = _dump(t)
        if not isinstance(t, dict):
            continue
        if "input_schema" not in t and t.get("type") not in (None, "custom"):
            out.append({"name": str(t.get("name") or t.get("type")), "builtin": True})  # server tools (web_search, ...)
            continue
        d = _defn(t.get("name"), t.get("description"), t.get("input_schema"))
        if d:
            out.append(d)
    return out


def gemini_tool_definitions(cfg: Any) -> List[Dict[str, Any]]:
    """From ``GenerateContentConfig(tools=[Tool(function_declarations=[...]) | python callable])``."""
    tools = _get(cfg, "tools") if cfg is not None else None
    out: List[Dict[str, Any]] = []
    for t in tools or []:
        if callable(t) and not hasattr(t, "model_dump"):  # automatic function calling with a Python function
            out.append(_defn(getattr(t, "__name__", "tool"), (getattr(t, "__doc__", "") or "").strip(), None) or {})
            continue
        t = _dump(t)
        if not isinstance(t, dict):
            continue
        decls = t.get("function_declarations") or t.get("functionDeclarations") or []
        for f in decls:
            params = f.get("parameters_json_schema") or f.get("parametersJsonSchema") or f.get("parameters")
            d = _defn(f.get("name"), f.get("description"), params)
            if d:
                out.append(d)
        for k in t:
            if k not in ("function_declarations", "functionDeclarations"):
                out.append({"name": k, "builtin": True})  # google_search, code_execution, ...
    return [d for d in out if d]


def tool_metadata(defs: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not defs:
        return {}
    return {"tools": [d["name"] for d in defs], "tool_definitions": defs}


def openai_tool_calls(tcs: Any) -> List[Dict[str, Any]]:
    out = []
    for t in tcs or []:
        fn = _get(t, "function")
        out.append({"id": _get(t, "id"), "name": _get(fn, "name"), "arguments": parse_args(_get(fn, "arguments"))})
    return out


def gemini_tool_calls(resp: Any) -> List[Dict[str, Any]]:
    parts = safe(lambda: resp.candidates[0].content.parts) or []
    out = []
    for p in parts:
        fc = getattr(p, "function_call", None)
        if fc is not None and getattr(fc, "name", None):
            out.append({"id": getattr(fc, "id", None), "name": fc.name, "arguments": parse_args(getattr(fc, "args", None))})
    return out


class StreamToolCalls:
    """Accumulates OpenAI-style streamed ``delta.tool_calls`` fragments by index."""

    def __init__(self) -> None:
        self.calls: Dict[int, Dict[str, Any]] = {}

    def feed(self, deltas: Any) -> None:
        for d in deltas or []:
            i = _get(d, "index", len(self.calls)) or 0
            c = self.calls.setdefault(i, {"id": None, "name": "", "arguments": ""})
            if _get(d, "id"):
                c["id"] = _get(d, "id")
            fn = _get(d, "function")
            if fn is not None:
                if _get(fn, "name"):
                    c["name"] += _get(fn, "name")
                if _get(fn, "arguments"):
                    c["arguments"] += _get(fn, "arguments")

    def result(self) -> List[Dict[str, Any]]:
        return [{"id": c["id"], "name": c["name"], "arguments": parse_args(c["arguments"])} for _, c in sorted(self.calls.items())]


def with_tool_calls(text: Any, calls: List[Dict[str, Any]]) -> Any:
    """Output shape: plain text when there are no tool calls, else ``{"role", "content", "tool_calls"}``."""
    if not calls:
        return text
    return {"role": "assistant", "content": text or None, "tool_calls": calls}


def messages_jsonable(msgs: Any) -> Any:
    """Turn SDK message objects (e.g. a ``ChatCompletionMessage`` appended back into ``messages``, or Gemini ``Content``)
    into plain dicts without None fields, so assistant tool calls and tool results are kept in the recorded input."""
    if isinstance(msgs, (list, tuple)):
        return [m if isinstance(m, (str, dict)) and not _has_objects(m) else _dump(m) for m in msgs]
    return msgs


def _has_objects(m: Any) -> bool:
    if isinstance(m, dict):
        return any(_has_objects(v) for v in m.values())
    if isinstance(m, (list, tuple)):
        return any(_has_objects(v) for v in m)
    return not (m is None or isinstance(m, (str, int, float, bool)))
