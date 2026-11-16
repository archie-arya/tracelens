"""REST endpoints for the Cost Optimizer (mounted by ``main.create_app``)."""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional, Union

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from ..auth import Ctx, read_ctx, write_ctx
from ..evals import list_evaluators
from . import runner
from .registry import catalog

router = APIRouter(prefix="/api/v1/optimizer", tags=["optimizer"])
MAX_ACTIVE_PER_PROJECT = 2


def _st(request: Request):
    return request.app.state.storage


@router.get("/plugins")
def plugins():
    """Available providers (with whether they're configured), scorers, samplers and policies."""
    c = catalog()
    c["scorers"].append({"name": "eval:<evaluator>", "description": "Reuse any evaluator: " + ", ".join(e["name"] for e in list_evaluators()),
                         "kind": "adapter", "config": []})
    c["defaults"] = runner.DEFAULTS
    return c


@router.get("/steps")
async def steps(request: Request, lookback_days: float = Query(30, gt=0, le=365), ctx: Ctx = Depends(read_ctx)):
    """LLM steps seen in the window, with call volume, models, cost and how many calls are replayable."""
    return {"data": await asyncio.to_thread(runner.discover_steps, _st(request), ctx.project["id"], lookback_days)}


class RunIn(BaseModel):
    name: Optional[str] = None
    steps: List[Union[str, Dict[str, Any]]] = Field(min_length=1)
    candidates: List[str] = Field(min_length=1)
    sampler: Optional[str] = None
    sampler_config: Optional[Dict[str, Any]] = None
    sample_size: Optional[int] = None
    scorer: Optional[str] = None
    scorer_config: Optional[Dict[str, Any]] = None
    policy: Optional[str] = None
    policy_config: Optional[Dict[str, Any]] = None
    max_spend_usd: Optional[float] = Field(None, ge=0, le=1000)
    concurrency: Optional[int] = None
    rpm: Optional[int] = None
    redact: Optional[bool] = None
    lookback_days: Optional[float] = None
    examples_per_candidate: Optional[int] = None
    use_cache: Optional[bool] = None
    min_max_tokens: Optional[int] = None
    allow_tool_steps: Optional[bool] = None


@router.post("/runs", status_code=202)
async def create_run(body: RunIn, request: Request, ctx: Ctx = Depends(write_ctx)):
    pid = ctx.project["id"]
    active = [r for r in await asyncio.to_thread(_st(request).list_optimizer_runs, pid, 20) if r["id"] in runner.ACTIVE]
    if len(active) >= MAX_ACTIVE_PER_PROJECT:
        raise HTTPException(429, f"at most {MAX_ACTIVE_PER_PROJECT} optimizer runs can run at once per project")
    try:
        run = await asyncio.to_thread(runner.create_run, _st(request), pid, body.model_dump(exclude_none=True))
    except runner.ConfigError as e:
        raise HTTPException(422, str(e))
    runner.start_run(_st(request), pid, run)
    return run


@router.get("/runs")
async def list_runs(request: Request, limit: int = Query(50, le=200), ctx: Ctx = Depends(read_ctx)):
    rows = await asyncio.to_thread(_st(request).list_optimizer_runs, ctx.project["id"], limit)
    return {"data": [runner.status_of(r) for r in rows]}


@router.get("/runs/{run_id}")
async def get_run(run_id: str, request: Request, ctx: Ctx = Depends(read_ctx)):
    r = await asyncio.to_thread(_st(request).get_optimizer_run, ctx.project["id"], run_id)
    if not r:
        raise HTTPException(404, "run not found")
    return runner.status_of(r)


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: str, request: Request, ctx: Ctx = Depends(write_ctx)):
    r = await asyncio.to_thread(_st(request).get_optimizer_run, ctx.project["id"], run_id)
    if not r:
        raise HTTPException(404, "run not found")
    if run_id in runner.ACTIVE:
        runner.CANCELLED.add(run_id)
    return {"ok": True}


@router.delete("/runs/{run_id}")
async def delete_run(run_id: str, request: Request, ctx: Ctx = Depends(write_ctx)):
    if run_id in runner.ACTIVE:
        runner.CANCELLED.add(run_id)
    if not await asyncio.to_thread(_st(request).delete_optimizer_run, ctx.project["id"], run_id):
        raise HTTPException(404, "run not found")
    return {"ok": True}
