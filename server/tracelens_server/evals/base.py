"""Evaluator plugin interface.

    from tracelens_server.evals import Evaluator, EvalInput, EvalResult, register_evaluator

    @register_evaluator
    class MentionsBrand(Evaluator):
        name = "mentions_brand"
        description = "1 if the answer mentions our brand"
        async def evaluate(self, inp: EvalInput, config: dict) -> EvalResult:
            return EvalResult(score=float("acme" in str(inp.output).lower()))

Load it with ``TRACELENS_PLUGINS=my_module`` or a ``tracelens.evaluators`` entry point.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class EvalInput:
    input: Any = None
    output: Any = None
    expected_output: Any = None
    context: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    trace: Optional[Dict[str, Any]] = None  # full trace (with observations) when available


@dataclass
class EvalResult:
    score: Optional[float] = None  # normalized 0..1 where possible
    label: Optional[str] = None
    reason: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ConfigField:
    name: str
    type: str = "string"  # string | number | boolean | text | list
    default: Any = None
    description: str = ""
    required: bool = False


class Evaluator:
    name: str = "base"
    description: str = ""
    kind: str = "heuristic"  # heuristic | llm | custom
    score_name: Optional[str] = None  # defaults to name
    higher_is_better: bool = True
    requires: List[str] = []  # e.g. ["expected_output"] or ["context"]
    config_fields: List[ConfigField] = []

    async def evaluate(self, inp: EvalInput, config: Dict[str, Any]) -> EvalResult:  # pragma: no cover - interface
        raise NotImplementedError

    @classmethod
    def describe(cls) -> Dict[str, Any]:
        return {"name": cls.name, "description": cls.description, "kind": cls.kind, "score_name": cls.score_name or cls.name,
                "higher_is_better": cls.higher_is_better, "requires": list(cls.requires), "config": [asdict(f) for f in cls.config_fields]}
