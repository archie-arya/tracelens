"""Storage interface. Implement :class:`Storage` to plug in another backend (ClickHouse, DuckDB, Mongo...).

Select it with ``TRACELENS_STORAGE=my_package.module:MyStorage`` (constructor receives the database URL).
Only the abstract methods are required; :meth:`metrics` has a portable default you may override to push
aggregation down into your database.
"""
from __future__ import annotations

import abc
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..util import percentile


@dataclass
class TraceFilter:
    q: Optional[str] = None
    name: Optional[str] = None
    user_id: Optional[str] = None
    session_id: Optional[str] = None
    tag: Optional[str] = None
    status: Optional[str] = None  # ok | error
    model: Optional[str] = None
    release: Optional[str] = None
    environment: Optional[str] = None
    start: Optional[float] = None
    end: Optional[float] = None
    min_latency_ms: Optional[float] = None
    min_cost: Optional[float] = None
    ids: Optional[List[str]] = None
    order_by: str = "start_time"
    desc: bool = True


@dataclass
class ObservationFilter:
    trace_id: Optional[str] = None
    type: Optional[str] = None
    model: Optional[str] = None
    name: Optional[str] = None
    level: Optional[str] = None
    start: Optional[float] = None
    end: Optional[float] = None


@dataclass
class ScoreFilter:
    trace_id: Optional[str] = None
    name: Optional[str] = None
    source: Optional[str] = None
    start: Optional[float] = None
    end: Optional[float] = None


