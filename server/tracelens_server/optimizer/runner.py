"""Runs optimizations: discover steps, sample stored generations, replay them on candidates, score, recommend."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import statistics
import time
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Tuple

from .. import pricing
from ..storage.base import ObservationFilter, Storage, TraceFilter
from ..util import new_id, now
from .base import Comparison, Completion, Sample
from .providers import call_with_retries, split_spec
from .registry import load_builtins, policy_registry, provider_registry, sampler_registry
from .replay import build_sample
from .scorers import resolve_scorer

logger = logging.getLogger("tracelens.optimizer")

DEFAULTS: Dict[str, Any] = {"sampler": "recent", "sample_size": 10, "scorer": "auto", "policy": "quality_threshold",
                            "max_spend_usd": 1.0, "concurrency": 4, "lookback_days": 30, "examples_per_candidate": 3,
                            "use_cache": True, "min_max_tokens": 1024}
ACTIVE: Dict[str, asyncio.Task] = {}
CANCELLED: set = set()
SEP = " / "


class ConfigError(ValueError):
    pass


# ---------------------------------------------------------------- steps

def parse_step(step: Any) -> Dict[str, Optional[str]]:
    if isinstance(step, dict):
        return {k: step.get(k) for k in ("name", "trace_name", "parent_name", "tag", "model")}
    parts = [p.strip() for p in str(step).split(SEP) if p.strip()] or [str(step).strip()]
    d: Dict[str, Optional[str]] = {"name": parts[-1], "trace_name": None, "parent_name": None, "tag": None, "model": None}
    if len(parts) >= 2:
        d["trace_name"] = parts[0]
    if len(parts) >= 3:
        d["parent_name"] = parts[-2]
    return d


def step_label(trace_name: Optional[str], parent_name: Optional[str], name: str) -> str:
    parts = [trace_name] if trace_name and trace_name != name else []
    if parent_name and parent_name != trace_name:
        parts.append(parent_name)
    return SEP.join(parts + [name])


def _context(storage: Storage, project_id: str, start: float, limit: int) -> Tuple[List[Dict[str, Any]], Dict[str, str], Dict[str, Dict[str, Any]]]:
    gens, _ = storage.list_observations(project_id, ObservationFilter(type="generation", start=start), limit=limit)
    allobs, _ = storage.list_observations(project_id, ObservationFilter(start=start - 3600), limit=limit * 4)
    names = {o["id"]: o.get("name") for o in allobs}
    tids = list({g["trace_id"] for g in gens})
    traces: Dict[str, Dict[str, Any]] = {}
    for i in range(0, len(tids), 500):
        rows, _ = storage.list_traces(project_id, TraceFilter(ids=tids[i:i + 500]), limit=500)
        traces.update({t["id"]: t for t in rows})
    return gens, names, traces


def _label_for(g: Dict[str, Any], names: Dict[str, str], traces: Dict[str, Dict[str, Any]]) -> Tuple[str, Optional[str], Optional[str]]:
    t = traces.get(g["trace_id"]) or {}
    parent = names.get(g.get("parent_id")) if g.get("parent_id") else None
    return step_label(t.get("name"), parent, g.get("name") or "generation"), t.get("name"), parent


def discover_steps(storage: Storage, project_id: str, lookback_days: float = 30, limit: int = 3000) -> List[Dict[str, Any]]:
    start = now() - lookback_days * 86400
    gens, names, traces = _context(storage, project_id, start, limit)
    groups: Dict[str, Dict[str, Any]] = {}
    for g in gens:
        label, tname, parent = _label_for(g, names, traces)
        s = groups.setdefault(label, {"step": label, "name": g.get("name"), "trace_name": tname, "parent_name": parent, "calls": 0,
                                      "models": Counter(), "total_cost": 0.0, "lat": [], "replayable": 0, "skipped": Counter()})
        s["calls"] += 1
        s["models"][g.get("model") or "unknown"] += 1
        s["total_cost"] += g.get("cost") or 0
        if g.get("latency_ms"):
            s["lat"].append(g["latency_ms"])
        smp, why = build_sample(g, label, {"redact": False})
        if smp:
            s["replayable"] += 1
        else:
            s["skipped"][why] += 1
    out = []
    for s in groups.values():
        lat = s.pop("lat")
        s.update({"models": dict(s["models"].most_common()), "skipped": dict(s["skipped"]), "total_cost": round(s["total_cost"], 8),
                  "avg_cost": round(s["total_cost"] / s["calls"], 10) if s["calls"] else None,
                  "avg_latency_ms": round(statistics.mean(lat), 1) if lat else None,
                  "monthly_cost_est": round(s["total_cost"] * 30 / max(lookback_days, 1e-9), 6)})
        out.append(s)
    return sorted(out, key=lambda s: (-s["total_cost"], -s["calls"]))


# ---------------------------------------------------------------- config

def validate(config: Dict[str, Any]) -> Dict[str, Any]:
    load_builtins()
    cfg = {**DEFAULTS, **{k: v for k, v in config.items() if v is not None}}
    if not cfg.get("steps"):
        raise ConfigError("steps: give at least one step (an observation name or 'trace / parent / name' path)")
    if not cfg.get("candidates"):
        raise ConfigError("candidates: give at least one 'provider:model', e.g. gemini:gemma-4-26b-a4b-it")
    cfg["steps"] = [cfg["steps"]] if isinstance(cfg["steps"], (str, dict)) else list(cfg["steps"])
    cfg["candidates"] = [c.strip() for c in (cfg["candidates"].split(",") if isinstance(cfg["candidates"], str) else cfg["candidates"]) if str(c).strip()]
    for c in cfg["candidates"]:
        prov, model = split_spec(c)
        p = provider_registry.get(prov)
        if p is None:
            raise ConfigError(f"unknown provider {prov!r} in {c!r}; available: {', '.join(provider_registry.names())}")
        if not model:
            raise ConfigError(f"missing model in {c!r}")
    if sampler_registry.get(cfg["sampler"]) is None:
        raise ConfigError(f"unknown sampler {cfg['sampler']!r}")
    if resolve_scorer(cfg["scorer"]) is None:
        raise ConfigError(f"unknown scorer {cfg['scorer']!r}")
    if policy_registry.get(cfg["policy"]) is None:
        raise ConfigError(f"unknown policy {cfg['policy']!r}")
    cfg["sample_size"] = max(1, min(int(cfg["sample_size"]), 500))
    cfg["concurrency"] = max(1, min(int(cfg["concurrency"]), 32))
    cfg["max_spend_usd"] = float(cfg["max_spend_usd"])
    return cfg


def cache_key(spec: str, sample: Sample) -> str:
    raw = json.dumps({"c": spec, "m": sample.messages, "p": sample.params}, sort_keys=True, ensure_ascii=False)
    return "rp_" + hashlib.sha256(raw.encode()).hexdigest()[:40]


def _ci(vals: List[float]) -> Optional[List[float]]:
    if not vals:
        return None
    m = statistics.mean(vals)
    if len(vals) < 2:
        return [round(m, 4), round(m, 4)]
    h = 1.96 * statistics.stdev(vals) / math.sqrt(len(vals))
    return [round(max(0.0, m - h), 4), round(min(1.0, m + h), 4)]


def _clip(s: Any, n: int) -> str:
    s = s if isinstance(s, str) else json.dumps(s, ensure_ascii=False)
    return s if len(s) <= n else s[:n] + " …"


# ---------------------------------------------------------------- runs

def create_run(storage: Storage, project_id: str, config: Dict[str, Any]) -> Dict[str, Any]:
    cfg = validate(config)
    run = {"id": new_id("opt_"), "project_id": project_id, "name": cfg.get("name") or f"{len(cfg['steps'])} step(s) × {len(cfg['candidates'])} candidate(s)",
           "status": "queued", "config": cfg, "progress": {"total": 0, "done": 0, "spend_usd": 0.0, "cache_hits": 0, "errors": 0},
           "results": None, "error": None, "created_at": now(), "finished_at": None}
    storage.save_optimizer_run(project_id, run)
    return run


def start_run(storage: Storage, project_id: str, run: Dict[str, Any]) -> None:
    task = asyncio.get_running_loop().create_task(execute(storage, project_id, run))
    ACTIVE[run["id"]] = task
    task.add_done_callback(lambda _t: ACTIVE.pop(run["id"], None))


def status_of(run: Dict[str, Any]) -> Dict[str, Any]:
    if run.get("status") in ("queued", "running") and run["id"] not in ACTIVE:
        run = {**run, "status": "interrupted"}
    return run


async def execute(storage: Storage, project_id: str, run: Dict[str, Any]) -> Dict[str, Any]:
    cfg = run["config"]
    prog = run["progress"]
    run["status"] = "running"
    last_save = [0.0]

    async def save(force: bool = False) -> None:
        if force or time.time() - last_save[0] > 2:
            last_save[0] = time.time()
            await asyncio.to_thread(storage.save_optimizer_run, project_id, run)

    try:
        start = now() - float(cfg["lookback_days"]) * 86400
        gens, names, traces = await asyncio.to_thread(_context, storage, project_id, start, 5000)
        sampler = sampler_registry.get(cfg["sampler"])
        scorer = resolve_scorer(cfg["scorer"])
        policy = policy_registry.get(cfg["policy"])
        plan: List[Tuple[Dict[str, Any], str, List[Sample], Dict[str, int], int]] = []
        for st in cfg["steps"]:
            f = parse_step(st)
            matched, skipped, label = [], Counter(), None
            for g in gens:
                lab, tname, parent = _label_for(g, names, traces)
                if g.get("name") != f["name"] or (f["trace_name"] and tname != f["trace_name"]) or (f["parent_name"] and parent != f["parent_name"]):
                    continue
                if f.get("model") and g.get("model") != f["model"]:
                    continue
                if f.get("tag") and f["tag"] not in ((traces.get(g["trace_id"]) or {}).get("tags") or []):
                    continue
                label = label or (lab if (f["trace_name"] or f["parent_name"]) else f["name"])
                smp, why = build_sample(g, label, cfg)
                if smp:
                    matched.append(smp)
                else:
                    skipped[why] += 1
            calls = len(matched) + sum(skipped.values())
            chosen = sampler.select(matched, cfg["sample_size"], cfg.get("sampler_config") or {}) if matched else []
            plan.append((f, label or step_label(f["trace_name"], f["parent_name"], f["name"]), chosen, dict(skipped), calls))
        prog["total"] = sum(len(p[2]) for p in plan) * len(cfg["candidates"])
        await save(True)

        sem = asyncio.Semaphore(cfg["concurrency"])
        results: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)

        async def one(label: str, spec: str, smp: Sample) -> None:
            prov_name, model = split_spec(spec)
            provider = provider_registry.get(prov_name)
            rec: Dict[str, Any] = {"sample": smp, "completion": None, "comparison": None, "skipped": None}
            async with sem:
                try:
                    if run["id"] in CANCELLED:
                        rec["skipped"] = "cancelled"
                        return
                    comp: Optional[Completion] = None
                    key = cache_key(spec, smp)
                    if cfg["use_cache"]:
                        hit = await asyncio.to_thread(storage.replay_cache_get, key)
                        if hit:
                            comp = Completion(**{k: v for k, v in hit.items() if k in Completion.__dataclass_fields__})
                            comp.cached = True
                            prog["cache_hits"] += 1
                    if comp is None:
                        est = 0.0 if provider.local else (pricing.calculate(model, smp.input_tokens or 500, (smp.output_tokens or 300) * 2) or 0.0)
                        if prog["spend_usd"] + est > cfg["max_spend_usd"]:
                            rec["skipped"] = "max_spend_usd reached"
                            return
                        if not provider.available():
                            rec["skipped"] = f"provider {prov_name} is not configured"
                            return
                        comp = await call_with_retries(provider, model, smp.messages, smp.params, rpm=cfg.get("rpm"))
                        if comp.error:
                            prog["errors"] += 1
                            rec["completion"] = comp
                            return
                        if provider.local:
                            comp.cost = 0.0
                        elif comp.cost is None:
                            comp.cost = pricing.calculate(model, comp.input_tokens, comp.output_tokens)
                        prog["spend_usd"] = round(prog["spend_usd"] + (comp.cost or 0.0), 8)
                        if cfg["use_cache"]:
                            await asyncio.to_thread(storage.replay_cache_put, key, {k: getattr(comp, k) for k in
                                                    ("text", "input_tokens", "output_tokens", "latency_ms", "cost", "raw_model")})
                    rec["completion"] = comp
                    try:
                        rec["comparison"] = await scorer.compare(smp, comp, cfg.get("scorer_config") or {})
                    except Exception as e:  # noqa: BLE001
                        rec["comparison"] = Comparison(None, "error", f"{type(e).__name__}: {e}"[:300])
                finally:
                    results[(label, spec)].append(rec)
                    prog["done"] += 1
                    await save()

        await asyncio.gather(*(one(label, spec, s) for (_f, label, chosen, _sk, _c) in plan for spec in cfg["candidates"] for s in chosen))

        steps_out = []
        lookback = float(cfg["lookback_days"])
        for f, label, chosen, skipped, calls in plan:
            base_costs = [s.cost if s.cost is not None else pricing.calculate(s.model, s.input_tokens, s.output_tokens) for s in chosen]
            base_costs = [c for c in base_costs if c is not None]
            base_lat = [s.latency_ms for s in chosen if s.latency_ms]
            models = Counter(s.model for s in chosen)
            baseline = {"model": models.most_common(1)[0][0] if models else None, "models": dict(models), "n": len(chosen),
                        "cost_per_call": round(statistics.mean(base_costs), 10) if base_costs else None,
                        "latency_ms": round(statistics.median(base_lat), 1) if base_lat else None,
                        "calls_in_window": calls, "monthly_calls_est": round(calls * 30 / max(lookback, 1e-9))}
            cands = []
            for spec in cfg["candidates"]:
                recs = results.get((label, spec), [])
                scored = [r for r in recs if r["comparison"] and r["comparison"].score is not None]
                comps = [r["completion"] for r in recs if r["completion"] and not r["completion"].error]
                vals = [r["comparison"].score for r in scored]
                costs = [c.cost for c in comps]
                price_known = bool(comps) and all(c is not None for c in costs)
                cpc = round(statistics.mean(costs), 10) if price_known else None
                lat = [c.latency_ms for c in comps if c.latency_ms and not c.cached] or [c.latency_ms for c in comps if c.latency_ms]
                bc = baseline["cost_per_call"]
                sav = None
                if cpc is not None and bc is not None:
                    sav = round((bc - cpc) / bc * 100, 1) if bc > 0 else (0.0 if cpc == 0 else -100.0)
                med_lat = round(statistics.median(lat), 1) if lat else None
                ldelta = round((med_lat - baseline["latency_ms"]) / baseline["latency_ms"] * 100, 1) if med_lat and baseline["latency_ms"] else None
                labels = Counter((r["comparison"].label or "?") for r in scored)
                order = sorted(scored, key=lambda r: r["comparison"].score)
                k = int(cfg["examples_per_candidate"])
                picks = (order[:max(1, k - 1)] + order[-1:]) if len(order) > k else order
                seen, examples = set(), []
                for r in picks:
                    s, c, cmp = r["sample"], r["completion"], r["comparison"]
                    if s.observation_id in seen:
                        continue
                    seen.add(s.observation_id)
                    user = next((m.get("content") for m in reversed(s.messages) if m.get("role") == "user"), "")
                    examples.append({"observation_id": s.observation_id, "trace_id": s.trace_id, "input": _clip(user, 700),
                                     "original": _clip(s.output_text, 1500), "candidate": _clip(c.text, 1500), "score": cmp.score,
                                     "label": cmp.label, "reason": cmp.reason, "original_latency_ms": s.latency_ms,
                                     "candidate_latency_ms": c.latency_ms, "candidate_cost": c.cost, "cached": c.cached})
                errs = [r["completion"].error for r in recs if r["completion"] and r["completion"].error]
                skips = Counter(r["skipped"] for r in recs if r["skipped"])
                cands.append({"candidate": spec, "provider": split_spec(spec)[0], "model": split_spec(spec)[1], "n": len(vals),
                              "attempted": len(recs), "errors": len(errs), "error_samples": errs[:3], "skipped": dict(skips),
                              "quality_mean": round(statistics.mean(vals), 4) if vals else None, "quality_ci": _ci(vals),
                              "labels": dict(labels), "price_known": price_known, "cost_per_call": cpc, "cost_savings_pct": sav,
                              "latency_ms": med_lat, "latency_delta_pct": ldelta,
                              "monthly_savings_usd": round((bc - cpc) * baseline["monthly_calls_est"], 6) if (cpc is not None and bc is not None) else None,
                              "scorers": dict(Counter((r["comparison"].metadata or {}).get("scorer", cfg["scorer"]) for r in scored)),
                              "examples": examples})
            rec = policy.recommend(label, baseline, cands, cfg.get("policy_config") or {}) if chosen else \
                {"step": label, "action": "insufficient_data", "from_model": None, "estimate": True,
                 "rationale": "No replayable generations found for this step" + (f" (skipped: {', '.join(f'{v}× {k}' for k, v in skipped.items())})" if skipped else ".")}
            steps_out.append({"step": label, "filter": f, "baseline": baseline, "candidates": cands, "recommendation": rec,
                              "skipped": skipped, "sampled": len(chosen)})
        switches = [s["recommendation"] for s in steps_out if s["recommendation"].get("action") == "switch"]
        run["results"] = {"steps": steps_out, "summary": {
            "steps": len(steps_out), "switches": len(switches),
            "monthly_savings_usd": round(sum(r.get("monthly_savings_usd") or 0 for r in switches), 6),
            "replays": prog["done"], "spend_usd": prog["spend_usd"], "cache_hits": prog["cache_hits"], "errors": prog["errors"],
            "redacted_inputs": any(s.redacted for p in plan for s in p[2]),
            "disclaimer": "Estimates from a sample of replays: list prices from the pricing table, quality relative to the original outputs as judged by the scorer."}}
        run["status"] = "cancelled" if run["id"] in CANCELLED else "completed"
    except Exception as e:  # noqa: BLE001
        logger.exception("optimizer run %s failed", run["id"])
        run["status"], run["error"] = "failed", f"{type(e).__name__}: {e}"[:1000]
    run["finished_at"] = now()
    CANCELLED.discard(run["id"])
    await save(True)
    return run
