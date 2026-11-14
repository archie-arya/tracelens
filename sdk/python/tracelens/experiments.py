"""Datasets & experiments: run your app over a dataset and compare runs in the dashboard."""
from __future__ import annotations

import time
import uuid
from typing import Any, Callable, Dict, List, Optional, Sequence

from .client import TraceLens, get_client


def create_dataset(name: str, items: Sequence[Dict[str, Any]], description: str = "", client: Optional[TraceLens] = None) -> Dict[str, Any]:
    """Create (or extend) a dataset. Each item: ``{"input": ..., "expected_output": ..., "metadata": {...}}``."""
    c = client or get_client()
    ds = c.api("POST", "/api/v1/datasets", {"name": name, "description": description})
    c.api("POST", f"/api/v1/datasets/{ds['id']}/items", {"items": list(items)})
    return ds


def run_experiment(dataset: str, task: Callable[[Any], Any], *, run_name: Optional[str] = None, evaluators: Sequence[Any] = ("similarity",),
                   metadata: Optional[Dict[str, Any]] = None, client: Optional[TraceLens] = None) -> Dict[str, Any]:
    """Run ``task(item_input)`` for every dataset item, trace it, link it to the run and score it server-side.

    ``evaluators`` are server evaluator names (``"similarity"``, ``"exact_match"``, ``"llm_relevance"``...) or
    ``{"evaluator": "llm_rubric", "config": {...}}`` dicts. Returns the run summary.
    """
    c = client or get_client()
    ds = c.api("GET", f"/api/v1/datasets/by-name/{dataset}")
    run_name = run_name or f"run-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}"
    links: List[Dict[str, str]] = []
    for item in ds["items"]:
        with c.trace(f"experiment:{dataset}", input=item["input"], tags=["experiment", run_name],
                     metadata={"dataset": dataset, "dataset_item_id": item["id"], "run": run_name, **(metadata or {})}) as t:
            with c.span("task", input=item["input"]) as s:
                try:
                    out = task(item["input"])
                    s.output = out
                except Exception as e:  # keep going; error is recorded on the span
                    s.record_error(e)
                    out = None
            t.output = out
        links.append({"item_id": item["id"], "trace_id": t.id})
    c.flush()
    evs = [e if isinstance(e, dict) else {"evaluator": e} for e in evaluators]
    return c.api("POST", f"/api/v1/datasets/{ds['id']}/runs", {"run_name": run_name, "links": links, "evaluators": evs, "metadata": metadata or {}}, timeout=600)
