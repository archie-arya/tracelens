"""Comparison scorers: how good is a candidate's output compared with the original model's output?

Every scorer returns a *relative quality* in 0..1, where 1.0 means "at least as good as the original".
Built-ins: ``pairwise_judge`` (LLM judge, position-randomized), ``exact_match``, ``json_match``, ``similarity``,
``tool_call_match`` (tool-calling steps: same decision, tool names, order and arguments), ``auto`` (tool_call_match for
tool-calling steps, json_match for JSON outputs, otherwise pairwise_judge, falling back to similarity), and an adapter
``eval:<evaluator>`` that reuses any registered evaluator (e.g. ``eval:llm_relevance``, ``eval:regex``).
"""
from __future__ import annotations

import json
import random
import re
from typing import Any, Dict, Optional

from ..evals.base import ConfigField, EvalInput
from ..evals.llm import JudgeLLM, get_judge
from ..evals.registry import get_evaluator
from .base import Comparison, Completion, Sample, Scorer
from .registry import register_scorer, scorer_registry
from .tools import compare_tool_calls


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().strip('"').strip()).lower()


def _parse_json(s: str) -> Any:
    s = (s or "").strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", s, re.S)
    if m:
        s = m.group(1).strip()
    try:
        return json.loads(s)
    except Exception:
        m = re.search(r"(\{.*\}|\[.*\])", s, re.S)
        if m:
            try:
                return json.loads(m.group(1))
            except Exception:
                return None
    return None


def _flat(v: Any, prefix: str = "") -> Dict[str, Any]:
    if isinstance(v, dict):
        out: Dict[str, Any] = {}
        for k, x in v.items():
            out.update(_flat(x, f"{prefix}{k}."))
        return out
    return {prefix.rstrip("."): (_norm(v) if isinstance(v, str) else v)}


def _prompt_text(sample: Sample, limit: int = 6000) -> str:
    s = "\n".join(f"[{m.get('role')}] {m.get('content')}" for m in sample.messages)
    return s if len(s) <= limit else s[:limit] + " ...[truncated]"


@register_scorer
class ExactMatch(Scorer):
    name = "exact_match"
    description = "1 if the candidate output equals the original (whitespace/case/quote-insensitive)"

    async def compare(self, sample: Sample, candidate: Completion, config: Dict[str, Any]) -> Comparison:
        ok = _norm(candidate.text) == _norm(sample.output_text)
        return Comparison(1.0 if ok else 0.0, "match" if ok else "mismatch")


@register_scorer
class JsonMatch(Scorer):
    name = "json_match"
    description = "Share of the original JSON's (flattened) fields the candidate reproduces exactly; 0 if not valid JSON"
    config_fields = [ConfigField("keys", "list", None, "Only compare these top-level keys")]

    def applicable(self, sample: Sample) -> bool:
        return isinstance(_parse_json(sample.output_text), (dict, list))

    async def compare(self, sample: Sample, candidate: Completion, config: Dict[str, Any]) -> Comparison:
        a, b = _parse_json(sample.output_text), _parse_json(candidate.text)
        if b is None:
            return Comparison(0.0, "invalid_json", "candidate output is not valid JSON")
        if isinstance(a, dict) and isinstance(b, dict):
            keys = config.get("keys")
            if keys:
                a = {k: a.get(k) for k in keys}
                b = {k: b.get(k) for k in keys}
            fa, fb = _flat(a), _flat(b)
            if not fa:
                return Comparison(1.0, "match")
            same = [k for k in fa if k in fb and fb[k] == fa[k]]
            diff = [k for k in fa if k not in same]
            score = len(same) / len(fa)
            return Comparison(round(score, 4), "match" if score == 1 else "partial", None if not diff else "differs on: " + ", ".join(diff[:8]))
        ok = a == b
        return Comparison(1.0 if ok else 0.0, "match" if ok else "mismatch")


