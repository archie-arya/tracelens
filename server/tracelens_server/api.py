"""REST API (v1). All endpoints are project-scoped by API key; GETs fall back to the public read-only demo project."""
from __future__ import annotations

import asyncio
import json
import time
from collections import defaultdict
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from . import __version__, alerts as alerts_mod, otlp
from .auth import Ctx, display_key, generate_key, hash_key, llm_eval_limiter, read_ctx, signup_limiter, write_ctx
from .config import settings
from .evals import get_evaluator, list_evaluators
from .evals.base import EvalInput
from .evals.llm import get_judge
from .evals.runner import JOBS, apply_rules, evaluate_trace, new_job, run_job
from .ingest import ingest_batch
from .storage import ObservationFilter, ScoreFilter, TraceFilter
from .util import now, parse_time

router = APIRouter()


def _st(request: Request):
    return request.app.state.storage


# ------------------------------------------------------------------ meta
@router.get("/api/health")
def health(request: Request):
    return {"status": "ok", "version": __version__, "judge_available": get_judge().available()}


@router.get("/api/v1/project")
def current_project(ctx: Ctx = Depends(read_ctx)):
    p = ctx.project
    return {"id": p["id"], "name": p["name"], "slug": p["slug"], "read_only": ctx.read_only, "is_demo": p["slug"] == settings.demo_project,
            "allow_signup": settings.allow_signup, "judge_available": get_judge().available()}


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)


@router.post("/api/v1/projects")
def create_project(body: ProjectIn, request: Request):
    admin = settings.admin_token and request.headers.get("x-admin-token") == settings.admin_token
    if not (settings.allow_signup or admin):
        raise HTTPException(403, "project signup disabled on this server")
    if not admin:
        signup_limiter.check(request.client.host if request.client else "anon")
    st = _st(request)
    proj = st.create_project(body.name)
    key = generate_key()
    st.create_api_key(proj["id"], "default", hash_key(key), display_key(key))
    return {"project": proj, "api_key": key, "note": "Store this key now - it is shown only once."}


@router.get("/api/v1/projects")
def list_projects(request: Request):
    if not settings.admin_token or request.headers.get("x-admin-token") != settings.admin_token:
        raise HTTPException(403, "admin token required")
    return {"data": _st(request).list_projects()}


# ------------------------------------------------------------------ API keys
class KeyIn(BaseModel):
    name: str = "key"


@router.get("/api/v1/keys")
def list_keys(request: Request, ctx: Ctx = Depends(write_ctx)):
    return {"data": _st(request).list_api_keys(ctx.project_id)}


@router.post("/api/v1/keys")
def create_key(body: KeyIn, request: Request, ctx: Ctx = Depends(write_ctx)):
    key = generate_key()
    rec = _st(request).create_api_key(ctx.project_id, body.name, hash_key(key), display_key(key))
    return {**rec, "api_key": key}


@router.delete("/api/v1/keys/{key_id}")
def revoke_key(key_id: str, request: Request, ctx: Ctx = Depends(write_ctx)):
    if not _st(request).revoke_api_key(ctx.project_id, key_id):
        raise HTTPException(404, "key not found")
    return {"revoked": key_id}


# ------------------------------------------------------------------ ingestion
class IngestIn(BaseModel):
    batch: List[Dict[str, Any]]
    metadata: Optional[Dict[str, Any]] = None


@router.post("/api/v1/ingest")
def ingest(body: IngestIn, request: Request, background: BackgroundTasks, ctx: Ctx = Depends(write_ctx)):
    if len(body.batch) > settings.max_batch:
        raise HTTPException(413, f"batch too large (max {settings.max_batch})")
    st = _st(request)
    result, completed = ingest_batch(st, ctx.project_id, body.batch)
    if completed:
        background.add_task(apply_rules, st, ctx.project_id, completed)
    return result


# Langfuse-compatible alias so existing Langfuse SDK users can point at TraceLens
@router.post("/api/public/ingestion")
def ingest_langfuse(body: IngestIn, request: Request, background: BackgroundTasks, ctx: Ctx = Depends(write_ctx)):
    return ingest(body, request, background, ctx)


