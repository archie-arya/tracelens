"""OpenTelemetry (OTLP/HTTP, protobuf or JSON) -> TraceLens events.

Understands the OpenTelemetry GenAI semantic conventions (``gen_ai.*``, incl. legacy indexed prompt/completion
attributes and gen_ai.* events), OpenInference (``llm.*``, ``input.value``...) and Traceloop/OpenLLMetry attributes.
"""
from __future__ import annotations

import base64
import json
from typing import Any, Dict, List, Optional, Tuple

CONTENT_KEYS = ("gen_ai.prompt", "gen_ai.completion", "input.value", "output.value", "gen_ai.input.messages", "gen_ai.output.messages",
                "llm.input_messages", "llm.output_messages", "traceloop.entity.input", "traceloop.entity.output", "gen_ai.system_instructions")


def _b64_to_hex(v: Optional[str]) -> Optional[str]:
    if not v:
        return None
    try:
        return base64.b64decode(v).hex()
    except Exception:
        return v


def decode_request(body: bytes, content_type: str) -> List[Dict[str, Any]]:
    """Return resourceSpans as dicts (camelCase, hex ids)."""
    if "json" in content_type:
        data = json.loads(body or b"{}")
        return data.get("resourceSpans") or data.get("resource_spans") or []
    from google.protobuf.json_format import MessageToDict
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

    req = ExportTraceServiceRequest()
    req.ParseFromString(body)
    d = MessageToDict(req)
    rs = d.get("resourceSpans") or []
    for r in rs:
        for ss in r.get("scopeSpans") or []:
            for sp in ss.get("spans") or []:
                for k in ("traceId", "spanId", "parentSpanId"):
                    if sp.get(k):
                        sp[k] = _b64_to_hex(sp[k])
                for link in sp.get("links") or []:
                    for k in ("traceId", "spanId"):
                        if link.get(k):
                            link[k] = _b64_to_hex(link[k])
    return rs


def _val(v: Dict[str, Any]) -> Any:
    if not isinstance(v, dict):
        return v
    for k in ("stringValue", "string_value"):
        if k in v:
            return v[k]
    for k in ("intValue", "int_value"):
        if k in v:
            return int(v[k])
    for k in ("doubleValue", "double_value"):
        if k in v:
            return float(v[k])
    for k in ("boolValue", "bool_value"):
        if k in v:
            return bool(v[k])
    if "arrayValue" in v or "array_value" in v:
        arr = v.get("arrayValue") or v.get("array_value") or {}
        return [_val(x) for x in arr.get("values", [])]
    if "kvlistValue" in v or "kvlist_value" in v:
        kv = v.get("kvlistValue") or v.get("kvlist_value") or {}
        return {x["key"]: _val(x.get("value", {})) for x in kv.get("values", [])}
    if "bytesValue" in v:
        return v["bytesValue"]
    return None


def _attrs(lst: Optional[List[Dict[str, Any]]]) -> Dict[str, Any]:
    return {a["key"]: _val(a.get("value", {})) for a in (lst or []) if "key" in a}


def _maybe_json(v: Any) -> Any:
    if isinstance(v, str) and v[:1] in "[{":
        try:
            return json.loads(v)
        except Exception:
            return v
    return v


def _indexed(attrs: Dict[str, Any], prefix: str, fields: Tuple[str, ...] = ("role", "content")) -> List[Dict[str, Any]]:
    out: Dict[int, Dict[str, Any]] = {}
    for k, v in attrs.items():
        if not k.startswith(prefix + "."):
            continue
        rest = k[len(prefix) + 1:].split(".")
        if not rest[0].isdigit():
            continue
        idx = int(rest[0])
        field = rest[-1]
        if field in fields or field in ("role", "content", "finish_reason"):
            out.setdefault(idx, {})[field] = v
    return [out[i] for i in sorted(out)]


def _first(attrs: Dict[str, Any], *keys: str) -> Any:
    for k in keys:
        if attrs.get(k) is not None:
            return attrs[k]
    return None


def _span_type(attrs: Dict[str, Any], model: Any) -> str:
    oi = str(attrs.get("openinference.span.kind") or "").upper()
    if oi:
        return {"LLM": "generation", "RETRIEVER": "retrieval", "TOOL": "tool", "AGENT": "agent", "CHAIN": "chain", "EMBEDDING": "embedding",
                "GUARDRAIL": "guardrail"}.get(oi, "span")
    op = str(attrs.get("gen_ai.operation.name") or "").lower()
    if op in ("embeddings", "embedding"):
        return "embedding"
    if op in ("execute_tool",):
        return "tool"
    if op in ("invoke_agent", "create_agent"):
        return "agent"
    if op or model or attrs.get("llm.request.type"):
        return "generation"
    tl = str(attrs.get("traceloop.span.kind") or "").lower()
    return {"tool": "tool", "agent": "agent", "workflow": "chain", "task": "span"}.get(tl, "span")


