from __future__ import annotations

import importlib
import logging
from typing import Dict, List, Optional, Type

from .base import Evaluator

logger = logging.getLogger("tracelens.evals")
_REGISTRY: Dict[str, Type[Evaluator]] = {}


def register_evaluator(cls: Type[Evaluator]) -> Type[Evaluator]:
    _REGISTRY[cls.name] = cls
    return cls


def get_evaluator(name: str) -> Optional[Evaluator]:
    cls = _REGISTRY.get(name)
    return cls() if cls else None


def list_evaluators() -> List[dict]:
    return [c.describe() for c in sorted(_REGISTRY.values(), key=lambda c: (c.kind != "llm", c.name))]


def load_plugins(modules: List[str]) -> None:
    from . import heuristic, llm_judge  # noqa: F401  built-ins register themselves
    for m in modules:
        try:
            importlib.import_module(m)
        except Exception as e:
            logger.error("failed to load plugin %s: %s", m, e)
    try:
        from importlib.metadata import entry_points
        for ep in entry_points(group="tracelens.evaluators"):
            try:
                obj = ep.load()
                if isinstance(obj, type) and issubclass(obj, Evaluator):
                    register_evaluator(obj)
            except Exception as e:
                logger.error("failed to load evaluator entry point %s: %s", ep.name, e)
    except Exception:
        pass