@router.post("/v1/traces")
@router.post("/api/v1/otel/v1/traces")
async def otlp_traces(request: Request, background: BackgroundTasks, ctx: Ctx = Depends(write_ctx)):
    raw = await request.body()
    if request.headers.get("content-encoding") == "gzip":
        import gzip
        raw = gzip.decompress(raw)
    ctype = request.headers.get("content-type", "application/x-protobuf")
    try:
        events = otlp.to_events(otlp.decode_request(raw, ctype))
    except Exception as e:
        raise HTTPException(400, f"could not decode OTLP payload: {e}")
    st = _st(request)
    result, completed = await asyncio.to_thread(ingest_batch, st, ctx.project_id, events)
    if completed:
        background.add_task(apply_rules, st, ctx.project_id, completed)
    if "json" in ctype:
        return {"partialSuccess": {}} if not result["errors"] else {"partialSuccess": {"rejectedSpans": len(result["errors"])}}
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceResponse
    return Response(ExportTraceServiceResponse().SerializeToString(), media_type="application/x-protobuf")


# ------------------------------------------------------------------ traces & observations
def _trace_filter(q: Optional[str], name: Optional[str], user_id: Optional[str], session_id: Optional[str], tag: Optional[str],
                  status: Optional[str], model: Optional[str], release: Optional[str], environment: Optional[str], start: Optional[str],
                  end: Optional[str], min_latency_ms: Optional[float], min_cost: Optional[float], order_by: str, order: str) -> TraceFilter:
    return TraceFilter(q=q, name=name, user_id=user_id, session_id=session_id, tag=tag, status=status, model=model, release=release,
                       environment=environment, start=parse_time(start), end=parse_time(end), min_latency_ms=min_latency_ms,
                       min_cost=min_cost, order_by=order_by, desc=order != "asc")


@router.get("/api/v1/traces")
def list_traces(request: Request, ctx: Ctx = Depends(read_ctx), q: Optional[str] = None, name: Optional[str] = None, user_id: Optional[str] = None,
                session_id: Optional[str] = None, tag: Optional[str] = None, status: Optional[str] = None, model: Optional[str] = None,
                release: Optional[str] = None, environment: Optional[str] = None, start: Optional[str] = None, end: Optional[str] = None,
                min_latency_ms: Optional[float] = None, min_cost: Optional[float] = None, order_by: str = "start_time", order: str = "desc",
                limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0)):
    f = _trace_filter(q, name, user_id, session_id, tag, status, model, release, environment, start, end, min_latency_ms, min_cost, order_by, order)
    items, total = _st(request).list_traces(ctx.project_id, f, limit, offset)
    return {"data": items, "total": total, "limit": limit, "offset": offset}


@router.get("/api/v1/traces/export")
def export_traces(request: Request, ctx: Ctx = Depends(read_ctx), start: Optional[str] = None, end: Optional[str] = None, limit: int = Query(1000, le=10000)):
    st = _st(request)
    items, _ = st.list_traces(ctx.project_id, TraceFilter(start=parse_time(start), end=parse_time(end)), limit, 0)

    def gen():
        for it in items:
            full = st.get_trace(ctx.project_id, it["id"])
            yield json.dumps(full, default=str) + "\n"
    return StreamingResponse(gen(), media_type="application/x-ndjson", headers={"Content-Disposition": "attachment; filename=traces.jsonl"})


@router.get("/api/v1/traces/{trace_id}")
def get_trace(trace_id: str, request: Request, ctx: Ctx = Depends(read_ctx)):
    t = _st(request).get_trace(ctx.project_id, trace_id)
    if not t:
        raise HTTPException(404, "trace not found")
    return t


@router.delete("/api/v1/traces/{trace_id}")
def delete_trace(trace_id: str, request: Request, ctx: Ctx = Depends(write_ctx)):
    if not _st(request).delete_trace(ctx.project_id, trace_id):
        raise HTTPException(404, "trace not found")
    return {"deleted": trace_id}


