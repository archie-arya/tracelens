"""TraceLens server entrypoint: ``uvicorn tracelens_server.main:app``."""
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse

from . import __version__
from .alerts import alert_loop
from .api import router
from .auth import display_key, generate_key, hash_key
from .config import settings
from .evals import load_plugins
from .storage import Storage, create_storage

logger = logging.getLogger("tracelens")
logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))


def _ensure_key(storage: Storage, project_id: str, key: str, name: str) -> None:
    if not storage.get_project_by_key_hash(hash_key(key)):
        storage.create_api_key(project_id, name, hash_key(key), display_key(key))


def bootstrap(storage: Storage) -> dict:
    storage.init()
    demo = storage.get_project_by_slug(settings.demo_project) if settings.demo_project else None
    if settings.demo_project and not demo:
        demo = storage.create_project("Demo", slug=settings.demo_project, public=True)
    if demo and settings.demo_key:
        _ensure_key(storage, demo["id"], settings.demo_key, "demo-seed")
    default = storage.get_project_by_slug("default")
    if not default:
        default = storage.create_project("Default", slug="default")
        key = settings.init_api_key or generate_key()
        _ensure_key(storage, default["id"], key, "initial")
        if not settings.init_api_key:
            logger.warning("=" * 70)
            logger.warning("TraceLens: created project 'default'. Initial API key (shown once): %s", key)
            logger.warning("=" * 70)
    elif settings.init_api_key:
        _ensure_key(storage, default["id"], settings.init_api_key, "initial")
    return demo or {}


def create_app(storage: Storage | None = None, start_background: bool = True) -> FastAPI:
    load_plugins(settings.plugins)
    storage = storage or create_storage(settings.database_url, settings.storage_backend)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.demo_project = await asyncio.to_thread(bootstrap, storage)
        task = asyncio.create_task(alert_loop(storage, settings.alert_interval_seconds)) if start_background else None
        yield
        if task:
            task.cancel()

    app = FastAPI(title="TraceLens", version=__version__, description="Open-source LLM observability: tracing, metrics, evals & alerts.", lifespan=lifespan)
    app.state.storage = storage
    app.state.demo_project = None
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["*"], allow_headers=["*"], allow_credentials=False)
    app.add_middleware(GZipMiddleware, minimum_size=1000)
    max_body = int(os.environ.get("TRACELENS_MAX_BODY_MB", "20")) * 1024 * 1024

    @app.middleware("http")
    async def limit_body(request: Request, call_next):
        cl = request.headers.get("content-length")
        if cl and cl.isdigit() and int(cl) > max_body:
            return JSONResponse({"detail": "payload too large"}, status_code=413)
        return await call_next(request)

    app.include_router(router)

    static = settings.static_dir
    if os.path.isdir(static):
        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str, request: Request):
            if path.startswith(("api/", "v1/")):
                return JSONResponse({"detail": "Not Found"}, status_code=404)
            fp = os.path.normpath(os.path.join(static, path))
            if path and fp.startswith(os.path.abspath(static)) and os.path.isfile(fp):
                headers = {"Cache-Control": "public, max-age=31536000, immutable"} if "/assets/" in fp else {}
                return FileResponse(fp, headers=headers)
            return FileResponse(os.path.join(static, "index.html"), headers={"Cache-Control": "no-cache"})
    return app


app = create_app()