def _events_messages(events: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Any]]:
    inputs, outputs = [], []
    for e in events or []:
        name = e.get("name", "")
        a = _attrs(e.get("attributes"))
        body = a.get("content") or a.get("gen_ai.event.content") or a.get("message")
        if name in ("gen_ai.system.message", "gen_ai.user.message", "gen_ai.assistant.message", "gen_ai.tool.message"):
            inputs.append({"role": name.split(".")[1], "content": _maybe_json(body)})
        elif name == "gen_ai.choice":
            outputs.append(_maybe_json(body) if body is not None else a)
        elif name == "gen_ai.client.inference.operation.details":
            if a.get("gen_ai.input.messages"):
                inputs.extend(_maybe_json(a["gen_ai.input.messages"]) or [])
            if a.get("gen_ai.output.messages"):
                outputs.extend(_maybe_json(a["gen_ai.output.messages"]) or [])
    return inputs, outputs


def to_events(resource_spans: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    for rs in resource_spans:
        res = _attrs((rs.get("resource") or {}).get("attributes"))
        service = res.get("service.name")
        for ss in rs.get("scopeSpans") or rs.get("scope_spans") or []:
            scope = (ss.get("scope") or {}).get("name")
            for sp in ss.get("spans") or []:
                a = _attrs(sp.get("attributes"))
                model = _first(a, "gen_ai.response.model", "gen_ai.request.model", "llm.model_name", "llm.request.model")
                typ = _span_type(a, model)
                ev_in, ev_out = _events_messages(sp.get("events") or [])
                inp = _maybe_json(_first(a, "gen_ai.input.messages", "input.value", "traceloop.entity.input", "gen_ai.prompt"))
                if inp is None:
                    inp = _indexed(a, "gen_ai.prompt") or _indexed(a, "llm.input_messages") or ev_in or None
                out = _maybe_json(_first(a, "gen_ai.output.messages", "output.value", "traceloop.entity.output", "gen_ai.completion"))
                if out is None:
                    msgs = _indexed(a, "gen_ai.completion") or _indexed(a, "llm.output_messages")
                    out = (msgs[0].get("content") if len(msgs) == 1 else msgs) if msgs else (ev_out[0] if len(ev_out) == 1 else ev_out or None)
                pt = _first(a, "gen_ai.usage.input_tokens", "gen_ai.usage.prompt_tokens", "llm.token_count.prompt", "llm.usage.prompt_tokens")
                ct = _first(a, "gen_ai.usage.output_tokens", "gen_ai.usage.completion_tokens", "llm.token_count.completion", "llm.usage.completion_tokens")
                tt = _first(a, "llm.token_count.total", "llm.usage.total_tokens", "gen_ai.usage.total_tokens")
                params = {k.split(".")[-1]: v for k, v in a.items() if k.startswith("gen_ai.request.") and k not in ("gen_ai.request.model",)}
                status = sp.get("status") or {}
                code = str(status.get("code", ""))
                is_err = code in ("2", "STATUS_CODE_ERROR", "ERROR")
                md = {k: v for k, v in a.items() if not any(k.startswith(p) for p in CONTENT_KEYS) and not k.startswith("gen_ai.usage")
                      and not k.startswith("llm.token_count")}
                md.update({"otel.scope": scope, "service.name": service, "otel.kind": sp.get("kind")})
                provider = _first(a, "gen_ai.system", "gen_ai.provider.name", "llm.provider", "llm.system")
                if provider:
                    md["provider"] = provider
                trace_id, span_id, parent = sp.get("traceId"), sp.get("spanId"), sp.get("parentSpanId") or None
                events.append({"type": "observation", "body": {
                    "id": span_id, "trace_id": trace_id, "parent_id": parent, "type": typ, "name": sp.get("name"), "input": inp, "output": out,
                    "model": model, "model_parameters": params or None, "prompt_tokens": pt, "completion_tokens": ct, "total_tokens": tt,
                    "level": "ERROR" if is_err else "DEFAULT", "status_message": status.get("message"), "metadata": {k: v for k, v in md.items() if v is not None},
                    "start_time": sp.get("startTimeUnixNano"), "end_time": sp.get("endTimeUnixNano"),
                }})
                user = _first(a, "user.id", "enduser.id", "tracelens.user_id", "langfuse.user.id")
                session = _first(a, "session.id", "gen_ai.conversation.id", "tracelens.session_id", "langfuse.session.id")
                tags = _first(a, "tracelens.tags", "tags", "langfuse.trace.tags")
                tbody: Dict[str, Any] = {"id": trace_id, "sdk": f"otel:{scope or 'unknown'}"}
                if user: tbody["user_id"] = user
                if session: tbody["session_id"] = session
                if tags: tbody["tags"] = tags if isinstance(tags, list) else [t.strip() for t in str(tags).split(",")]
                if not parent:
                    tbody.update(name=sp.get("name"), start_time=sp.get("startTimeUnixNano"), end_time=sp.get("endTimeUnixNano"),
                                 environment=res.get("deployment.environment.name") or res.get("deployment.environment"), release=res.get("service.version"),
                                 metadata={"service.name": service} if service else {})
                if len(tbody) > 2 or not parent:
                    events.append({"type": "trace", "body": tbody})
    return events
