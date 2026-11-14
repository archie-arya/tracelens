"""Server configuration - everything is an environment variable (12-factor)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List


def _bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    return default if v is None else v.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Settings:
    database_url: str = field(default_factory=lambda: os.environ.get("TRACELENS_DATABASE_URL") or os.environ.get("DATABASE_URL") or "sqlite:///./data/tracelens.db")
    storage_backend: str = field(default_factory=lambda: os.environ.get("TRACELENS_STORAGE", ""))  # "module:Class" to plug a custom backend
    allow_signup: bool = field(default_factory=lambda: _bool("TRACELENS_ALLOW_SIGNUP", True))
    demo_project: str = field(default_factory=lambda: os.environ.get("TRACELENS_DEMO_PROJECT", "demo"))  # slug served read-only to anonymous users
    public_demo: bool = field(default_factory=lambda: _bool("TRACELENS_PUBLIC_DEMO", True))
    admin_token: str = field(default_factory=lambda: os.environ.get("TRACELENS_ADMIN_TOKEN", ""))
    demo_key: str = field(default_factory=lambda: os.environ.get("TRACELENS_DEMO_KEY", ""))  # write key for the demo project (seeding)
    init_api_key: str = field(default_factory=lambda: os.environ.get("TRACELENS_INIT_API_KEY", ""))  # known key for the "default" project
    public_url: str = field(default_factory=lambda: os.environ.get("TRACELENS_PUBLIC_URL", ""))
    cors_origins: List[str] = field(default_factory=lambda: [o.strip() for o in os.environ.get("TRACELENS_CORS_ORIGINS", "*").split(",") if o.strip()])
    plugins: List[str] = field(default_factory=lambda: [p.strip() for p in os.environ.get("TRACELENS_PLUGINS", "").split(",") if p.strip()])
    # LLM-as-judge: comma separated "provider:model" chain with automatic fallback
    judge_models: List[str] = field(default_factory=lambda: [m.strip() for m in os.environ.get(
        "TRACELENS_JUDGE_MODELS", "gemini:gemini-flash-lite-latest,gemini:gemini-3.1-flash-lite,gemini:gemini-3-flash-preview,gemini:gemini-flash-latest").split(",") if m.strip()])
    gemini_api_key: str = field(default_factory=lambda: os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY", ""))
    openai_api_key: str = field(default_factory=lambda: os.environ.get("OPENAI_API_KEY", ""))
    openai_base_url: str = field(default_factory=lambda: os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"))
    groq_api_key: str = field(default_factory=lambda: os.environ.get("GROQ_API_KEY", ""))
    ollama_base_url: str = field(default_factory=lambda: os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1"))
    alert_interval_seconds: int = field(default_factory=lambda: int(os.environ.get("TRACELENS_ALERT_INTERVAL", "60")))
    max_eval_concurrency: int = field(default_factory=lambda: int(os.environ.get("TRACELENS_EVAL_CONCURRENCY", "4")))
    static_dir: str = field(default_factory=lambda: os.environ.get("TRACELENS_STATIC_DIR", os.path.join(os.path.dirname(__file__), "static")))
    max_batch: int = field(default_factory=lambda: int(os.environ.get("TRACELENS_MAX_BATCH", "2000")))


settings = Settings()
