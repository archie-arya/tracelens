"""Deterministic evaluators - free, instant, no LLM needed."""
from __future__ import annotations

import difflib
import json
import re
from typing import Any, Dict

from .base import ConfigField, EvalInput, EvalResult, Evaluator
from .registry import register_evaluator


def _text(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    if isinstance(v, dict):
        for k in ("content", "text", "answer", "output", "result"):
            if isinstance(v.get(k), str):
                return v[k]
    return json.dumps(v, ensure_ascii=False)


@register_evaluator
class JsonValid(Evaluator):
    name = "json_valid"
    description = "1 if the output is valid JSON (optionally containing required keys)."
    config_fields = [ConfigField("required_keys", "list", [], "Keys that must be present at the top level")]

    async def evaluate(self, inp: EvalInput, config: Dict[str, Any]) -> EvalResult:
        out = inp.output
        if isinstance(out, (dict, list)):
            data = out
        else:
            s = _text(out).strip()
            m = re.search(r"```(?:json)?\s*(.*?)```", s, re.S)
            if m:
                s = m.group(1)
            try:
                data = json.loads(s)
            except Exception as e:
                return EvalResult(0.0, "invalid", f"Not valid JSON: {e}")
        missing = [k for k in (config.get("required_keys") or []) if not (isinstance(data, dict) and k in data)]
        if missing:
            return EvalResult(0.0, "missing_keys", f"Missing keys: {missing}")
        return EvalResult(1.0, "valid", "Valid JSON")


@register_evaluator
class RegexMatch(Evaluator):
    name = "regex"
    description = "1 if the output matches (or, with must_match=false, does not match) a regular expression."
    config_fields = [ConfigField("pattern", "string", "", "Python regular expression", True), ConfigField("must_match", "boolean", True, ""),
                     ConfigField("ignore_case", "boolean", True, "")]

    async def evaluate(self, inp: EvalInput, config: Dict[str, Any]) -> EvalResult:
        flags = re.I if config.get("ignore_case", True) else 0
        found = re.search(config.get("pattern") or "", _text(inp.output), flags) is not None
        ok = found if config.get("must_match", True) else not found
        return EvalResult(1.0 if ok else 0.0, "pass" if ok else "fail", f"pattern {'found' if found else 'not found'}")


@register_evaluator
class Length(Evaluator):
    name = "length"
    description = "1 if the output length (characters) is within [min, max]."
    config_fields = [ConfigField("min", "number", 1, ""), ConfigField("max", "number", 4000, "")]

    async def evaluate(self, inp: EvalInput, config: Dict[str, Any]) -> EvalResult:
        n = len(_text(inp.output))
        lo, hi = float(config.get("min", 1)), float(config.get("max", 4000))
        ok = lo <= n <= hi
        return EvalResult(1.0 if ok else 0.0, "pass" if ok else "fail", f"{n} chars (allowed {lo:g}-{hi:g})", {"length": n})


@register_evaluator
class Contains(Evaluator):
    name = "contains"
    description = "Fraction of keywords present in the output (mode=all requires every keyword)."
    config_fields = [ConfigField("keywords", "list", [], "", True), ConfigField("mode", "string", "any", "any | all | fraction")]

    async def evaluate(self, inp: EvalInput, config: Dict[str, Any]) -> EvalResult:
        text = _text(inp.output).lower()
        kws = [k.lower() for k in (config.get("keywords") or [])]
        if not kws:
            return EvalResult(None, "skipped", "no keywords configured")
        hits = [k for k in kws if k in text]
        mode = config.get("mode", "any")
        score = (1.0 if hits else 0.0) if mode == "any" else (1.0 if len(hits) == len(kws) else 0.0) if mode == "all" else len(hits) / len(kws)
        return EvalResult(score, None, f"matched {hits}")


@register_evaluator
class ExactMatch(Evaluator):
    name = "exact_match"
    description = "1 if output equals the expected output (normalized whitespace/case)."
    requires = ["expected_output"]

    async def evaluate(self, inp: EvalInput, config: Dict[str, Any]) -> EvalResult:
        if inp.expected_output is None:
            return EvalResult(None, "skipped", "no expected output")
        norm = lambda s: " ".join(_text(s).lower().split())  # noqa: E731
        ok = norm(inp.output) == norm(inp.expected_output)
        return EvalResult(1.0 if ok else 0.0, "match" if ok else "mismatch")


@register_evaluator
class Similarity(Evaluator):
    name = "similarity"
    description = "Fuzzy string similarity (0-1) between output and expected output."
    requires = ["expected_output"]

    async def evaluate(self, inp: EvalInput, config: Dict[str, Any]) -> EvalResult:
        if inp.expected_output is None:
            return EvalResult(None, "skipped", "no expected output")
        a, b = _text(inp.output).lower(), _text(inp.expected_output).lower()
        ratio = difflib.SequenceMatcher(None, a, b).ratio()
        # token recall helps when the output is a longer paraphrase of the expected answer
        bt = set(re.findall(r"\w+", b))
        recall = len(bt & set(re.findall(r"\w+", a))) / len(bt) if bt else 0.0
        return EvalResult(round(max(ratio, recall), 4), None, f"char ratio {ratio:.2f}, token recall {recall:.2f}")


@register_evaluator
class LatencyBudget(Evaluator):
    name = "latency_budget"
    description = "1 if the trace finished within max_ms milliseconds."
    config_fields = [ConfigField("max_ms", "number", 5000, "")]

    async def evaluate(self, inp: EvalInput, config: Dict[str, Any]) -> EvalResult:
        lat = (inp.trace or {}).get("latency_ms")
        if lat is None:
            return EvalResult(None, "skipped", "no latency")
        ok = lat <= float(config.get("max_ms", 5000))
        return EvalResult(1.0 if ok else 0.0, "pass" if ok else "slow", f"{lat:.0f} ms")
