"""Cost Optimizer plugin interfaces.

The optimizer replays real, stored LLM generations against cheaper candidate models and scores the
candidates' outputs against the originals. Four plugin points, each with its own registry:

* :class:`Provider`  - runs a chat completion for a ``provider:model`` candidate (OpenAI-compatible, Gemini, ...)
* :class:`Scorer`    - compares a candidate output with the original output (0..1, 1 = as good as the original)
* :class:`Sampler`   - picks which stored generations of a step to replay
* :class:`Policy`    - turns aggregated per-candidate stats into recommendations

Write one by subclassing and decorating, e.g.::

    from tracelens_server.optimizer import Provider, Completion, register_provider

    @register_provider
    class MyGateway(Provider):
        name = "mygw"
        async def complete(self, model, messages, params):
            ...
            return Completion(text=..., input_tokens=..., output_tokens=...)

then load the module with ``TRACELENS_PLUGINS=my_module``.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from ..evals.base import ConfigField


@dataclass
class Sample:
    """One stored LLM generation that can be replayed."""
    observation_id: str
    trace_id: str
    step: str                         # display label of the step, e.g. "support-rag / rewrite_query / gemini.generate_content"
    model: Optional[str]
    messages: List[Dict[str, Any]]    # normalized OpenAI-style chat messages
    params: Dict[str, Any]            # temperature, max_tokens, ... (normalized)
    output: Any                       # original output
    output_text: str
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    cost: Optional[float] = None
    latency_ms: Optional[float] = None
    start_time: Optional[float] = None
    redacted: bool = False
    context: List[str] = field(default_factory=list)


@dataclass
class Completion:
    text: str = ""
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    latency_ms: Optional[float] = None
    cost: Optional[float] = None      # filled from the pricing table when the provider leaves it empty
    error: Optional[str] = None
    cached: bool = False
    raw_model: Optional[str] = None


@dataclass
class Comparison:
    score: Optional[float]            # relative quality 0..1; 1.0 = at least as good as the original
    label: Optional[str] = None       # e.g. better | equal | worse | match | mismatch
    reason: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


class _Plugin:
    name: str = "base"
    description: str = ""
    config_fields: List[ConfigField] = []

    @classmethod
    def describe(cls) -> Dict[str, Any]:
        return {"name": cls.name, "description": cls.description, "config": [asdict(f) for f in cls.config_fields]}


class Provider(_Plugin):
    """Runs one chat completion. ``model`` is the part after ``provider:`` in a candidate spec."""
    local: bool = False               # local models (e.g. Ollama) cost $0 regardless of the pricing table
    default_rpm: int = 60             # requests per minute budget (override with TRACELENS_OPTIMIZER_RPM_<NAME>)

    def available(self) -> bool:
        return True

    async def complete(self, model: str, messages: List[Dict[str, Any]], params: Dict[str, Any]) -> Completion:  # pragma: no cover
        raise NotImplementedError

    @classmethod
    def describe(cls) -> Dict[str, Any]:
        d = super().describe()
        d.update({"local": cls.local, "available": cls().available(), "default_rpm": cls.default_rpm})
        return d


class Scorer(_Plugin):
    kind: str = "heuristic"           # heuristic | llm
    max_concurrency: Optional[int] = None

    def applicable(self, sample: Sample) -> bool:
        return True

    async def compare(self, sample: Sample, candidate: Completion, config: Dict[str, Any]) -> Comparison:  # pragma: no cover
        raise NotImplementedError

    @classmethod
    def describe(cls) -> Dict[str, Any]:
        d = super().describe()
        d["kind"] = cls.kind
        return d


class Sampler(_Plugin):
    def select(self, samples: List[Sample], n: int, config: Dict[str, Any]) -> List[Sample]:  # pragma: no cover
        raise NotImplementedError


class Policy(_Plugin):
    def recommend(self, step: str, baseline: Dict[str, Any], candidates: List[Dict[str, Any]], config: Dict[str, Any]) -> Dict[str, Any]:  # pragma: no cover
        """Return one recommendation dict for a step (see ``policies.recommendation``)."""
        raise NotImplementedError
