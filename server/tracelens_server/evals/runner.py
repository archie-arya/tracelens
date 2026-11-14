"""Run evaluators against traces: on demand (jobs), automatically (rules) and for dataset experiments."""
from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Any, Dict, List, Optional

from ..config import settings
from ..storage import Storage
from ..util import new_id, now
from .base import EvalInput, EvalResult
from .registry import get_evaluator

logger = logging.getLogger("tracelens.evals")
_sem: Optional[asyncio.Semaphore] = None
JOBS: Dict[str, Dict[str, Any]] = {}


def _semaphore() -> asyncio.Semaphore:
    global _sem
    if _sem is None:
        _sem = asyncio.Semaphore(settings.max_eval_concurrency)
    return _sem


def _doc_text(d: Any) -> str:
    if isinstance(d, str):
        return d
    if isinstance(d, dict):
        for k in ("page_content", "content", "text", "chunk", "document"):
            if isinstance(d.get(k), str):
                return d[k]
    return str(d)


def build_eval_input(trace: Dict[str, Any], expected: Any = None, observation_name: Optional[str] = None) -> EvalInput:
    obs = trace.get("observations") or []
    inp, out = trace.get("input"), trace.get("output")
    if observation_name:
        for o in obs:
            if o.get("name") == observation_name:
                inp, out = o.get("input"), o.get("output")
                break
    context: List[str] = []
    for o in obs:
        if o.get("type") == "retrieval" and o.get("output") is not None:
            docs = o["output"]
            if isinstance(docs, dict) and isinstance(docs.get("documents"), list):
                docs = docs["documents"]
            context.extend(_doc_text(d) for d in (docs if isinstance(docs, list) else [docs]))
    md = trace.get("metadata") or {}
    if md.get("context"):
        c = md["context"]
        context.extend(_doc_text(d) for d in (c if isinstance(c, list) else [c]))
    if expected is None:
        expected = md.get("expected_output")
    return EvalInput(input=inp, output=out, expected_output=expected, context=context, metadata=md, trace=trace)


async def evaluate_trace(storage: Storage, project_id: str, trace_id: str, evaluator: str, config: Optional[Dict[str, Any]] = None,
                         rule_id: Optional[str] = None, expected: Any = None) -> Optional[Dict[str, Any]]:
    config = config or {}
    ev = get_evaluator(evaluator)
    if ev is None:
        raise ValueError(f"unknown evaluator {evaluator!r}")
    trace = await asyncio.to_thread(storage.get_trace, project_id, trace_id)
    if trace is None:
        return None
    inp = build_eval_input(trace, expected, config.get("observation_name"))
    score_name = config.get("score_name") or ev.score_name or ev.name
    t0 = time.time()
    async with _semaphore():
        try:
            res = await ev.evaluate(inp, config)
        except Exception as e:
            logger.warning("evaluator %s failed on %s: %s", evaluator, trace_id, e)
            res = EvalResult(None, "error", f"{type(e).__name__}: {e}"[:500])
    if res.score is None and res.label in ("skipped",):
        return None
    score = {"id": new_id("sc_"), "trace_id": trace_id, "name": score_name, "value": res.score, "string_value": res.label,
             "comment": res.reason, "source": "eval", "evaluator": evaluator, "rule_id": rule_id, "created_at": now(),
             "metadata": {**(res.metadata or {}), "eval_ms": round((time.time() - t0) * 1000), "higher_is_better": ev.higher_is_better}}
    await asyncio.to_thread(storage.insert_scores, project_id, [score])
    return score


async def run_job(storage: Storage, project_id: str, job_id: str, trace_ids: List[str], evaluator: str, config: Dict[str, Any]) -> None:
    job = JOBS[job_id]
    job["status"] = "running"

    async def one(tid: str) -> None:
        try:
            s = await evaluate_trace(storage, project_id, tid, evaluator, config)
            if s:
                job["scores"].append({"trace_id": tid, "value": s["value"], "label": s["string_value"], "reason": s["comment"]})
        except Exception as e:
            job["errors"].append(f"{tid}: {e}")
        job["done"] += 1

    await asyncio.gather(*(one(t) for t in trace_ids))
    vals = [s["value"] for s in job["scores"] if s["value"] is not None]
    job["avg_score"] = round(sum(vals) / len(vals), 4) if vals else None
    job["status"] = "completed"
    job["finished_at"] = now()


def new_job(project_id: str, evaluator: str, total: int) -> Dict[str, Any]:
    jid = new_id("job_")
    JOBS[jid] = {"id": jid, "project_id": project_id, "evaluator": evaluator, "status": "queued", "total": total, "done": 0,
                 "scores": [], "errors": [], "created_at": now()}
    if len(JOBS) > 500:  # bound memory
        for k in sorted(JOBS, key=lambda k: JOBS[k]["created_at"])[:100]:
            JOBS.pop(k, None)
    return JOBS[jid]


def _matches(rule_filter: Dict[str, Any], trace: Dict[str, Any]) -> bool:
    if not rule_filter:
        return True
    if rule_filter.get("name") and trace.get("name") != rule_filter["name"]:
        return False
    tags = set(trace.get("tags") or [])
    if rule_filter.get("tags") and not set(rule_filter["tags"]) & tags:
        return False
    if rule_filter.get("exclude_tags") and set(rule_filter["exclude_tags"]) & tags:
        return False
    if rule_filter.get("environment") and trace.get("environment") != rule_filter["environment"]:
        return False
    return True


async def apply_rules(storage: Storage, project_id: str, trace_ids: List[str]) -> None:
    """Online evaluation: run matching eval rules on newly completed traces (idempotent)."""
    rules = [r for r in await asyncio.to_thread(storage.list_eval_rules, project_id) if r.get("enabled")]
    if not rules:
        return
    tasks = []
    for tid in set(trace_ids):
        trace = await asyncio.to_thread(storage.get_trace, project_id, tid)
        if not trace or trace.get("output") is None or not trace.get("end_time"):
            continue
        for r in rules:
            if not _matches(r.get("filter") or {}, trace) or random.random() > (r.get("sample_rate") or 0):
                continue
            ev = get_evaluator(r["evaluator"])
            if ev is None:
                continue
            sname = (r.get("config") or {}).get("score_name") or ev.score_name or ev.name
            if await asyncio.to_thread(storage.has_score, project_id, tid, sname, r["id"]):
                continue
            tasks.append(evaluate_trace(storage, project_id, tid, r["evaluator"], r.get("config") or {}, rule_id=r["id"]))
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
