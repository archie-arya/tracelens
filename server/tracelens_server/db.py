"""Relational schema (SQLite / Postgres / any SQLAlchemy dialect). Times are epoch seconds (float)."""
from __future__ import annotations

from sqlalchemy import JSON, Boolean, Column, Float, Index, Integer, MetaData, String, Table, Text

metadata = MetaData()

projects = Table(
    "projects", metadata,
    Column("id", String(64), primary_key=True),
    Column("name", String(200), nullable=False),
    Column("slug", String(200), unique=True, nullable=False),
    Column("public", Boolean, default=False, nullable=False),
    Column("settings", JSON),
    Column("created_at", Float, nullable=False),
)

api_keys = Table(
    "api_keys", metadata,
    Column("id", String(64), primary_key=True),
    Column("project_id", String(64), nullable=False, index=True),
    Column("name", String(200)),
    Column("display", String(64)),
    Column("key_hash", String(128), unique=True, nullable=False),
    Column("created_at", Float, nullable=False),
    Column("last_used_at", Float),
    Column("revoked", Boolean, default=False, nullable=False),
)

traces = Table(
    "traces", metadata,
    Column("project_id", String(64), primary_key=True),
    Column("id", String(128), primary_key=True),
    Column("name", String(500)),
    Column("user_id", String(256)),
    Column("session_id", String(256)),
    Column("tags", JSON),
    Column("tags_text", Text),
    Column("metadata", JSON),
    Column("input", JSON),
    Column("output", JSON),
    Column("release", String(200)),
    Column("environment", String(100)),
    Column("start_time", Float),
    Column("end_time", Float),
    Column("latency_ms", Float),
    Column("total_cost", Float),
    Column("prompt_tokens", Integer),
    Column("completion_tokens", Integer),
    Column("total_tokens", Integer),
    Column("observation_count", Integer),
    Column("error_count", Integer),
    Column("status", String(16)),
    Column("models", JSON),
    Column("models_text", Text),
    Column("search_text", Text),
    Column("sdk", String(50)),
    Column("updated_at", Float),
)
Index("ix_traces_project_start", traces.c.project_id, traces.c.start_time)
Index("ix_traces_project_user", traces.c.project_id, traces.c.user_id)
Index("ix_traces_project_session", traces.c.project_id, traces.c.session_id)

observations = Table(
    "observations", metadata,
    Column("project_id", String(64), primary_key=True),
    Column("id", String(128), primary_key=True),
    Column("trace_id", String(128), nullable=False),
    Column("parent_id", String(128)),
    Column("type", String(32)),
    Column("name", String(500)),
    Column("input", JSON),
    Column("output", JSON),
    Column("metadata", JSON),
    Column("model", String(200)),
    Column("model_parameters", JSON),
    Column("prompt_tokens", Integer),
    Column("completion_tokens", Integer),
    Column("total_tokens", Integer),
    Column("cost", Float),
    Column("level", String(16)),
    Column("status_message", Text),
    Column("start_time", Float),
    Column("end_time", Float),
    Column("completion_start_time", Float),
    Column("latency_ms", Float),
)
Index("ix_obs_project_trace", observations.c.project_id, observations.c.trace_id)
Index("ix_obs_project_start", observations.c.project_id, observations.c.start_time)

scores = Table(
    "scores", metadata,
    Column("project_id", String(64), primary_key=True),
    Column("id", String(128), primary_key=True),
    Column("trace_id", String(128), nullable=False),
    Column("observation_id", String(128)),
    Column("name", String(200), nullable=False),
    Column("value", Float),
    Column("string_value", String(500)),
    Column("comment", Text),
    Column("source", String(32)),
    Column("evaluator", String(100)),
    Column("rule_id", String(64)),
    Column("metadata", JSON),
    Column("created_at", Float, nullable=False),
)
Index("ix_scores_project_trace", scores.c.project_id, scores.c.trace_id)
Index("ix_scores_project_created", scores.c.project_id, scores.c.created_at)

eval_rules = Table(
    "eval_rules", metadata,
    Column("id", String(64), primary_key=True),
    Column("project_id", String(64), nullable=False, index=True),
    Column("name", String(200)),
    Column("evaluator", String(100), nullable=False),
    Column("config", JSON),
    Column("filter", JSON),
    Column("sample_rate", Float, default=1.0),
    Column("enabled", Boolean, default=True),
    Column("created_at", Float),
)

datasets = Table(
    "datasets", metadata,
    Column("id", String(64), primary_key=True),
    Column("project_id", String(64), nullable=False, index=True),
    Column("name", String(200), nullable=False),
    Column("description", Text),
    Column("created_at", Float),
)

dataset_items = Table(
    "dataset_items", metadata,
    Column("id", String(64), primary_key=True),
    Column("project_id", String(64), nullable=False),
    Column("dataset_id", String(64), nullable=False, index=True),
    Column("input", JSON),
    Column("expected_output", JSON),
    Column("metadata", JSON),
    Column("created_at", Float),
)

dataset_run_items = Table(
    "dataset_run_items", metadata,
    Column("id", String(64), primary_key=True),
    Column("project_id", String(64), nullable=False),
    Column("dataset_id", String(64), nullable=False, index=True),
    Column("run_name", String(200), nullable=False),
    Column("item_id", String(64), nullable=False),
    Column("trace_id", String(128), nullable=False),
    Column("run_metadata", JSON),
    Column("created_at", Float),
)

alert_rules = Table(
    "alert_rules", metadata,
    Column("id", String(64), primary_key=True),
    Column("project_id", String(64), nullable=False, index=True),
    Column("name", String(200)),
    Column("metric", String(100), nullable=False),
    Column("comparator", String(4), nullable=False),
    Column("threshold", Float, nullable=False),
    Column("window_minutes", Integer, default=60),
    Column("cooldown_minutes", Integer, default=60),
    Column("webhook_url", Text),
    Column("filter", JSON),
    Column("enabled", Boolean, default=True),
    Column("last_triggered_at", Float),
    Column("last_value", Float),
    Column("created_at", Float),
)

alert_events = Table(
    "alert_events", metadata,
    Column("id", String(64), primary_key=True),
    Column("project_id", String(64), nullable=False, index=True),
    Column("rule_id", String(64), nullable=False),
    Column("value", Float),
    Column("message", Text),
    Column("delivered", Boolean),
    Column("response", Text),
    Column("created_at", Float),
)
