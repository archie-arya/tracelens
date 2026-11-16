"""Recommendation policies: decide, per step, whether to switch models and to which candidate.

Each policy receives the baseline (the original model's observed cost/latency) and per-candidate stats::

    {"candidate": "gemini:gemma-4-26b-a4b-it", "n": 8, "quality_mean": 0.97, "quality_ci": [0.92, 1.0],
     "cost_per_call": 0.0, "cost_savings_pct": 100.0, "latency_delta_pct": 85.0, "price_known": True, ...}

and returns :func:`recommendation` output. All numbers are estimates from a sample of replays.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..evals.base import ConfigField
from .base import Policy
from .registry import register_policy


def confidence(n: int, ci: Optional[List[float]], threshold: float) -> str:
    if n >= 30 and ci and ci[0] >= threshold:
        return "high"
    if n >= 10:
        return "medium"
    return "low"


def recommendation(step: str, baseline: Dict[str, Any], best: Optional[Dict[str, Any]], action: str, rationale: str,
                   threshold: float = 0.95) -> Dict[str, Any]:
    rec: Dict[str, Any] = {"step": step, "action": action, "from_model": baseline.get("model"), "rationale": rationale,
                           "estimate": True}
    if best:
        rec.update({"to_model": best["candidate"], "cost_savings_pct": best.get("cost_savings_pct"),
                    "quality_delta_pct": None if best.get("quality_mean") is None else round((best["quality_mean"] - 1) * 100, 1),
                    "quality_ci": best.get("quality_ci"), "latency_delta_pct": best.get("latency_delta_pct"),
                    "monthly_savings_usd": best.get("monthly_savings_usd"), "n": best.get("n", 0),
                    "confidence": confidence(best.get("n", 0), best.get("quality_ci"), threshold)})
        if best.get("tool_agreement") is not None:
            rec.update({"tool_agreement": best["tool_agreement"], "decision_agreement": best.get("decision_agreement"), "tool_n": best.get("tool_n")})
    return rec


def _usable(c: Dict[str, Any], min_samples: int) -> bool:
    return c.get("price_known") and c.get("quality_mean") is not None and c.get("n", 0) >= min_samples


def _pct(x: Optional[float]) -> str:
    return "n/a" if x is None else f"{x:+.0f}%"


@register_policy
class QualityThreshold(Policy):
    name = "quality_threshold"
    description = "Cheapest candidate whose average relative quality stays within max_quality_drop of the original"
    config_fields = [ConfigField("max_quality_drop", "number", 0.05, "Allowed average quality drop (0.05 = 5%)"),
                     ConfigField("min_savings_pct", "number", 10, "Only recommend switches that save at least this much"),
                     ConfigField("min_samples", "number", 5, "Minimum scored replays per candidate"),
                     ConfigField("max_latency_increase_pct", "number", None, "Optional: reject candidates slower than this (e.g. 50)"),
                     ConfigField("min_tool_agreement", "number", 0.95, "Tool-calling steps: share of replays where the candidate makes the "
                                 "same tool calls (names, order, arguments) as the original; null disables")]

    def recommend(self, step, baseline, candidates, config):
        drop, min_sav, min_n = float(config.get("max_quality_drop", 0.05)), float(config.get("min_savings_pct", 10)), int(config.get("min_samples", 5))
        max_lat = config.get("max_latency_increase_pct")
        mta = config.get("min_tool_agreement", 0.95)
        mta = None if mta is None else float(mta)
        usable = [c for c in candidates if _usable(c, min_n)]
        if not usable:
            return recommendation(step, baseline, None, "insufficient_data", f"No candidate has ≥{min_n} scored replays with a known price.")

        def fast_enough(c):
            return max_lat is None or c.get("latency_delta_pct") is None or c["latency_delta_pct"] <= float(max_lat)

        def tools_agree(c):
            return mta is None or c.get("tool_agreement") is None or c["tool_agreement"] >= mta

        ok = [c for c in usable if c["quality_mean"] >= 1 - drop and (c.get("cost_savings_pct") or 0) >= min_sav and fast_enough(c) and tools_agree(c)]
        if ok:
            best = max(ok, key=lambda c: (c["cost_savings_pct"], c["quality_mean"]))
            lat = f" and {_pct(best.get('latency_delta_pct'))} latency" if best.get("latency_delta_pct") is not None else ""
            tools = ""
            if best.get("tool_agreement") is not None:
                tools = f"; it makes the same tool calls in {best['tool_agreement'] * 100:.0f}% of them"
            return recommendation(step, baseline, best, "switch",
                                  f"{best['candidate']} is {best['cost_savings_pct']:.0f}% cheaper with {(best['quality_mean'] - 1) * 100:+.1f}% "
                                  f"relative quality{lat} over {best['n']} replays (threshold -{drop * 100:.0f}%){tools}.", 1 - drop)
        closest = max(usable, key=lambda c: (c["quality_mean"], c.get("cost_savings_pct") or 0))

        def why(c):
            reasons = []
            sav = c.get("cost_savings_pct") or 0
            if sav < min_sav:
                reasons.append(f"is only {sav:.0f}% cheaper (need {min_sav:.0f}%)" if sav > 0 else f"costs more ({_pct(-sav)})")
            if c["quality_mean"] < 1 - drop:
                reasons.append(f"loses {(1 - c['quality_mean']) * 100:.0f}% quality (allowed {drop * 100:.0f}%)")
            if not tools_agree(c):
                reasons.append(f"makes the same tool calls in only {c['tool_agreement'] * 100:.0f}% of replays (need {mta * 100:.0f}%)")
            if not fast_enough(c):
                reasons.append(f"is {_pct(c.get('latency_delta_pct'))} slower (allowed +{float(max_lat):.0f}%)")
            return f"{c['candidate']} " + (", ".join(reasons[:-1]) + " and " + reasons[-1] if len(reasons) > 1 else reasons[0])
        return recommendation(step, baseline, closest, "keep", f"Keep {baseline.get('model')}: " + "; ".join(why(c) for c in usable) + ".", 1 - drop)


@register_policy
class Budget(Policy):
    name = "budget"
    description = "Highest-quality candidate that reaches a target cost reduction"
    config_fields = [ConfigField("target_savings_pct", "number", 50, "Required cost reduction in %"),
                     ConfigField("min_quality", "number", 0.8, "Never accept average relative quality below this"),
                     ConfigField("min_samples", "number", 5, "Minimum scored replays per candidate")]

    def recommend(self, step, baseline, candidates, config):
        target, minq, min_n = float(config.get("target_savings_pct", 50)), float(config.get("min_quality", 0.8)), int(config.get("min_samples", 5))
        usable = [c for c in candidates if _usable(c, min_n)]
        ok = [c for c in usable if (c.get("cost_savings_pct") or 0) >= target and c["quality_mean"] >= minq]
        if ok:
            best = max(ok, key=lambda c: (c["quality_mean"], c["cost_savings_pct"]))
            return recommendation(step, baseline, best, "switch", f"Meets the {target:.0f}% savings target with the best quality "
                                  f"({(best['quality_mean'] - 1) * 100:+.1f}%) over {best['n']} replays.", minq)
        return recommendation(step, baseline, None, "keep" if usable else "insufficient_data",
                              f"No candidate saves ≥{target:.0f}% while keeping quality ≥{minq:.0%}.")


@register_policy
class Weighted(Policy):
    name = "weighted"
    description = "Maximize quality_weight·quality + cost_weight·savings (+ latency_weight·speedup); switch only if it beats the original"
    config_fields = [ConfigField("quality_weight", "number", 0.7, ""), ConfigField("cost_weight", "number", 0.3, ""),
                     ConfigField("latency_weight", "number", 0.0, ""), ConfigField("min_samples", "number", 5, "")]

    def recommend(self, step, baseline, candidates, config):
        qw, cw, lw = float(config.get("quality_weight", 0.7)), float(config.get("cost_weight", 0.3)), float(config.get("latency_weight", 0.0))
        usable = [c for c in candidates if _usable(c, int(config.get("min_samples", 5)))]
        def util(c):
            return qw * c["quality_mean"] + cw * (c.get("cost_savings_pct") or 0) / 100 - lw * (c.get("latency_delta_pct") or 0) / 100
        if not usable:
            return recommendation(step, baseline, None, "insufficient_data", "Not enough scored replays.")
        best = max(usable, key=util)
        if util(best) > qw:  # baseline utility: quality 1, no savings, no speedup
            return recommendation(step, baseline, best, "switch", f"Best weighted utility {util(best):.2f} vs {qw:.2f} for the original.")
        return recommendation(step, baseline, best, "keep", f"No candidate beats the original's weighted utility ({qw:.2f}).")