@router.get("/api/v1/observations")
def list_observations(request: Request, ctx: Ctx = Depends(read_ctx), trace_id: Optional[str] = None, type: Optional[str] = None,
                      model: Optional[str] = None, name: Optional[str] = None, level: Optional[str] = None, start: Optional[str] = None,
                      end: Optional[str] = None, limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0)):
    items, total = _st(request).list_observations(ctx.project_id, ObservationFilter(trace_id, type, model, name, level, parse_time(start), parse_time(end)), limit, offset)
    return {"data": items, "total": total}


@router.get("/api/v1/sessions")
def list_sessions(request: Request, ctx: Ctx = Depends(read_ctx), limit: int = Query(50, ge=1, le=500), offset: int = 0):
    items, total = _st(request).list_sessions(ctx.project_id, limit, offset)
    return {"data": items, "total": total}


@router.get("/api/v1/metrics")
def metrics(request: Request, ctx: Ctx = Depends(read_ctx), start: Optional[str] = None, end: Optional[str] = None, bucket_seconds: Optional[int] = None,
            range: Optional[str] = Query(None, description="1h, 24h, 7d, 30d")):
    e = parse_time(end) or now()
    s = parse_time(start)
    if s is None:
        spans = {"1h": 3600, "6h": 6 * 3600, "24h": 86400, "7d": 7 * 86400, "30d": 30 * 86400, "90d": 90 * 86400}
        s = e - spans.get(range or "7d", 7 * 86400)
    return _st(request).metrics(ctx.project_id, s, e, bucket_seconds)


@router.get("/api/v1/filters")
def filter_options(request: Request, ctx: Ctx = Depends(read_ctx)):
    """Distinct values for dashboard filter dropdowns (last 30 days)."""
    st = _st(request)
    m = st.metrics(ctx.project_id, now() - 30 * 86400, now())
    return {"names": [x["name"] for x in m["by_name"]], "models": [x["model"] for x in m["by_model"]], "tags": [x["tag"] for x in m["by_tag"]],
            "users": [x["user_id"] for x in m["by_user"]], "scores": [x["name"] for x in m["scores"]]}


# ------------------------------------------------------------------ scores
class ScoreIn(BaseModel):
    trace_id: str
    name: str
    value: Optional[float] = None
    string_value: Optional[str] = None
    observation_id: Optional[str] = None
    comment: Optional[str] = None
    source: str = "api"


@router.post("/api/v1/scores")
def create_score(body: ScoreIn, request: Request, ctx: Ctx = Depends(write_ctx)):
    from .util import new_id
    row = {**body.model_dump(), "id": new_id("sc_"), "created_at": now()}
    _st(request).insert_scores(ctx.project_id, [row])
    return row


@router.get("/api/v1/scores")
def list_scores(request: Request, ctx: Ctx = Depends(read_ctx), trace_id: Optional[str] = None, name: Optional[str] = None, source: Optional[str] = None,
                start: Optional[str] = None, end: Optional[str] = None, limit: int = Query(100, ge=1, le=1000), offset: int = 0):
    items, total = _st(request).list_scores(ctx.project_id, ScoreFilter(trace_id, name, source, parse_time(start), parse_time(end)), limit, offset)
    return {"data": items, "total": total}


# ------------------------------------------------------------------ evaluations
@router.get("/api/v1/evaluators")
def evaluators():
    return {"data": list_evaluators(), "judge_available": get_judge().available(), "judge_models": settings.judge_models}


class EvalRunIn(BaseModel):
    evaluator: str
    config: Dict[str, Any] = {}
    trace_ids: Optional[List[str]] = None
    filter: Optional[Dict[str, Any]] = None
    limit: int = Field(20, ge=1, le=500)


