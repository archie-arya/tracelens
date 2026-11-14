"""Turn stored observations into replayable samples, with the safety rules applied:

* only ``generation`` observations are ever replayed (spans, tools, retrievals and events never are);
* generations whose original output is a tool/function call are skipped: replaying them could not reproduce the
  tool definitions faithfully, and tools are never executed by the optimizer;
* inputs are redacted before leaving the server when redaction is configured (``redact`` option or
  ``TRACELENS_OPTIMIZER_REDACT=true``);
* errored generations and generations without input/output are skipped.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from .base import Sample

_PATTERNS: Tuple[Tuple[str, "re.Pattern[str]"], ...] = (
    ("EMAIL", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("API_KEY", re.compile(r"\b(?:sk|pk|rk|gsk|tl-sk|AIza)[-_A-Za-z0-9]{16,}\b")),
    ("CARD", re.compile(r"\b(?:\d[ -]?){13,19}\b")),
    ("SSN", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("PHONE", re.compile(r"(?<!\w)(?:\+?\d{1,3}[\s.-]?)?(?:\(?\d{2,4}\)?[\s.-]?)\d{3,4}[\s.-]?\d{3,4}(?!\w)")),
    ("IP", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")),
)


def redact(value: Any) -> Any:
    if isinstance(value, str):
        for label, pat in _PATTERNS:
            value = pat.sub(f"[REDACTED_{label}]", value)
        return value
    if isinstance(value, dict):
        return {k: redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


def redaction_enabled(config: Dict[str, Any]) -> bool:
    if config.get("redact") is not None:
        return bool(config["redact"])
    return os.environ.get("TRACELENS_OPTIMIZER_REDACT", "").lower() in ("1", "true", "yes")


def _parts_text(parts: Any) -> str:
    if isinstance(parts, str):
        return parts
    if isinstance(parts, list):
        return "\n".join(_parts_text(p) for p in parts)
    if isinstance(parts, dict):
        if "text" in parts:
            return str(parts["text"])
        if "parts" in parts:
            return _parts_text(parts["parts"])
        if "content" in parts:
            return _parts_text(parts["content"])
    return "" if parts is None else json.dumps(parts, ensure_ascii=False)


def normalize_messages(inp: Any) -> Optional[List[Dict[str, Any]]]:
    """Accept OpenAI messages, Anthropic {system, messages}, Gemini contents, LangChain-ish dicts or a plain prompt."""
    if inp is None:
        return None
    if isinstance(inp, str):
        return [{"role": "user", "content": inp}]
    if isinstance(inp, dict):
        msgs: List[Dict[str, Any]] = []
        if inp.get("system"):
            msgs.append({"role": "system", "content": _parts_text(inp["system"])})
        for key in ("messages", "contents", "input", "prompt"):
            if key in inp:
                rest = normalize_messages(inp[key])
                return msgs + (rest or [])
        return msgs + [{"role": "user", "content": json.dumps(inp, ensure_ascii=False)}]
    if isinstance(inp, list):
        out = []
        for m in inp:
            if isinstance(m, str):
                out.append({"role": "user", "content": m})
            elif isinstance(m, dict):
                role = m.get("role") or m.get("type") or "user"
                role = {"model": "assistant", "ai": "assistant", "human": "user"}.get(role, role)
                content = m.get("content") if "content" in m else m.get("parts", m.get("text"))
                if role == "tool" or m.get("tool_calls"):
                    return None  # multi-turn tool conversations are not replayable without the tool definitions
                out.append({"role": role, "content": _parts_text(content)})
        return out or None
    return [{"role": "user", "content": str(inp)}]


def is_tool_call(output: Any) -> bool:
    if isinstance(output, dict):
        if output.get("tool_calls") or output.get("function_call"):
            return True
        if output.get("type") in ("tool_use", "function_call"):
            return True
        return any(is_tool_call(v) for v in output.values() if isinstance(v, (dict, list)))
    if isinstance(output, list):
        return any(is_tool_call(v) for v in output)
    return False


def output_text(output: Any) -> str:
    if output is None:
        return ""
    if isinstance(output, str):
        return output
    if isinstance(output, dict):
        if isinstance(output.get("parts"), list):  # Gemini content object: visible text only, never thoughts
            return "".join(str(p.get("text", "")) for p in output["parts"] if isinstance(p, dict) and not p.get("thought"))
        if "content" in output and isinstance(output["content"], (str, list)):
            return _parts_text(output["content"])
        if "text" in output:
            return str(output["text"])
        if "choices" in output:
            try:
                return output["choices"][0]["message"]["content"] or ""
            except Exception:
                pass
    return json.dumps(output, ensure_ascii=False)


def normalize_params(p: Optional[Dict[str, Any]], config: Dict[str, Any]) -> Dict[str, Any]:
    p = p or {}
    out: Dict[str, Any] = {}
    if p.get("temperature") is not None:
        out["temperature"] = p["temperature"]
    if p.get("top_p") is not None:
        out["top_p"] = p["top_p"]
    mt = p.get("max_tokens") or p.get("max_output_tokens") or p.get("maxOutputTokens") or p.get("max_completion_tokens")
    floor = int(config.get("min_max_tokens", 1024))  # thinking models spend tokens before answering; avoid truncation
    if mt:
        out["max_tokens"] = max(int(mt), floor)
    if p.get("stop"):
        out["stop"] = p["stop"]
    rf = p.get("response_format") or p.get("response_mime_type")
    if (isinstance(rf, dict) and rf.get("type") in ("json_object", "json_schema")) or rf == "application/json":
        out["json"] = True
    return out


def build_sample(obs: Dict[str, Any], step_label: str, config: Dict[str, Any]) -> Tuple[Optional[Sample], Optional[str]]:
    """Return (sample, None) or (None, skip_reason)."""
    if obs.get("type") != "generation":
        return None, "not an LLM generation"
    if obs.get("level") == "ERROR":
        return None, "original call errored"
    if obs.get("output") in (None, "", []):
        return None, "no stored output"
    if is_tool_call(obs.get("output")):
        return None, "output is a tool call (tools are never replayed)"
    msgs = normalize_messages(obs.get("input"))
    if not msgs:
        return None, "input is not a replayable chat prompt"
    red = redaction_enabled(config)
    if red:
        msgs = redact(msgs)
    text = output_text(obs.get("output"))
    if not text.strip():
        return None, "no visible output text"
    return Sample(observation_id=obs["id"], trace_id=obs["trace_id"], step=step_label, model=obs.get("model"), messages=msgs,
                  params=normalize_params(obs.get("model_parameters"), config), output=obs.get("output"), output_text=text,
                  input_tokens=obs.get("prompt_tokens"), output_tokens=obs.get("completion_tokens"), cost=obs.get("cost"),
                  latency_ms=obs.get("latency_ms"), start_time=obs.get("start_time"), redacted=red), None
