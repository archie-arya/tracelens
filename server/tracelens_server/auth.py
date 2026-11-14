from __future__ import annotations

import base64
import hashlib
import secrets
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any, Deque, Dict, Optional

from fastapi import HTTPException, Request

from .config import settings


def generate_key() -> str:
    return "tl-sk-" + secrets.token_urlsafe(30)


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def display_key(key: str) -> str:
    return key[:10] + "..." + key[-4:]


def extract_key(request: Request) -> Optional[str]:
    auth = request.headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip() or None
    if auth.lower().startswith("basic "):  # Langfuse-style "public:secret" -> the secret part is the key
        try:
            return base64.b64decode(auth[6:]).decode().split(":", 1)[-1] or None
        except Exception:
            return None
    return request.headers.get("x-api-key") or request.headers.get("x-tracelens-key") or None


@dataclass
class Ctx:
    project: Dict[str, Any]
    read_only: bool

    @property
    def project_id(self) -> str:
        return self.project["id"]


def resolve(request: Request, write: bool) -> Ctx:
    storage = request.app.state.storage
    key = extract_key(request)
    if key:
        proj = storage.get_project_by_key_hash(hash_key(key))
        if not proj:
            raise HTTPException(401, "invalid API key")
        return Ctx(proj, False)
    if not write and settings.public_demo and getattr(request.app.state, "demo_project", None):
        return Ctx(request.app.state.demo_project, True)
    raise HTTPException(401, "missing API key (Authorization: Bearer tl-sk-...)")


def read_ctx(request: Request) -> Ctx:
    return resolve(request, write=False)


def write_ctx(request: Request) -> Ctx:
    return resolve(request, write=True)


class RateLimiter:
    def __init__(self, limit: int, window: float):
        self.limit, self.window = limit, window
        self.hits: Dict[str, Deque[float]] = defaultdict(deque)

    def check(self, key: str) -> None:
        q = self.hits[key]
        t = time.time()
        while q and t - q[0] > self.window:
            q.popleft()
        if len(q) >= self.limit:
            raise HTTPException(429, "rate limit exceeded, try again later")
        q.append(t)


signup_limiter = RateLimiter(10, 3600)
# LLM-judge calls are rate limited per project so a public instance can't exhaust the judge key's quota
import os as _os
llm_eval_limiter = RateLimiter(int(_os.environ.get("TRACELENS_LLM_EVALS_PER_HOUR", "300")), 3600)