@router.post("/api/v1/evals/run")
async def run_eval(body: EvalRunIn, request: Request, ctx: Ctx = Depends(write_ctx)):
    if get_evaluator(body.evaluator) is None:
        raise HTTPException(400, f"unknown evaluator {body.evaluator!r}")
    st = _st(request)
    ids = body.trace_ids
    if not ids:
        f = TraceFilter(**{k: v for k, v in (body.filter or {}).items() if k in TraceFilter.__dataclass_fields__})
        items, _ = await asyncio.to_thread(st.list_traces, ctx.project_id, f, body.limit, 0)
        ids = [i["id"] for i in items]
    if get_evaluator(body.evaluator).kind == "llm":
        for _ in ids:
            llm_eval_limiter.check(ctx.project_id)
    job = new_job(ctx.project_id, body.evaluator, len(ids))
    asyncio.create_task(run_job(st, ctx.project_id, job["id"], ids, body.evaluator, body.config))
    return job


@router.get("/api/v1/evals/jobs/{job_id}")
def eval_job(job_id: str, ctx: Ctx = Depends(read_ctx)):
    job = JOBS.get(job_id)
    if not job or job["project_id"] != ctx.project_id:
        raise HTTPException(404, "job not found")
    return job


class EvalTestIn(BaseModel):
    evaluator: str
    config: Dict[str, Any] = {}
    input: Any = None
    output: Any = None
    expected_output: Any = None
    context: List[str] = []


@router.post("/api/v1/evals/test")
async def test_eval(body: EvalTestIn, ctx: Ctx = Depends(write_ctx)):
    ev = get_evaluator(body.evaluator)
    if ev is None:
        raise HTTPException(400, f"unknown evaluator {body.evaluator!r}")
    if ev.kind == "llm":
        llm_eval_limiter.check(ctx.project_id)
    t0 = time.time()
    try:
        res = await ev.evaluate(EvalInput(body.input, body.output, body.expected_output, body.context), body.config)
    except Exception as e:
        raise HTTPException(502, f"evaluator failed: {e}")
    return {"score": res.score, "label": res.label, "reason": res.reason, "metadata": res.metadata, "ms": round((time.time() - t0) * 1000)}


@router.post("/api/v1/traces/{trace_id}/evaluate")
async def evaluate_one(trace_id: str, body: Dict[str, Any], request: Request, ctx: Ctx = Depends(write_ctx)):
    ev = get_evaluator(body.get("evaluator", ""))
    if ev is not None and ev.kind == "llm":
        llm_eval_limiter.check(ctx.project_id)
    try:
        s = await evaluate_trace(_st(request), ctx.project_id, trace_id, body.get("evaluator", ""), body.get("config") or {})
    except ValueError as e:
        raise HTTPException(400, str(e))
    if s is None:
        raise HTTPException(404, "trace not found or evaluator skipped")
    return s


class RuleIn(BaseModel):
    evaluator: str
    name: Optional[str] = None
    config: Dict[str, Any] = {}
    filter: Dict[str, Any] = {}
    sample_rate: float = Field(1.0, ge=0, le=1)
    enabled: bool = True


@router.get("/api/v1/eval-rules")
def list_rules(request: Request, ctx: Ctx = Depends(read_ctx)):
    return {"data": _st(request).list_eval_rules(ctx.project_id)}


@router.post("/api/v1/eval-rules")
def create_rule(body: RuleIn, request: Request, ctx: Ctx = Depends(write_ctx)):
    if get_evaluator(body.evaluator) is None:
        raise HTTPException(400, f"unknown evaluator {body.evaluator!r}")
    return _st(request).create_eval_rule(ctx.project_id, body.model_dump())


@router.delete("/api/v1/eval-rules/{rule_id}")
def delete_rule(rule_id: str, request: Request, ctx: Ctx = Depends(write_ctx)):
    if not _st(request).delete_eval_rule(ctx.project_id, rule_id):
        raise HTTPException(404, "rule not found")
    return {"deleted": rule_id}


# ------------------------------------------------------------------ datasets & experiments
class DatasetIn(BaseModel):
    name: str
    description: str = ""


