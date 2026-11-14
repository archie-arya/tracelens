"""SQLAlchemy storage backend: SQLite (default, zero-config) and PostgreSQL (``postgresql+psycopg://...``)."""
from __future__ import annotations

import os
import re
import threading
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from sqlalchemy import and_, create_engine, delete, event, func, insert, or_, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from .. import db
from ..util import new_id, now, pipe_join, text_of
from .base import ObservationFilter, ScoreFilter, Storage, TraceFilter

TRACE_FIELDS = ("name", "user_id", "session_id", "input", "output", "release", "environment", "start_time", "end_time", "sdk")
OBS_FIELDS = ("trace_id", "parent_id", "type", "name", "input", "output", "model", "model_parameters", "prompt_tokens", "completion_tokens",
              "total_tokens", "cost", "level", "status_message", "start_time", "end_time", "completion_start_time")


def _slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:60] or "project"


def _row(r: Any) -> Dict[str, Any]:
    return dict(r._mapping)


def _chunks(seq: Sequence[Any], n: int = 500) -> Iterable[Sequence[Any]]:
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


class SQLStorage(Storage):
    def __init__(self, url: str):
        self.url = url
        kwargs: Dict[str, Any] = {"pool_pre_ping": True, "future": True}
        if url.startswith("sqlite"):
            path = url.split("///", 1)[-1]
            if path and path != ":memory:" and not path.startswith("file:"):
                os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
            kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
            if path == ":memory:" or url == "sqlite://":
                from sqlalchemy.pool import StaticPool
                kwargs["poolclass"] = StaticPool
        elif url.startswith("postgres://"):
            url = "postgresql+psycopg://" + url[len("postgres://"):]
        elif url.startswith("postgresql://"):
            url = "postgresql+psycopg://" + url[len("postgresql://"):]
        self.engine: Engine = create_engine(url, **kwargs)
        self._write_lock = threading.RLock() if url.startswith("sqlite") else None
        if url.startswith("sqlite"):
            @event.listens_for(self.engine, "connect")
            def _pragma(dbapi_conn, _):  # pragma: no cover - trivial
                cur = dbapi_conn.cursor()
                cur.execute("PRAGMA journal_mode=WAL")
                cur.execute("PRAGMA synchronous=NORMAL")
                cur.execute("PRAGMA busy_timeout=30000")
                cur.close()

    def init(self) -> None:
        db.metadata.create_all(self.engine)

    def _w(self):
        """Serialize writes on SQLite (single writer), no-op elsewhere."""
        return self._write_lock if self._write_lock is not None else _NullCtx()

    # ------------------------------------------------------------------ projects & keys
    def create_project(self, name: str, slug: Optional[str] = None, public: bool = False) -> Dict[str, Any]:
        base = _slugify(slug or name)
        with self._w(), self.engine.begin() as c:
            s, i = base, 1
            while c.execute(select(db.projects.c.id).where(db.projects.c.slug == s)).first():
                i += 1
                s = f"{base}-{i}"
            row = {"id": new_id("prj_"), "name": name, "slug": s, "public": public, "settings": {}, "created_at": now()}
            c.execute(insert(db.projects).values(**row))
        return row

    def get_project(self, project_id: str) -> Optional[Dict[str, Any]]:
        with self.engine.connect() as c:
            r = c.execute(select(db.projects).where(db.projects.c.id == project_id)).first()
        return _row(r) if r else None

    def get_project_by_slug(self, slug: str) -> Optional[Dict[str, Any]]:
        with self.engine.connect() as c:
            r = c.execute(select(db.projects).where(db.projects.c.slug == slug)).first()
        return _row(r) if r else None

    def list_projects(self) -> List[Dict[str, Any]]:
        with self.engine.connect() as c:
            return [_row(r) for r in c.execute(select(db.projects).order_by(db.projects.c.created_at))]

    def create_api_key(self, project_id: str, name: str, key_hash: str, display: str) -> Dict[str, Any]:
        row = {"id": new_id("key_"), "project_id": project_id, "name": name, "display": display, "key_hash": key_hash, "created_at": now(), "revoked": False}
        with self._w(), self.engine.begin() as c:
            c.execute(insert(db.api_keys).values(**row))
        return {k: v for k, v in row.items() if k != "key_hash"}

    def list_api_keys(self, project_id: str) -> List[Dict[str, Any]]:
        t = db.api_keys
        with self.engine.connect() as c:
            rows = c.execute(select(t.c.id, t.c.name, t.c.display, t.c.created_at, t.c.last_used_at, t.c.revoked)
                             .where(t.c.project_id == project_id).order_by(t.c.created_at))
            return [_row(r) for r in rows]

    def revoke_api_key(self, project_id: str, key_id: str) -> bool:
        t = db.api_keys
        with self._w(), self.engine.begin() as c:
            res = c.execute(update(t).where(and_(t.c.project_id == project_id, t.c.id == key_id)).values(revoked=True))
            return res.rowcount > 0

    def get_project_by_key_hash(self, key_hash: str) -> Optional[Dict[str, Any]]:
        k = db.api_keys
        with self.engine.connect() as c:
            r = c.execute(select(db.projects, k.c.id.label("key_id")).join(k, k.c.project_id == db.projects.c.id)
                          .where(and_(k.c.key_hash == key_hash, k.c.revoked == False))).first()  # noqa: E712
        return _row(r) if r else None

    # ------------------------------------------------------------------ ingestion
    def upsert_traces(self, project_id: str, items: List[Dict[str, Any]]) -> None:
        if not items:
            return
        t = db.traces
        merged: Dict[str, Dict[str, Any]] = {}
        for it in items:  # collapse duplicates inside the batch
            cur = merged.get(it["id"])
            merged[it["id"]] = _merge_trace(cur, it) if cur else dict(it)
        with self._w(), self.engine.begin() as c:
            ids = list(merged)
            existing = {}
            for ch in _chunks(ids):
                for r in c.execute(select(t).where(and_(t.c.project_id == project_id, t.c.id.in_(ch)))):
                    existing[r.id] = _row(r)
            ts = now()
            for tid, it in merged.items():
                if tid in existing:
                    m = _merge_trace(existing[tid], it)
                    vals = {k: m.get(k) for k in TRACE_FIELDS + ("tags", "metadata")}
                    vals["tags_text"] = pipe_join(m.get("tags") or [])
                    vals["updated_at"] = ts
                    c.execute(update(t).where(and_(t.c.project_id == project_id, t.c.id == tid)).values(**vals))
                else:
                    vals = {k: it.get(k) for k in TRACE_FIELDS}
                    vals.update(project_id=project_id, id=tid, tags=it.get("tags") or [], metadata=it.get("metadata") or {},
                                tags_text=pipe_join(it.get("tags") or []), updated_at=ts, observation_count=0, error_count=0,
                                total_cost=0.0, total_tokens=0, prompt_tokens=0, completion_tokens=0, status="ok", models=[], models_text="")
                    c.execute(insert(t).values(**vals))

    def upsert_observations(self, project_id: str, items: List[Dict[str, Any]]) -> None:
        if not items:
            return
        o = db.observations
        merged: Dict[str, Dict[str, Any]] = {}
        for it in items:
            cur = merged.get(it["id"])
            merged[it["id"]] = _merge_simple(cur, it, OBS_FIELDS) if cur else dict(it)
        with self._w(), self.engine.begin() as c:
            existing = {}
            for ch in _chunks(list(merged)):
                for r in c.execute(select(o).where(and_(o.c.project_id == project_id, o.c.id.in_(ch)))):
                    existing[r.id] = _row(r)
            for oid, it in merged.items():
                m = _merge_simple(existing.get(oid), it, OBS_FIELDS)
                vals = {k: m.get(k) for k in OBS_FIELDS + ("metadata",)}
                vals["latency_ms"] = (m["end_time"] - m["start_time"]) * 1000 if m.get("end_time") and m.get("start_time") else None
                if oid in existing:
                    c.execute(update(o).where(and_(o.c.project_id == project_id, o.c.id == oid)).values(**vals))
                else:
                    c.execute(insert(o).values(project_id=project_id, id=oid, **vals))

    def insert_scores(self, project_id: str, items: List[Dict[str, Any]]) -> None:
        if not items:
            return
        s = db.scores
        with self._w(), self.engine.begin() as c:
            for it in items:
                row = {k: it.get(k) for k in ("trace_id", "observation_id", "name", "value", "string_value", "comment", "source", "evaluator", "rule_id", "metadata")}
                row.update(project_id=project_id, id=it.get("id") or new_id("sc_"), created_at=it.get("created_at") or now())
                try:
                    with c.begin_nested():
                        c.execute(insert(s).values(**row))
                except IntegrityError:
                    c.execute(update(s).where(and_(s.c.project_id == project_id, s.c.id == row["id"])).values(**row))

    def refresh_trace_aggregates(self, project_id: str, trace_ids: Sequence[str]) -> None:
        if not trace_ids:
            return
        o, t = db.observations, db.traces
        cols = (o.c.trace_id, o.c.id, o.c.parent_id, o.c.name, o.c.type, o.c.model, o.c.cost, o.c.prompt_tokens, o.c.completion_tokens,
                o.c.total_tokens, o.c.level, o.c.start_time, o.c.end_time, o.c.input, o.c.output)
        with self._w(), self.engine.begin() as c:
            for ch in _chunks(list(set(trace_ids)), 200):
                obs_by: Dict[str, List[Dict[str, Any]]] = {}
                for r in c.execute(select(*cols).where(and_(o.c.project_id == project_id, o.c.trace_id.in_(ch)))):
                    obs_by.setdefault(r.trace_id, []).append(_row(r))
                traces = {r.id: _row(r) for r in c.execute(select(t).where(and_(t.c.project_id == project_id, t.c.id.in_(ch))))}
                for tid in ch:
                    obs = obs_by.get(tid, [])
                    tr = traces.get(tid)
                    agg = _aggregate(tr, obs)
                    if tr is None:
                        if not obs:
                            continue
                        c.execute(insert(t).values(project_id=project_id, id=tid, tags=[], tags_text="", metadata={}, **agg))
                    else:
                        c.execute(update(t).where(and_(t.c.project_id == project_id, t.c.id == tid)).values(**agg))

    # ------------------------------------------------------------------ queries
    def _trace_where(self, project_id: str, f: TraceFilter):
        t = db.traces
        conds = [t.c.project_id == project_id]
        if f.q:
            q = f"%{f.q.lower()}%"
            conds.append(or_(t.c.search_text.like(q), t.c.id.like(f"{f.q}%")))
        if f.name: conds.append(t.c.name == f.name)
        if f.user_id: conds.append(t.c.user_id == f.user_id)
        if f.session_id: conds.append(t.c.session_id == f.session_id)
        if f.tag: conds.append(t.c.tags_text.like(f"%|{f.tag}|%"))
        if f.model: conds.append(t.c.models_text.like(f"%|{f.model}|%"))
        if f.status: conds.append(t.c.status == f.status)
        if f.release: conds.append(t.c.release == f.release)
        if f.environment: conds.append(t.c.environment == f.environment)
        if f.start is not None: conds.append(t.c.start_time >= f.start)
        if f.end is not None: conds.append(t.c.start_time <= f.end)
        if f.min_latency_ms is not None: conds.append(t.c.latency_ms >= f.min_latency_ms)
        if f.min_cost is not None: conds.append(t.c.total_cost >= f.min_cost)
        if f.ids: conds.append(t.c.id.in_(f.ids))
        return and_(*conds)

    def list_traces(self, project_id: str, f: TraceFilter, limit: int = 50, offset: int = 0) -> Tuple[List[Dict[str, Any]], int]:
        t = db.traces
        where = self._trace_where(project_id, f)
        order_col = {"start_time": t.c.start_time, "latency": t.c.latency_ms, "latency_ms": t.c.latency_ms, "cost": t.c.total_cost,
                     "total_cost": t.c.total_cost, "tokens": t.c.total_tokens, "total_tokens": t.c.total_tokens}.get(f.order_by, t.c.start_time)
        cols = [c for c in t.c if c.name not in ("search_text", "tags_text", "models_text")]
        with self.engine.connect() as c:
            total = c.execute(select(func.count()).select_from(t).where(where)).scalar() or 0
            rows = c.execute(select(*cols).where(where).order_by(order_col.desc() if f.desc else order_col.asc(), t.c.id).limit(limit).offset(offset))
            items = [_row(r) for r in rows]
            if items:
                sc = db.scores
                scores: Dict[str, List[Dict[str, Any]]] = {}
                for r in c.execute(select(sc.c.trace_id, sc.c.name, sc.c.value, sc.c.string_value)
                                   .where(and_(sc.c.project_id == project_id, sc.c.trace_id.in_([i["id"] for i in items])))):
                    scores.setdefault(r.trace_id, []).append({"name": r.name, "value": r.value, "string_value": r.string_value})
                for i in items:
                    i["scores"] = scores.get(i["id"], [])
        return items, total

    def get_trace(self, project_id: str, trace_id: str) -> Optional[Dict[str, Any]]:
        t, o, s = db.traces, db.observations, db.scores
        with self.engine.connect() as c:
            r = c.execute(select(t).where(and_(t.c.project_id == project_id, t.c.id == trace_id))).first()
            if not r:
                return None
            tr = _row(r)
            for k in ("search_text", "tags_text", "models_text"):
                tr.pop(k, None)
            tr["observations"] = [_row(x) for x in c.execute(select(o).where(and_(o.c.project_id == project_id, o.c.trace_id == trace_id)).order_by(o.c.start_time))]
            tr["scores"] = [_row(x) for x in c.execute(select(s).where(and_(s.c.project_id == project_id, s.c.trace_id == trace_id)).order_by(s.c.created_at))]
        return tr

    def delete_trace(self, project_id: str, trace_id: str) -> bool:
        with self._w(), self.engine.begin() as c:
            for tbl in (db.observations, db.scores):
                c.execute(delete(tbl).where(and_(tbl.c.project_id == project_id, tbl.c.trace_id == trace_id)))
            res = c.execute(delete(db.traces).where(and_(db.traces.c.project_id == project_id, db.traces.c.id == trace_id)))
            return res.rowcount > 0

    def list_observations(self, project_id: str, f: ObservationFilter, limit: int = 100, offset: int = 0):
        o = db.observations
        conds = [o.c.project_id == project_id]
        if f.trace_id: conds.append(o.c.trace_id == f.trace_id)
        if f.type: conds.append(o.c.type == f.type)
        if f.model: conds.append(o.c.model == f.model)
        if f.name: conds.append(o.c.name == f.name)
        if f.level: conds.append(o.c.level == f.level)
        if f.start is not None: conds.append(o.c.start_time >= f.start)
        if f.end is not None: conds.append(o.c.start_time <= f.end)
        w = and_(*conds)
        with self.engine.connect() as c:
            total = c.execute(select(func.count()).select_from(o).where(w)).scalar() or 0
            rows = [_row(r) for r in c.execute(select(o).where(w).order_by(o.c.start_time.desc()).limit(limit).offset(offset))]
        return rows, total

    def list_scores(self, project_id: str, f: ScoreFilter, limit: int = 100, offset: int = 0):
        s = db.scores
        conds = [s.c.project_id == project_id]
        if f.trace_id: conds.append(s.c.trace_id == f.trace_id)
        if f.name: conds.append(s.c.name == f.name)
        if f.source: conds.append(s.c.source == f.source)
        if f.start is not None: conds.append(s.c.created_at >= f.start)
        if f.end is not None: conds.append(s.c.created_at <= f.end)
        w = and_(*conds)
        with self.engine.connect() as c:
            total = c.execute(select(func.count()).select_from(s).where(w)).scalar() or 0
            rows = [_row(r) for r in c.execute(select(s).where(w).order_by(s.c.created_at.desc()).limit(limit).offset(offset))]
        return rows, total

    def list_sessions(self, project_id: str, limit: int = 50, offset: int = 0):
        t = db.traces
        w = and_(t.c.project_id == project_id, t.c.session_id.isnot(None))
        q = (select(t.c.session_id, func.count().label("traces"), func.min(t.c.start_time).label("start_time"), func.max(t.c.end_time).label("end_time"),
                    func.sum(t.c.total_cost).label("total_cost"), func.sum(t.c.total_tokens).label("total_tokens"), func.max(t.c.user_id).label("user_id"),
                    func.sum(t.c.error_count).label("errors"))
             .where(w).group_by(t.c.session_id))
        with self.engine.connect() as c:
            total = c.execute(select(func.count(func.distinct(t.c.session_id))).where(w)).scalar() or 0
            rows = [_row(r) for r in c.execute(q.order_by(func.max(t.c.start_time).desc()).limit(limit).offset(offset))]
        return rows, total

    def metric_rows(self, project_id: str, start: float, end: float):
        t, o, s = db.traces, db.observations, db.scores
        with self.engine.connect() as c:
            trows = [_row(r) for r in c.execute(select(t.c.name, t.c.user_id, t.c.tags, t.c.start_time, t.c.latency_ms, t.c.total_cost, t.c.total_tokens, t.c.status)
                                               .where(and_(t.c.project_id == project_id, t.c.start_time >= start, t.c.start_time <= end)))]
            grows = [_row(r) for r in c.execute(select(o.c.model, o.c.start_time, o.c.completion_start_time, o.c.latency_ms, o.c.prompt_tokens, o.c.completion_tokens, o.c.cost, o.c.level)
                                               .where(and_(o.c.project_id == project_id, o.c.type == "generation", o.c.start_time >= start, o.c.start_time <= end)))]
            srows = [_row(r) for r in c.execute(select(s.c.name, s.c.value, s.c.created_at)
                                               .where(and_(s.c.project_id == project_id, s.c.created_at >= start, s.c.created_at <= end)))]
        return trows, grows, srows

    # ------------------------------------------------------------------ eval rules
    def list_eval_rules(self, project_id: str):
        r = db.eval_rules
        with self.engine.connect() as c:
            return [_row(x) for x in c.execute(select(r).where(r.c.project_id == project_id).order_by(r.c.created_at))]

    def create_eval_rule(self, project_id: str, rule: Dict[str, Any]):
        row = {"id": new_id("rule_"), "project_id": project_id, "name": rule.get("name") or rule["evaluator"], "evaluator": rule["evaluator"],
               "config": rule.get("config") or {}, "filter": rule.get("filter") or {}, "sample_rate": float(rule.get("sample_rate", 1.0)),
               "enabled": bool(rule.get("enabled", True)), "created_at": now()}
        with self._w(), self.engine.begin() as c:
            c.execute(insert(db.eval_rules).values(**row))
        return row

    def delete_eval_rule(self, project_id: str, rule_id: str) -> bool:
        r = db.eval_rules
        with self._w(), self.engine.begin() as c:
            return c.execute(delete(r).where(and_(r.c.project_id == project_id, r.c.id == rule_id))).rowcount > 0

    def has_score(self, project_id: str, trace_id: str, name: str, rule_id: Optional[str] = None) -> bool:
        s = db.scores
        conds = [s.c.project_id == project_id, s.c.trace_id == trace_id, s.c.name == name]
        if rule_id:
            conds.append(s.c.rule_id == rule_id)
        with self.engine.connect() as c:
            return c.execute(select(s.c.id).where(and_(*conds)).limit(1)).first() is not None

    # ------------------------------------------------------------------ datasets
    def create_dataset(self, project_id: str, name: str, description: str = ""):
        existing = self.get_dataset(project_id, name=name)
        if existing:
            return existing
        row = {"id": new_id("ds_"), "project_id": project_id, "name": name, "description": description, "created_at": now()}
        with self._w(), self.engine.begin() as c:
            c.execute(insert(db.datasets).values(**row))
        return row

    def get_dataset(self, project_id: str, dataset_id: Optional[str] = None, name: Optional[str] = None):
        d = db.datasets
        cond = d.c.id == dataset_id if dataset_id else d.c.name == name
        with self.engine.connect() as c:
            r = c.execute(select(d).where(and_(d.c.project_id == project_id, cond))).first()
        return _row(r) if r else None

    def list_datasets(self, project_id: str):
        d, i, ri = db.datasets, db.dataset_items, db.dataset_run_items
        with self.engine.connect() as c:
            rows = [_row(r) for r in c.execute(select(d).where(d.c.project_id == project_id).order_by(d.c.created_at.desc()))]
            for r in rows:
                r["item_count"] = c.execute(select(func.count()).select_from(i).where(i.c.dataset_id == r["id"])).scalar()
                r["run_count"] = c.execute(select(func.count(func.distinct(ri.c.run_name))).where(ri.c.dataset_id == r["id"])).scalar()
        return rows

    def add_dataset_items(self, project_id: str, dataset_id: str, items: List[Dict[str, Any]]):
        rows = [{"id": it.get("id") or new_id("item_"), "project_id": project_id, "dataset_id": dataset_id, "input": it.get("input"),
                 "expected_output": it.get("expected_output"), "metadata": it.get("metadata") or {}, "created_at": now()} for it in items]
        if rows:
            with self._w(), self.engine.begin() as c:
                c.execute(insert(db.dataset_items), rows)
        return rows

    def list_dataset_items(self, project_id: str, dataset_id: str):
        i = db.dataset_items
        with self.engine.connect() as c:
            return [_row(r) for r in c.execute(select(i).where(and_(i.c.project_id == project_id, i.c.dataset_id == dataset_id)).order_by(i.c.created_at, i.c.id))]

    def add_run_items(self, project_id: str, dataset_id: str, run_name: str, links: List[Dict[str, Any]], run_metadata: Dict[str, Any]):
        rows = [{"id": new_id("ri_"), "project_id": project_id, "dataset_id": dataset_id, "run_name": run_name, "item_id": l["item_id"],
                 "trace_id": l["trace_id"], "run_metadata": run_metadata, "created_at": now()} for l in links]
        if rows:
            with self._w(), self.engine.begin() as c:
                c.execute(insert(db.dataset_run_items), rows)

    def list_run_items(self, project_id: str, dataset_id: str):
        ri = db.dataset_run_items
        with self.engine.connect() as c:
            return [_row(r) for r in c.execute(select(ri).where(and_(ri.c.project_id == project_id, ri.c.dataset_id == dataset_id)).order_by(ri.c.created_at))]

    # ------------------------------------------------------------------ alerts
    def list_alert_rules(self, project_id: Optional[str] = None):
        a = db.alert_rules
        q = select(a)
        if project_id:
            q = q.where(a.c.project_id == project_id)
        with self.engine.connect() as c:
            return [_row(r) for r in c.execute(q.order_by(a.c.created_at))]

    def create_alert_rule(self, project_id: str, rule: Dict[str, Any]):
        row = {"id": new_id("alert_"), "project_id": project_id, "name": rule.get("name") or rule["metric"], "metric": rule["metric"],
               "comparator": rule.get("comparator", "gt"), "threshold": float(rule["threshold"]), "window_minutes": int(rule.get("window_minutes", 60)),
               "cooldown_minutes": int(rule.get("cooldown_minutes", 60)), "webhook_url": rule.get("webhook_url"), "filter": rule.get("filter") or {},
               "enabled": bool(rule.get("enabled", True)), "created_at": now()}
        with self._w(), self.engine.begin() as c:
            c.execute(insert(db.alert_rules).values(**row))
        return row

    def update_alert_rule(self, project_id: str, rule_id: str, fields: Dict[str, Any]) -> None:
        a = db.alert_rules
        with self._w(), self.engine.begin() as c:
            c.execute(update(a).where(and_(a.c.project_id == project_id, a.c.id == rule_id)).values(**fields))

    def delete_alert_rule(self, project_id: str, rule_id: str) -> bool:
        a = db.alert_rules
        with self._w(), self.engine.begin() as c:
            return c.execute(delete(a).where(and_(a.c.project_id == project_id, a.c.id == rule_id))).rowcount > 0

    def add_alert_event(self, project_id: str, ev: Dict[str, Any]):
        row = {"id": new_id("aev_"), "project_id": project_id, "created_at": now(), **ev}
        with self._w(), self.engine.begin() as c:
            c.execute(insert(db.alert_events).values(**row))
        return row

    def list_alert_events(self, project_id: str, limit: int = 100):
        e = db.alert_events
        with self.engine.connect() as c:
            return [_row(r) for r in c.execute(select(e).where(e.c.project_id == project_id).order_by(e.c.created_at.desc()).limit(limit))]


