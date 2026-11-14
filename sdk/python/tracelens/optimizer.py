"""Cost Optimizer helpers: replay real traffic on cheaper models and get switch recommendations.

    import tracelens
    from tracelens.optimizer import optimize

    tracelens.init()
    run = optimize(steps=["support-rag / gemini.generate_content"],
                   candidates=["gemini:gemma-4-26b-a4b-it", "groq:llama-3.1-8b-instant", "ollama:llama3.1"],
                   sample_size=20, max_spend_usd=0.50)
    for rec in recommendations(run):
        print(rec["step"], rec["action"], rec.get("to_model"), rec.get("cost_savings_pct"), rec.get("quality_delta_pct"))

Everything runs server-side (replays, scoring, caching, rate limits and the spend cap); this module just calls the API.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Sequence, Union

from .client import TraceLens, get_client

Step = Union[str, Dict[str, Any]]


def list_steps(lookback_days: float = 30, client: Optional[TraceLens] = None) -> List[Dict[str, Any]]:
    """LLM steps seen recently, with volume, models, cost and how many calls are replayable."""
    return (client or get_client()).api("GET", f"/api/v1/optimizer/steps?lookback_days={lookback_days}")["data"]


def plugins(client: Optional[TraceLens] = None) -> Dict[str, Any]:
    """Providers (and whether the server has credentials for them), scorers, samplers and policies."""
    return (client or get_client()).api("GET", "/api/v1/optimizer/plugins")


def start(steps: Sequence[Step], candidates: Sequence[str], client: Optional[TraceLens] = None, **options: Any) -> Dict[str, Any]:
    """Start an optimization run and return immediately. Options: sampler, sample_size, scorer, scorer_config, policy,
    policy_config, max_spend_usd, concurrency, rpm, redact, lookback_days, examples_per_candidate, use_cache, name."""
    body = {"steps": list(steps), "candidates": list(candidates), **{k: v for k, v in options.items() if v is not None}}
    return (client or get_client()).api("POST", "/api/v1/optimizer/runs", body)


def get_run(run_id: str, client: Optional[TraceLens] = None) -> Dict[str, Any]:
    return (client or get_client()).api("GET", f"/api/v1/optimizer/runs/{run_id}")


def wait(run_id: str, timeout: float = 3600, poll: float = 3.0, client: Optional[TraceLens] = None, on_progress=None) -> Dict[str, Any]:
    deadline = time.time() + timeout
    while True:
        run = get_run(run_id, client)
        if on_progress:
            on_progress(run)
        if run["status"] not in ("queued", "running") or time.time() > deadline:
            return run
        time.sleep(poll)


def optimize(steps: Sequence[Step], candidates: Sequence[str], *, wait_for_result: bool = True, timeout: float = 3600,
             client: Optional[TraceLens] = None, **options: Any) -> Dict[str, Any]:
    """Start a run and (by default) block until it finishes. Returns the full run with ``results``."""
    run = start(steps, candidates, client=client, **options)
    return wait(run["id"], timeout=timeout, client=client) if wait_for_result else run


def recommendations(run: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [s["recommendation"] for s in ((run.get("results") or {}).get("steps") or [])]