class ItemsIn(BaseModel):
    items: List[Dict[str, Any]]


class RunIn(BaseModel):
    run_name: str
    links: List[Dict[str, str]]
    evaluators: List[Dict[str, Any]] = []
    metadata: Dict[str, Any] = {}


@router.get("/api/v1/datasets")
def list_datasets(request: Request, ctx: Ctx = Depends(read_ctx)):
    return {"data": _st(request).list_datasets(ctx.project_id)}


@router.post("/api/v1/datasets")
def create_dataset(body: DatasetIn, request: Request, ctx: Ctx = Depends(write_ctx)):
    return _st(request).create_dataset(ctx.project_id, body.name, body.description)


def _dataset_full(st, project_id: str, ds: Optional[Dict[str, Any]]):
    if not ds:
        raise HTTPException(404, "dataset not found")
    return {**ds, "items": st.list_dataset_items(project_id, ds["id"])}


@router.get("/api/v1/datasets/by-name/{name}")
def dataset_by_name(name: str, request: Request, ctx: Ctx = Depends(read_ctx)):
    st = _st(request)
    return _dataset_full(st, ctx.project_id, st.get_dataset(ctx.project_id, name=name))


@router.get("/api/v1/datasets/{dataset_id}")
def get_dataset(dataset_id: str, request: Request, ctx: Ctx = Depends(read_ctx)):
    st = _st(request)
    return _dataset_full(st, ctx.project_id, st.get_dataset(ctx.project_id, dataset_id=dataset_id))


@router.post("/api/v1/datasets/{dataset_id}/items")
def add_items(dataset_id: str, body: ItemsIn, request: Request, ctx: Ctx = Depends(write_ctx)):
    st = _st(request)
    if not st.get_dataset(ctx.project_id, dataset_id=dataset_id):
        raise HTTPException(404, "dataset not found")
    return {"data": st.add_dataset_items(ctx.project_id, dataset_id, body.items)}


@router.post("/api/v1/datasets/{dataset_id}/runs")
async def create_run(dataset_id: str, body: RunIn, request: Request, ctx: Ctx = Depends(write_ctx)):
    st = _st(request)
    ds = await asyncio.to_thread(st.get_dataset, ctx.project_id, dataset_id)
    if not ds:
        raise HTTPException(404, "dataset not found")
    items = {i["id"]: i for i in await asyncio.to_thread(st.list_dataset_items, ctx.project_id, dataset_id)}
    await asyncio.to_thread(st.add_run_items, ctx.project_id, dataset_id, body.run_name, body.links, body.metadata)
    await asyncio.sleep(0.2)
    tasks = []
    for link in body.links:
        item = items.get(link["item_id"])
        for ev in body.evaluators:
            name = ev.get("evaluator") or ev.get("name")
            if name and get_evaluator(name):
                tasks.append(evaluate_trace(st, ctx.project_id, link["trace_id"], name, ev.get("config") or {}, expected=item.get("expected_output") if item else None))
    await asyncio.gather(*tasks, return_exceptions=True)
    runs = await asyncio.to_thread(_runs_summary, st, ctx.project_id, dataset_id)
    return next((r for r in runs["runs"] if r["run_name"] == body.run_name), {"run_name": body.run_name})