class _NullCtx:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _merge_simple(cur: Optional[Dict[str, Any]], new: Dict[str, Any], fields: Sequence[str]) -> Dict[str, Any]:
    if not cur:
        return dict(new)
    out = dict(cur)
    for k in fields:
        if new.get(k) is not None:
            out[k] = new[k]
    md = dict(cur.get("metadata") or {})
    md.update(new.get("metadata") or {})
    out["metadata"] = md
    return out


def _merge_trace(cur: Dict[str, Any], new: Dict[str, Any]) -> Dict[str, Any]:
    out = _merge_simple(cur, new, TRACE_FIELDS)
    out["tags"] = list(dict.fromkeys(list(cur.get("tags") or []) + list(new.get("tags") or [])))
    out["id"] = cur.get("id") or new.get("id")
    # keep the earliest start
    if cur.get("start_time") and new.get("start_time"):
        out["start_time"] = min(cur["start_time"], new["start_time"])
    return out


def _aggregate(tr: Optional[Dict[str, Any]], obs: List[Dict[str, Any]]) -> Dict[str, Any]:
    tr = tr or {}
    models = sorted({o["model"] for o in obs if o.get("model")})
    errors = sum(1 for o in obs if o.get("level") == "ERROR")
    starts = [o["start_time"] for o in obs if o.get("start_time")]
    ends = [o["end_time"] for o in obs if o.get("end_time")]
    ids = {o["id"] for o in obs}
    roots = sorted([o for o in obs if not o.get("parent_id") or o["parent_id"] not in ids], key=lambda o: o.get("start_time") or 0)
    root = roots[0] if roots else None
    start = tr.get("start_time") or (min(starts) if starts else None)
    end = tr.get("end_time") or (max(ends) if ends else None)
    if start and starts:
        start = min(start, min(starts))
    if end and ends:
        end = max(end, max(ends))
    name = tr.get("name") or (root.get("name") if root else None)
    inp = tr.get("input") if tr.get("input") is not None else (root.get("input") if root else None)
    out = tr.get("output") if tr.get("output") is not None else (root.get("output") if root else None)
    md = tr.get("metadata") or {}
    status = "error" if errors or md.get("error") else "ok"
    cost = sum(o.get("cost") or 0.0 for o in obs)
    pt = sum(o.get("prompt_tokens") or 0 for o in obs)
    ct = sum(o.get("completion_tokens") or 0 for o in obs)
    tt = sum((o.get("total_tokens") or ((o.get("prompt_tokens") or 0) + (o.get("completion_tokens") or 0))) for o in obs)
    search = " ".join(filter(None, [name, tr.get("user_id"), tr.get("session_id"), " ".join(models), text_of(inp, 1500), text_of(out, 1500)])).lower()
    return {
        "name": name, "input": inp, "output": out, "start_time": start, "end_time": end,
        "latency_ms": (end - start) * 1000 if start and end else None, "total_cost": round(cost, 10), "prompt_tokens": pt,
        "completion_tokens": ct, "total_tokens": tt, "observation_count": len(obs), "error_count": errors, "status": status,
        "models": models, "models_text": pipe_join(models), "search_text": search, "updated_at": now(),
    }
