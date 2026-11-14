from __future__ import annotations

import importlib

from .base import ObservationFilter, ScoreFilter, Storage, TraceFilter, compute_metrics

__all__ = ["Storage", "TraceFilter", "ObservationFilter", "ScoreFilter", "compute_metrics", "create_storage"]


def create_storage(url: str, backend: str = "") -> Storage:
    """Instantiate storage. ``backend`` may be ``"module.path:ClassName"`` for a custom implementation."""
    if backend:
        mod, _, cls = backend.partition(":")
        return getattr(importlib.import_module(mod), cls)(url)
    from .sql import SQLStorage
    return SQLStorage(url)