@register_scorer
class Similarity(Scorer):
    name = "similarity"
    description = "Token-overlap F1 between candidate and original output (cheap, no LLM)"

    async def compare(self, sample: Sample, candidate: Completion, config: Dict[str, Any]) -> Comparison:
        ta, tb = re.findall(r"\w+", sample.output_text.lower()), re.findall(r"\w+", candidate.text.lower())
        if not ta and not tb:
            return Comparison(1.0, "equal")
        if not ta or not tb:
            return Comparison(0.0, "empty")
        common = sum(min(ta.count(w), tb.count(w)) for w in set(ta))
        p, r = common / len(tb), common / len(ta)
        f1 = 0.0 if common == 0 else 2 * p * r / (p + r)
        return Comparison(round(f1, 4), "similar" if f1 >= 0.6 else "different")


PAIRWISE_SYSTEM = ("You are an impartial judge comparing two AI responses to the same prompt. Judge correctness, faithfulness to "
                   "any provided context, completeness, instruction-following (format, length, language) and helpfulness. "
                   "Do not prefer a response for being longer. Respond ONLY with JSON: "
                   '{"score_1": <0..1>, "score_2": <0..1>, "reason": "<one or two sentences>"}')


@register_scorer
class PairwiseJudge(Scorer):
    name = "pairwise_judge"
    kind = "llm"
    description = "LLM judge scores original vs candidate side by side (order randomized to avoid position bias)"
    config_fields = [ConfigField("judge_models", "string", "", "Optional judge chain, e.g. gemini:gemini-flash-lite-latest"),
                     ConfigField("criteria", "text", "", "Extra task-specific criteria for the judge")]

    def applicable(self, sample: Sample) -> bool:
        return get_judge().available()

    async def compare(self, sample: Sample, candidate: Completion, config: Dict[str, Any]) -> Comparison:
        judge = JudgeLLM([s for s in str(config["judge_models"]).split(",") if s]) if config.get("judge_models") else get_judge()
        flip = random.Random(sample.observation_id + candidate.text[:50]).random() < 0.5
        r1, r2 = (candidate.text, sample.output_text) if flip else (sample.output_text, candidate.text)
        prompt = f"PROMPT:\n{_prompt_text(sample)}\n\nRESPONSE 1:\n{r1[:5000]}\n\nRESPONSE 2:\n{r2[:5000]}"
        if config.get("criteria"):
            prompt = f"EXTRA CRITERIA:\n{config['criteria']}\n\n" + prompt
        data, used = await judge.complete_json(PAIRWISE_SYSTEM, prompt)
        s1, s2 = float(data.get("score_1", 0) or 0), float(data.get("score_2", 0) or 0)
        orig, cand = (s2, s1) if flip else (s1, s2)
        rel = max(0.0, min(1.0, 1.0 - max(0.0, orig - cand)))
        label = "better" if cand - orig > 0.1 else "worse" if orig - cand > 0.1 else "equal"
        return Comparison(round(rel, 4), label, data.get("reason"), {"judge_model": used, "original_score": orig, "candidate_score": cand})


class EvaluatorScorer(Scorer):
    """Adapter: reuse any registered evaluator. Reference-style evaluators (needing ``expected_output``) score the
    candidate with the original output as reference; absolute evaluators score both and compare."""
    kind = "adapter"

    def __init__(self, evaluator: str):
        self.evaluator = evaluator
        self.name = f"eval:{evaluator}"

    async def compare(self, sample: Sample, candidate: Completion, config: Dict[str, Any]) -> Comparison:
        ev = get_evaluator(self.evaluator)
        if ev is None:
            raise ValueError(f"unknown evaluator {self.evaluator!r}")
        user = next((m.get("content") for m in reversed(sample.messages) if m.get("role") == "user"), None)
        def mk(out: str) -> EvalInput:
            return EvalInput(input=user, output=out, expected_output=sample.output_text, context=sample.context)
        if "expected_output" in ev.requires:
            r = await ev.evaluate(mk(candidate.text), config)
            return Comparison(r.score, r.label, r.reason, r.metadata)
        rc = await ev.evaluate(mk(candidate.text), config)
        ro = await ev.evaluate(mk(sample.output_text), config)
        if rc.score is None or ro.score is None:
            return Comparison(None, "error", rc.reason or ro.reason)
        c, o = (rc.score, ro.score) if ev.higher_is_better else (1 - rc.score, 1 - ro.score)
        rel = max(0.0, min(1.0, 1.0 - max(0.0, o - c)))
        return Comparison(round(rel, 4), "better" if c > o + 0.1 else "worse" if o > c + 0.1 else "equal", rc.reason,
                          {"original_score": ro.score, "candidate_score": rc.score})


