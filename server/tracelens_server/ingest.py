"""Normalize ingestion events (TraceLens SDK, Langfuse-style and OTLP-converted) and persist them."""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

from . import pricing
from .storage import Storage
from .util import new_id, parse_time, to_float, to_int

TRACE_TYPES = {"trace", "trace-create", "trace-upsert"}
OBS_TYPES = {"observation", "observation-create", "observation-upsert", "observation-update", "span", "span-create", "span-update",
             "generation", "generation-create", "generation-update", "event", "event-create"}
SCORE_TYPES = {"score", "score-create"}
VALID_OBS = {"span", "generation", "event", "tool", "retrieval", "embedding", "agent", "chain", "guardrail"}


def _md(v: Any) -> Dict[str, Any]:
    if v is None:
        return {}
    return v if isinstance(v, dict) else {"value": v}


def norm_trace(b: Dict[str, Any]) -> Dict[str, Any]:
    tags = b.get("tags") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    return {
        "id": str(b.get("id") or new_id()), "name": b.get("name"), "user_id": _s(b.get("user_id", b.get("userId"))),
        "session_id": _s(b.get("session_id", b.get("sessionId"))), "tags": [str(t) for t in tags][:50], "metadata": _md(b.get("metadata")),
        "input": b.get("input"), "output": b.get("output"), "release": b.get("release"), "environment": b.get("environment"),
        "start_time": parse_time(b.get("start_time", b.get("timestamp", b.get("startTime")))), "end_time": parse_time(b.get("end_time", b.get("endTime"))),
        "sdk": b.get("sdk"),
    }


def _s(v: Any) -> Any:
    return None if v is None else str(v)


def norm_observation(b: Dict[str, Any], event_type: str = "observation") -> Dict[str, Any]:
    typ = str(b.get("type") or event_type.split("-")[0]).lower()
    if typ == "observation":
        typ = "span"
    if typ not in VALID_OBS:
        typ = "span"
    usage = b.get("usage") or b.get("usage_details") or {}
    pt = to_int(b.get("prompt_tokens", usage.get("input", usage.get("prompt_tokens", usage.get("input_tokens", usage.get("promptTokens"))))))
    ct = to_int(b.get("completion_tokens", usage.get("output", usage.get("completion_tokens", usage.get("output_tokens", usage.get("completionTokens"))))))
    tt = to_int(b.get("total_tokens", usage.get("total", usage.get("total_tokens", usage.get("totalTokens")))))
    if tt is None and (pt is not None or ct is not None):
        tt = (pt or 0) + (ct or 0)
    model = b.get("model")
    cost = to_float(b.get("cost", usage.get("total_cost", b.get("calculated_total_cost"))))
    if cost is None and model and (pt is not None or ct is not None):
        cost = pricing.calculate(model, pt, ct)
    level = str(b.get("level") or ("ERROR" if str(b.get("status", "")).lower() == "error" else "DEFAULT")).upper()
    return {
        "id": str(b.get("id") or new_id()), "trace_id": str(b.get("trace_id") or b.get("traceId") or ""),
        "parent_id": _s(b.get("parent_id", b.get("parent_observation_id", b.get("parentObservationId")))), "type": typ, "name": b.get("name"),
        "input": b.get("input"), "output": b.get("output"), "metadata": _md(b.get("metadata")), "model": model,
        "model_parameters": b.get("model_parameters", b.get("modelParameters")), "prompt_tokens": pt, "completion_tokens": ct, "total_tokens": tt,
        "cost": cost, "level": level if level in ("DEBUG", "DEFAULT", "WARNING", "ERROR") else "DEFAULT",
        "status_message": b.get("status_message", b.get("statusMessage")),
        "start_time": parse_time(b.get("start_time", b.get("startTime", b.get("timestamp")))), "end_time": parse_time(b.get("end_time", b.get("endTime"))),
        "completion_start_time": parse_time(b.get("completion_start_time", b.get("completionStartTime"))),
    }


def norm_score(b: Dict[str, Any]) -> Dict[str, Any]:
    v = b.get("value")
    sv = b.get("string_value")
    if isinstance(v, bool):
        v = 1.0 if v else 0.0
    elif isinstance(v, str):
        try:
            v = float(v)
        except ValueError:
            sv, v = v, None
    return {"id": b.get("id") or new_id("sc_"), "trace_id": str(b.get("trace_id") or b.get("traceId") or ""),
            "observation_id": b.get("observation_id", b.get("observationId")), "name": str(b.get("name") or "score"),
            "value": to_float(v), "string_value": sv, "comment": b.get("comment"), "source": b.get("source") or "api",
            "metadata": _md(b.get("metadata")), "created_at": parse_time(b.get("timestamp"))}


def ingest_batch(storage: Storage, project_id: str, batch: List[Dict[str, Any]]) -> Tuple[Dict[str, Any], List[str]]:
    """Persist a batch. Returns (result summary, ids of traces that were touched)."""
    traces, obs, scores, errors = [], [], [], []
    for i, ev in enumerate(batch):
        try:
            et = str(ev.get("type", "")).lower()
            body = ev.get("body") if isinstance(ev.get("body"), dict) else None
            if body is None:
                raise ValueError("missing body")
            if et in TRACE_TYPES:
                traces.append(norm_trace(body))
            elif et in OBS_TYPES:
                o = norm_observation(body, et)
                if not o["trace_id"]:
                    raise ValueError("observation without trace_id")
                obs.append(o)
            elif et in SCORE_TYPES:
                s = norm_score(body)
                if not s["trace_id"]:
                    raise ValueError("score without trace_id")
                scores.append(s)
            else:
                raise ValueError(f"unknown event type {et!r}")
        except Exception as e:
            errors.append({"index": i, "error": str(e)})
    storage.upsert_traces(project_id, traces)
    storage.upsert_observations(project_id, obs)
    touched = list({t["id"] for t in traces} | {o["trace_id"] for o in obs})
    storage.refresh_trace_aggregates(project_id, touched)
    storage.insert_scores(project_id, scores)
    completed = [t["id"] for t in traces if t.get("end_time")] + [o["trace_id"] for o in obs if not o.get("parent_id") and o.get("end_time")]
    return {"accepted": len(traces) + len(obs) + len(scores), "traces": len(traces), "observations": len(obs), "scores": len(scores), "errors": errors}, list(set(completed))
