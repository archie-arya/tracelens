"""Alert rules: periodically compute a metric over a sliding window and POST a webhook when it crosses a threshold.

Metrics: ``cost``, ``error_rate``, ``latency_p95``, ``latency_p50``, ``trace_count``, ``tokens``, ``score_avg`` (filter.score_name).
Webhook payload is Slack- and Discord-compatible (``text`` + ``content``) and includes structured fields.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

import httpx

from .storage import Storage
from .util import now, percentile

logger = logging.getLogger("tracelens.alerts")
METRICS = ("cost", "error_rate", "latency_p95", "latency_p50", "trace_count", "tokens", "score_avg")
OPS = {"gt": lambda a, b: a > b, "gte": lambda a, b: a >= b, "lt": lambda a, b: a < b, "lte": lambda a, b: a <= b}


def compute_metric(storage: Storage, rule: Dict[str, Any], at: Optional[float] = None) -> Optional[float]:
    end = at or now()
    start = end - 60 * int(rule.get("window_minutes") or 60)
    trows, _, srows = storage.metric_rows(rule["project_id"], start, end)
    f = rule.get("filter") or {}
    if f.get("trace_name"):
        trows = [t for t in trows if t.get("name") == f["trace_name"]]
    if f.get("tag"):
        trows = [t for t in trows if f["tag"] in (t.get("tags") or [])]
    m = rule["metric"]
    if m == "score_avg":
        vals = [s["value"] for s in srows if s.get("value") is not None and (not f.get("score_name") or s["name"] == f["score_name"])]
        return sum(vals) / len(vals) if vals else None
    if m == "trace_count":
        return float(len(trows))
    if not trows:
        return None
    if m == "cost":
        return sum(t.get("total_cost") or 0 for t in trows)
    if m == "tokens":
        return float(sum(t.get("total_tokens") or 0 for t in trows))
    if m == "error_rate":
        return sum(1 for t in trows if t.get("status") == "error") / len(trows)
    if m in ("latency_p95", "latency_p50"):
        return percentile([t["latency_ms"] for t in trows if t.get("latency_ms") is not None], 95 if m.endswith("95") else 50)
    return None


async def deliver(url: str, payload: Dict[str, Any]) -> tuple[bool, str]:
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(url, json=payload)
        return r.status_code < 400, f"HTTP {r.status_code}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


async def check_rule(storage: Storage, rule: Dict[str, Any], force: bool = False, public_url: str = "") -> Optional[Dict[str, Any]]:
    value = await asyncio.to_thread(compute_metric, storage, rule)
    fields: Dict[str, Any] = {"last_value": value}
    triggered = value is not None and OPS.get(rule["comparator"], OPS["gt"])(value, rule["threshold"])
    event = None
    cooldown_ok = not rule.get("last_triggered_at") or now() - rule["last_triggered_at"] > 60 * int(rule.get("cooldown_minutes") or 60)
    if force or (triggered and cooldown_ok):
        sym = {"gt": ">", "gte": ">=", "lt": "<", "lte": "<="}.get(rule["comparator"], ">")
        vtxt = "n/a" if value is None else f"{value:.4g}"
        msg = (f"{'[TEST] ' if force else ''}TraceLens alert '{rule['name']}': {rule['metric']} = {vtxt} "
               f"({sym} {rule['threshold']:g}) over the last {rule['window_minutes']} min")
        payload = {"text": msg, "content": msg, "rule": {k: rule.get(k) for k in ("id", "name", "metric", "comparator", "threshold", "window_minutes")},
                   "value": value, "project_id": rule["project_id"], "test": force, "triggered_at": now(), "dashboard": public_url}
        ok, resp = (await deliver(rule["webhook_url"], payload)) if rule.get("webhook_url") else (False, "no webhook configured")
        event = await asyncio.to_thread(storage.add_alert_event, rule["project_id"], {"rule_id": rule["id"], "value": value, "message": msg, "delivered": ok, "response": resp})
        if not force:
            fields["last_triggered_at"] = now()
    await asyncio.to_thread(storage.update_alert_rule, rule["project_id"], rule["id"], fields)
    return event


async def alert_loop(storage: Storage, interval: int) -> None:
    while True:
        await asyncio.sleep(interval)
        try:
            rules = await asyncio.to_thread(storage.list_alert_rules, None)
            for r in rules:
                if r.get("enabled"):
                    try:
                        await check_rule(storage, r)
                    except Exception as e:
                        logger.warning("alert rule %s failed: %s", r.get("id"), e)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # pragma: no cover
            logger.warning("alert loop error: %s", e)
