"""Samplers choose which stored generations of a step get replayed. Duplicated prompts are always collapsed first."""
from __future__ import annotations

import hashlib
import json
import random
from typing import Any, Dict, List

from ..evals.base import ConfigField
from .base import Sample, Sampler
from .registry import register_sampler


def dedupe(samples: List[Sample]) -> List[Sample]:
    seen, out = set(), []
    for s in samples:
        h = hashlib.sha256(json.dumps(s.messages, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        if h not in seen:
            seen.add(h)
            out.append(s)
    return out


@register_sampler
class Recent(Sampler):
    name = "recent"
    description = "The most recent N distinct prompts"

    def select(self, samples: List[Sample], n: int, config: Dict[str, Any]) -> List[Sample]:
        return sorted(dedupe(samples), key=lambda s: -(s.start_time or 0))[:n]


@register_sampler
class RandomSampler(Sampler):
    name = "random"
    description = "A uniform random sample of N distinct prompts (seeded, so runs are reproducible)"
    config_fields = [ConfigField("seed", "number", 42, "Random seed")]

    def select(self, samples: List[Sample], n: int, config: Dict[str, Any]) -> List[Sample]:
        pool = dedupe(samples)
        random.Random(config.get("seed", 42)).shuffle(pool)
        return pool[:n]


@register_sampler
class Stratified(Sampler):
    name = "stratified"
    description = "Spread evenly over time and prompt length, so rare long prompts are represented"

    def select(self, samples: List[Sample], n: int, config: Dict[str, Any]) -> List[Sample]:
        pool = sorted(dedupe(samples), key=lambda s: (len(json.dumps(s.messages)), s.start_time or 0))
        if len(pool) <= n:
            return pool
        step = len(pool) / n
        return [pool[int(i * step)] for i in range(n)]