class Storage(abc.ABC):
    # ---- lifecycle
    def init(self) -> None:
        """Create tables / indexes. Called on startup."""

    # ---- projects & keys
    @abc.abstractmethod
    def create_project(self, name: str, slug: Optional[str] = None, public: bool = False) -> Dict[str, Any]: ...
    @abc.abstractmethod
    def get_project(self, project_id: str) -> Optional[Dict[str, Any]]: ...
    @abc.abstractmethod
    def get_project_by_slug(self, slug: str) -> Optional[Dict[str, Any]]: ...
    @abc.abstractmethod
    def list_projects(self) -> List[Dict[str, Any]]: ...
    @abc.abstractmethod
    def create_api_key(self, project_id: str, name: str, key_hash: str, display: str) -> Dict[str, Any]: ...
    @abc.abstractmethod
    def list_api_keys(self, project_id: str) -> List[Dict[str, Any]]: ...
    @abc.abstractmethod
    def revoke_api_key(self, project_id: str, key_id: str) -> bool: ...
    @abc.abstractmethod
    def get_project_by_key_hash(self, key_hash: str) -> Optional[Dict[str, Any]]: ...

    # ---- ingestion (idempotent upserts)
    @abc.abstractmethod
    def upsert_traces(self, project_id: str, items: List[Dict[str, Any]]) -> None: ...
    @abc.abstractmethod
    def upsert_observations(self, project_id: str, items: List[Dict[str, Any]]) -> None: ...
    @abc.abstractmethod
    def insert_scores(self, project_id: str, items: List[Dict[str, Any]]) -> None: ...
    @abc.abstractmethod
    def refresh_trace_aggregates(self, project_id: str, trace_ids: Sequence[str]) -> None: ...

    # ---- queries
    @abc.abstractmethod
    def list_traces(self, project_id: str, f: TraceFilter, limit: int = 50, offset: int = 0) -> Tuple[List[Dict[str, Any]], int]: ...
    @abc.abstractmethod
    def get_trace(self, project_id: str, trace_id: str) -> Optional[Dict[str, Any]]: ...
    @abc.abstractmethod
    def delete_trace(self, project_id: str, trace_id: str) -> bool: ...
    @abc.abstractmethod
    def list_observations(self, project_id: str, f: ObservationFilter, limit: int = 100, offset: int = 0) -> Tuple[List[Dict[str, Any]], int]: ...
    @abc.abstractmethod
    def list_scores(self, project_id: str, f: ScoreFilter, limit: int = 100, offset: int = 0) -> Tuple[List[Dict[str, Any]], int]: ...
    @abc.abstractmethod
    def list_sessions(self, project_id: str, limit: int = 50, offset: int = 0) -> Tuple[List[Dict[str, Any]], int]: ...
    @abc.abstractmethod
    def metric_rows(self, project_id: str, start: float, end: float) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Return (trace rows, generation rows, score rows) in the window, with the columns used by :meth:`metrics`."""

    # ---- eval rules
    @abc.abstractmethod
    def list_eval_rules(self, project_id: str) -> List[Dict[str, Any]]: ...
    @abc.abstractmethod
    def create_eval_rule(self, project_id: str, rule: Dict[str, Any]) -> Dict[str, Any]: ...
    @abc.abstractmethod
    def delete_eval_rule(self, project_id: str, rule_id: str) -> bool: ...
    @abc.abstractmethod
    def has_score(self, project_id: str, trace_id: str, name: str, rule_id: Optional[str] = None) -> bool: ...

    # ---- datasets
    @abc.abstractmethod
    def create_dataset(self, project_id: str, name: str, description: str = "") -> Dict[str, Any]: ...
    @abc.abstractmethod
    def get_dataset(self, project_id: str, dataset_id: Optional[str] = None, name: Optional[str] = None) -> Optional[Dict[str, Any]]: ...
    @abc.abstractmethod
    def list_datasets(self, project_id: str) -> List[Dict[str, Any]]: ...
    @abc.abstractmethod
    def add_dataset_items(self, project_id: str, dataset_id: str, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]: ...
    @abc.abstractmethod
    def list_dataset_items(self, project_id: str, dataset_id: str) -> List[Dict[str, Any]]: ...
    @abc.abstractmethod
    def add_run_items(self, project_id: str, dataset_id: str, run_name: str, links: List[Dict[str, Any]], run_metadata: Dict[str, Any]) -> None: ...
    @abc.abstractmethod
    def list_run_items(self, project_id: str, dataset_id: str) -> List[Dict[str, Any]]: ...

    # ---- alerts
    @abc.abstractmethod
    def list_alert_rules(self, project_id: Optional[str] = None) -> List[Dict[str, Any]]: ...
    @abc.abstractmethod
    def create_alert_rule(self, project_id: str, rule: Dict[str, Any]) -> Dict[str, Any]: ...
    @abc.abstractmethod
    def update_alert_rule(self, project_id: str, rule_id: str, fields: Dict[str, Any]) -> None: ...
    @abc.abstractmethod
    def delete_alert_rule(self, project_id: str, rule_id: str) -> bool: ...
    @abc.abstractmethod
    def add_alert_event(self, project_id: str, event: Dict[str, Any]) -> Dict[str, Any]: ...
    @abc.abstractmethod
    def list_alert_events(self, project_id: str, limit: int = 100) -> List[Dict[str, Any]]: ...

    # ---- metrics (portable default implementation)
    # --- cost optimizer (non-abstract so older custom backends keep working; override to support the optimizer)
    def save_optimizer_run(self, project_id: str, run: Dict[str, Any]) -> None:
        raise NotImplementedError("this storage backend does not support the cost optimizer")

    def get_optimizer_run(self, project_id: str, run_id: str) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

    def list_optimizer_runs(self, project_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def delete_optimizer_run(self, project_id: str, run_id: str) -> bool:
        raise NotImplementedError

    def replay_cache_get(self, key: str) -> Optional[Dict[str, Any]]:
        return None

    def replay_cache_put(self, key: str, value: Dict[str, Any]) -> None:
        return None

    def metrics(self, project_id: str, start: float, end: float, bucket_seconds: Optional[int] = None) -> Dict[str, Any]:
        trows, grows, srows = self.metric_rows(project_id, start, end)
        return compute_metrics(trows, grows, srows, start, end, bucket_seconds)


_BUCKETS = (60, 300, 900, 1800, 3600, 3 * 3600, 6 * 3600, 12 * 3600, 86400, 7 * 86400)


def choose_bucket(start: float, end: float, target: int = 48) -> int:
    span = max(1.0, end - start)
    for b in _BUCKETS:
        if span / b <= target:
            return b
    return _BUCKETS[-1]


def _r(x: Optional[float], n: int = 2) -> Optional[float]:
    return None if x is None else round(x, n)


def compute_metrics(trows: List[Dict[str, Any]], grows: List[Dict[str, Any]], srows: List[Dict[str, Any]], start: float, end: float,
                    bucket_seconds: Optional[int] = None) -> Dict[str, Any]:
    bucket = bucket_seconds or choose_bucket(start, end)
    first = int(start // bucket * bucket)
    nb = int((end - first) // bucket) + 1
    series = [{"t": first + i * bucket, "traces": 0, "errors": 0, "cost": 0.0, "tokens": 0, "_lat": [], "generations": 0} for i in range(nb)]

    def bi(t: Optional[float]) -> Optional[int]:
        if t is None:
            return None
        i = int((t - first) // bucket)
        return i if 0 <= i < nb else None

    lat = []
    total_cost = 0.0
    total_tokens = 0
    errors = 0
    by_name: Dict[str, Dict[str, Any]] = defaultdict(lambda: {"count": 0, "errors": 0, "cost": 0.0, "_lat": []})
    by_user: Dict[str, Dict[str, Any]] = defaultdict(lambda: {"count": 0, "cost": 0.0, "tokens": 0})
    by_tag: Dict[str, Dict[str, Any]] = defaultdict(lambda: {"count": 0, "cost": 0.0, "errors": 0})
    for r in trows:
        i = bi(r.get("start_time"))
        cost = r.get("total_cost") or 0.0
        toks = r.get("total_tokens") or 0
        err = r.get("status") == "error"
        total_cost += cost
        total_tokens += toks
        errors += err
        if r.get("latency_ms") is not None:
            lat.append(r["latency_ms"])
        if i is not None:
            s = series[i]
            s["traces"] += 1
            s["errors"] += err
            s["cost"] += cost
            s["tokens"] += toks
            if r.get("latency_ms") is not None:
                s["_lat"].append(r["latency_ms"])
        n = by_name[r.get("name") or "(unnamed)"]
        n["count"] += 1
        n["errors"] += err
        n["cost"] += cost
        if r.get("latency_ms") is not None:
            n["_lat"].append(r["latency_ms"])
        if r.get("user_id"):
            u = by_user[r["user_id"]]
            u["count"] += 1
            u["cost"] += cost
            u["tokens"] += toks
        for tag in (r.get("tags") or []):
            t = by_tag[tag]
            t["count"] += 1
            t["cost"] += cost
            t["errors"] += err

    by_model: Dict[str, Dict[str, Any]] = defaultdict(lambda: {"calls": 0, "errors": 0, "cost": 0.0, "prompt_tokens": 0, "completion_tokens": 0, "_lat": [], "_ttft": []})
    for g in grows:
        m = by_model[g.get("model") or "(unknown)"]
        m["calls"] += 1
        m["errors"] += g.get("level") == "ERROR"
        m["cost"] += g.get("cost") or 0.0
        m["prompt_tokens"] += g.get("prompt_tokens") or 0
        m["completion_tokens"] += g.get("completion_tokens") or 0
        if g.get("latency_ms") is not None:
            m["_lat"].append(g["latency_ms"])
        if g.get("completion_start_time") and g.get("start_time"):
            m["_ttft"].append((g["completion_start_time"] - g["start_time"]) * 1000)
        i = bi(g.get("start_time"))
        if i is not None:
            series[i]["generations"] += 1

    score_series: Dict[str, Dict[int, List[float]]] = defaultdict(lambda: defaultdict(list))
    score_tot: Dict[str, List[float]] = defaultdict(list)
    for s in srows:
        if s.get("value") is None:
            continue
        score_tot[s["name"]].append(s["value"])
        i = bi(s.get("created_at"))
        if i is not None:
            score_series[s["name"]][i].append(s["value"])

    for s in series:
        lats = s.pop("_lat")
        s["p50_latency_ms"] = _r(percentile(lats, 50), 1)
        s["p95_latency_ms"] = _r(percentile(lats, 95), 1)
        s["error_rate"] = _r(s["errors"] / s["traces"], 4) if s["traces"] else 0.0
        s["cost"] = round(s["cost"], 8)
    for idx, s in enumerate(series):
        for name, buckets in score_series.items():
            vals = buckets.get(idx)
            s[f"score:{name}"] = _r(sum(vals) / len(vals), 4) if vals else None

    def fin_lat(d: Dict[str, Any]) -> Dict[str, Any]:
        lats = d.pop("_lat", [])
        ttft = d.pop("_ttft", None)
        d["avg_latency_ms"] = _r(sum(lats) / len(lats), 1) if lats else None
        d["p95_latency_ms"] = _r(percentile(lats, 95), 1)
        if ttft is not None:
            d["avg_ttft_ms"] = _r(sum(ttft) / len(ttft), 1) if ttft else None
        if "cost" in d:
            d["cost"] = round(d["cost"], 8)
        return d

    n_traces = len(trows)
    return {
        "start": start, "end": end, "bucket_seconds": bucket,
        "totals": {
            "traces": n_traces, "generations": len(grows), "cost": round(total_cost, 8), "tokens": total_tokens,
            "errors": errors, "error_rate": _r(errors / n_traces, 4) if n_traces else 0.0,
            "p50_latency_ms": _r(percentile(lat, 50), 1), "p95_latency_ms": _r(percentile(lat, 95), 1), "p99_latency_ms": _r(percentile(lat, 99), 1),
            "avg_cost_per_trace": round(total_cost / n_traces, 8) if n_traces else 0.0,
            "users": len(by_user),
        },
        "timeseries": series,
        "by_model": sorted(({"model": k, **fin_lat(v)} for k, v in by_model.items()), key=lambda x: -x["calls"]),
        "by_name": sorted(({"name": k, **fin_lat(v)} for k, v in by_name.items()), key=lambda x: -x["count"])[:50],
        "by_user": sorted(({"user_id": k, **{kk: (round(vv, 8) if kk == "cost" else vv) for kk, vv in v.items()}} for k, v in by_user.items()), key=lambda x: -x["cost"])[:50],
        "by_tag": sorted(({"tag": k, **{kk: (round(vv, 8) if kk == "cost" else vv) for kk, vv in v.items()}} for k, v in by_tag.items()), key=lambda x: -x["count"])[:50],
        "scores": sorted(({"name": k, "count": len(v), "avg": _r(sum(v) / len(v), 4), "min": _r(min(v), 4), "max": _r(max(v), 4)} for k, v in score_tot.items()), key=lambda x: x["name"]),
    }