def _runs_summary(st, project_id: str, dataset_id: str) -> Dict[str, Any]:
    links = st.list_run_items(project_id, dataset_id)
    by_run: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for l in links:
        by_run[l["run_name"]].append(l)
    runs, matrix = [], defaultdict(dict)
    for run_name, ls in by_run.items():
        tids = [l["trace_id"] for l in ls]
        traces, _ = st.list_traces(project_id, TraceFilter(ids=tids), len(tids) or 1, 0)
        tmap = {t["id"]: t for t in traces}
        score_vals: Dict[str, List[float]] = defaultdict(list)
        lat, cost, errs = [], 0.0, 0
        for l in ls:
            t = tmap.get(l["trace_id"])
            if not t:
                continue
            for s in t.get("scores") or []:
                if s.get("value") is not None:
                    score_vals[s["name"]].append(s["value"])
            if t.get("latency_ms") is not None:
                lat.append(t["latency_ms"])
            cost += t.get("total_cost") or 0
            errs += t.get("status") == "error"
            matrix[l["item_id"]][run_name] = {"trace_id": t["id"], "output": t.get("output"), "latency_ms": t.get("latency_ms"),
                                              "scores": {s["name"]: s.get("value") for s in t.get("scores") or []}}
        runs.append({"run_name": run_name, "items": len(ls), "created_at": min(l["created_at"] for l in ls), "metadata": ls[0].get("run_metadata"),
                     "avg_scores": {k: round(sum(v) / len(v), 4) for k, v in score_vals.items()}, "avg_latency_ms": round(sum(lat) / len(lat), 1) if lat else None,
                     "total_cost": round(cost, 8), "errors": errs})
    runs.sort(key=lambda r: r["created_at"])
    return {"runs": runs, "matrix": matrix}


@router.get("/api/v1/datasets/{dataset_id}/runs")
def list_runs(dataset_id: str, request: Request, ctx: Ctx = Depends(read_ctx)):
    st = _st(request)
    if not st.get_dataset(ctx.project_id, dataset_id=dataset_id):
        raise HTTPException(404, "dataset not found")
    return _runs_summary(st, ctx.project_id, dataset_id)


# ------------------------------------------------------------------ alerts
class AlertIn(BaseModel):
    metric: str
    threshold: float
    comparator: str = "gt"
    name: Optional[str] = None
    window_minutes: int = Field(60, ge=1, le=7 * 24 * 60)
    cooldown_minutes: int = Field(60, ge=0)
    webhook_url: Optional[str] = None
    filter: Dict[str, Any] = {}
    enabled: bool = True


@router.get("/api/v1/alerts/rules")
def list_alerts(request: Request, ctx: Ctx = Depends(read_ctx)):
    rules = _st(request).list_alert_rules(ctx.project_id)
    if ctx.read_only:
        for r in rules:
            r["webhook_url"] = "(hidden in demo)" if r.get("webhook_url") else None
    return {"data": rules, "metrics": list(alerts_mod.METRICS), "comparators": list(alerts_mod.OPS)}


@router.post("/api/v1/alerts/rules")
def create_alert(body: AlertIn, request: Request, ctx: Ctx = Depends(write_ctx)):
    if body.metric not in alerts_mod.METRICS:
        raise HTTPException(400, f"metric must be one of {alerts_mod.METRICS}")
    if body.comparator not in alerts_mod.OPS:
        raise HTTPException(400, f"comparator must be one of {list(alerts_mod.OPS)}")
    return _st(request).create_alert_rule(ctx.project_id, body.model_dump())


@router.delete("/api/v1/alerts/rules/{rule_id}")
def delete_alert(rule_id: str, request: Request, ctx: Ctx = Depends(write_ctx)):
    if not _st(request).delete_alert_rule(ctx.project_id, rule_id):
        raise HTTPException(404, "rule not found")
    return {"deleted": rule_id}


@router.post("/api/v1/alerts/rules/{rule_id}/check")
async def check_alert(rule_id: str, request: Request, ctx: Ctx = Depends(write_ctx), test: bool = False):
    st = _st(request)
    rule = next((r for r in await asyncio.to_thread(st.list_alert_rules, ctx.project_id) if r["id"] == rule_id), None)
    if not rule:
        raise HTTPException(404, "rule not found")
    ev = await alerts_mod.check_rule(st, rule, force=test, public_url=settings.public_url)
    value = await asyncio.to_thread(alerts_mod.compute_metric, st, rule)
    return {"value": value, "event": ev}


@router.get("/api/v1/alerts/events")
def alert_events(request: Request, ctx: Ctx = Depends(read_ctx), limit: int = Query(100, le=1000)):
    return {"data": _st(request).list_alert_events(ctx.project_id, limit)}
