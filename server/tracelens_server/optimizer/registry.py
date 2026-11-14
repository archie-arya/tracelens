"""Registries for optimizer plugins. Built-ins register on import; add your own with the decorators."""
from __future__ import annotations

from typing import Dict, Generic, List, Optional, Type, TypeVar

from .base import Policy, Provider, Sampler, Scorer

T = TypeVar("T")


class Registry(Generic[T]):
    def __init__(self, kind: str):
        self.kind = kind
        self._items: Dict[str, Type[T]] = {}

    def register(self, cls: Type[T]) -> Type[T]:
        self._items[getattr(cls, "name")] = cls
        return cls

    def get(self, name: str) -> Optional[T]:
        cls = self._items.get(name)
        return cls() if cls else None

    def names(self) -> List[str]:
        return sorted(self._items)

    def describe(self) -> List[dict]:
        return [self._items[n].describe() for n in self.names()]  # type: ignore[attr-defined]


provider_registry: Registry[Provider] = Registry("provider")
scorer_registry: Registry[Scorer] = Registry("scorer")
sampler_registry: Registry[Sampler] = Registry("sampler")
policy_registry: Registry[Policy] = Registry("policy")

register_provider = provider_registry.register
register_scorer = scorer_registry.register
register_sampler = sampler_registry.register
register_policy = policy_registry.register


def load_builtins() -> None:
    from . import policies as _p, providers as _pr, samplers as _s, scorers as _sc  # noqa: F401


def catalog() -> dict:
    load_builtins()
    return {"providers": provider_registry.describe(), "scorers": scorer_registry.describe(), "samplers": sampler_registry.describe(), "policies": policy_registry.describe()}