@register_scorer
class ToolCallMatch(Scorer):
    name = "tool_call_match"
    description = ("Tool-calling steps: does the candidate make the same decision (call vs answer), call the same tools in the same "
                   "order with the same arguments? Final-answer turns fall back to a text scorer; judge_weight blends both")
    config_fields = [ConfigField("args_mode", "string", "lenient", "lenient (key order, case, whitespace, '5' vs 5) | strict (exact JSON) | partial (field overlap)"),
                     ConfigField("weights", "object", {"names": 0.3, "order": 0.2, "arguments": 0.5}, "Weights of the sub-scores when the decision matches"),
                     ConfigField("ignore_args", "list", None, "Argument names (or tool.arg) to leave out, e.g. free-text search queries"),
                     ConfigField("judge_weight", "number", 0.0, "0..1: blend in a text scorer on the assistant text (0 = tool calls only)"),
                     ConfigField("text_scorer", "string", "auto", "Scorer for final answers / the blended text part")]

    def applicable(self, sample: Sample) -> bool:
        return sample.tool_step

    async def _text(self, sample: Sample, candidate: Completion, config: Dict[str, Any]) -> Optional[Comparison]:
        name = config.get("text_scorer") or "auto"
        s = resolve_scorer(name) if name != self.name else None
        if isinstance(s, Auto):
            s = s.pick(sample, text_only=True)
        if s is None:
            return None
        c = await s.compare(sample, candidate, config.get("text_scorer_config") or {})
        c.metadata = {**(c.metadata or {}), "text_scorer": getattr(s, "name", name)}
        return c

    async def compare(self, sample: Sample, candidate: Completion, config: Dict[str, Any]) -> Comparison:
        mode = str(config.get("args_mode") or "lenient")
        t = compare_tool_calls(sample.tool_calls or [], candidate.tool_calls or [], mode, config.get("weights"), config.get("ignore_args"))
        meta: Dict[str, Any] = {k: v for k, v in t.items() if k not in ("label", "reason")}
        meta["scorer"] = self.name
        score, reason = t["score"], t.get("reason")
        both_answered = not sample.tool_calls and not candidate.tool_calls
        w = max(0.0, min(1.0, float(config.get("judge_weight") or 0.0)))
        if both_answered and sample.output_text.strip():  # a final answer: the tool part agrees, judge the text itself
            tc = await self._text(sample, candidate, config)
            if tc is not None and tc.score is not None:
                meta.update({"text_score": tc.score, **{k: v for k, v in (tc.metadata or {}).items() if k == "text_scorer"}})
                return Comparison(tc.score, tc.label or t["label"], tc.reason, meta)
        elif w > 0 and t["decision"] == 1.0 and (sample.output_text.strip() or candidate.text.strip()):
            tc = await self._text(sample, candidate, config)
            if tc is not None and tc.score is not None:
                meta["text_score"] = tc.score
                score = round((1 - w) * score + w * tc.score, 4)
        return Comparison(score, t["label"], reason, meta)


@register_scorer
class Auto(Scorer):
    name = "auto"
    description = "tool_call_match for tool-calling steps, json_match for JSON outputs, else pairwise_judge when a judge model is configured, else similarity"

    def pick(self, sample: Sample, text_only: bool = False) -> Scorer:
        if sample.tool_step and not text_only:
            return scorer_registry.get("tool_call_match")  # type: ignore[return-value]
        for name in ("json_match", "pairwise_judge"):
            s = scorer_registry.get(name)
            if s and s.applicable(sample):
                return s
        return scorer_registry.get("similarity")  # type: ignore[return-value]

    async def compare(self, sample: Sample, candidate: Completion, config: Dict[str, Any]) -> Comparison:
        s = self.pick(sample)
        c = await s.compare(sample, candidate, config)
        c.metadata = {**(c.metadata or {}), "scorer": s.name}
        return c


def resolve_scorer(spec: str) -> Optional[Scorer]:
    if spec.startswith("eval:"):
        return EvaluatorScorer(spec[5:]) if get_evaluator(spec[5:]) else None
    return scorer_registry.get(spec)
