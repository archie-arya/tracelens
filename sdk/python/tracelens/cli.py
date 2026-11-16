"""``tracelens`` command line: ``tracelens optimize``, ``tracelens steps``, ``tracelens runs``.

Uses TRACELENS_API_KEY / TRACELENS_HOST (or --api-key / --host)."""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
from typing import Any, Dict, List

from .client import TraceLens


def _client(a: argparse.Namespace) -> TraceLens:
    key = a.api_key or os.environ.get("TRACELENS_API_KEY")
    if not key:
        sys.exit("error: set TRACELENS_API_KEY or pass --api-key")
    c = TraceLens(api_key=key, host=a.host or os.environ.get("TRACELENS_HOST"), enabled=False)
    if getattr(a, "project", None):
        p = c.api("GET", "/api/v1/project")
        if a.project not in (p.get("id"), p.get("slug"), p.get("name")):
            sys.exit(f"error: this API key belongs to project {p.get('slug')!r}, not {a.project!r}")
    return c


def _fmt_pct(v: Any) -> str:
    return "  n/a" if v is None else f"{v:+.0f}%"


def _table(rows: List[List[str]]) -> str:
    w = [max(len(str(r[i])) for r in rows) for i in range(len(rows[0]))]
    return "\n".join("  ".join(str(c).ljust(w[i]) for i, c in enumerate(r)) for r in rows)


def _print_run(run: Dict[str, Any]) -> None:
    print(f"\nrun {run['id']}  status={run['status']}  spend=${run['progress'].get('spend_usd', 0):.4f}  "
          f"cache_hits={run['progress'].get('cache_hits', 0)}  errors={run['progress'].get('errors', 0)}")
    if run.get("error"):
        print("error:", run["error"])
    for s in (run.get("results") or {}).get("steps", []):
        b = s["baseline"]
        print(f"\n■ {s['step']}  (baseline {b.get('model')}, {s['sampled']} sampled"
              + (f", skipped: {s['skipped']}" if s.get("skipped") else "") + ")")
        tools = bool(s.get("tool_step"))
        rows = [["candidate", "n", "quality", "95% CI", "cost Δ", "latency Δ", "errors"] + (["tool agreement"] if tools else [])]
        for c in s["candidates"]:
            ci = c.get("quality_ci")
            ta = c.get("tool_agreement")
            rows.append([c["candidate"], c["n"], "n/a" if c["quality_mean"] is None else f"{c['quality_mean']:.2f}",
                         "" if not ci else f"{ci[0]:.2f}-{ci[1]:.2f}", _fmt_pct(None if c.get("cost_savings_pct") is None else -c["cost_savings_pct"]),
                         _fmt_pct(c.get("latency_delta_pct")), c.get("errors", 0)] + ([("n/a" if ta is None else f"{ta * 100:.0f}%")] if tools else []))
        print(_table(rows))
        for c in s["candidates"] if tools else []:
            for m in (c.get("tool_mismatches") or [])[:2]:
                print(f"  ✗ {c['candidate']}: {' → '.join(m['candidate'])}  (original: {' → '.join(m['original'])})")
        r = s["recommendation"]
        print(f"→ {r['action'].upper()}: {r.get('rationale', '')}" + (f"  [confidence: {r.get('confidence')}, n={r.get('n')}]" if r.get("confidence") else ""))
    if run.get("results"):
        print("\n" + run["results"]["summary"]["disclaimer"])


def cmd_optimize(a: argparse.Namespace) -> None:
    from . import optimizer
    c = _client(a)
    opts = {"sample_size": a.sample_size, "scorer": a.scorer, "policy": a.policy, "sampler": a.sampler, "max_spend_usd": a.max_spend,
            "redact": True if a.redact else None, "name": a.name, "concurrency": a.concurrency,
            "allow_tool_steps": True if a.allow_tool_steps else None,
            "scorer_config": json.loads(a.scorer_config) if a.scorer_config else None,
            "policy_config": json.loads(a.policy_config) if a.policy_config else None}
    run = optimizer.start(a.step, [x for c_ in a.candidates for x in c_.split(",") if x], client=c, **opts)
    print(f"started {run['id']} ({run['name']})")
    if a.no_wait:
        return
    last = [-1]

    def prog(r: Dict[str, Any]) -> None:
        p = r["progress"]
        if p.get("done") != last[0]:
            last[0] = p.get("done")
            print(f"  {p.get('done', 0)}/{p.get('total', 0)} replays  spend=${p.get('spend_usd', 0):.4f}", file=sys.stderr)

    run = optimizer.wait(run["id"], client=c, on_progress=prog, poll=2)
    if a.json:
        print(json.dumps(run, indent=2))
    else:
        _print_run(run)


def cmd_steps(a: argparse.Namespace) -> None:
    from . import optimizer
    rows = [["step", "calls", "replayable", "+ with tools", "models", "avg cost", "est. monthly"]]
    for s in optimizer.list_steps(a.lookback_days, client=_client(a)):
        rows.append([s["step"], s["calls"], s["replayable"], s.get("replayable_with_tools", s["replayable"]), ",".join(s["models"]),
                     f"${s['avg_cost'] or 0:.6f}", f"${s['monthly_cost_est']:.4f}"])
    print(_table(rows))


def cmd_runs(a: argparse.Namespace) -> None:
    from . import optimizer
    c = _client(a)
    if a.run_id:
        _print_run(optimizer.get_run(a.run_id, client=c))
        return
    for r in c.api("GET", "/api/v1/optimizer/runs")["data"]:
        print(r["id"], r["status"], r["name"])


def main(argv: Any = None) -> None:
    p = argparse.ArgumentParser("tracelens", description="TraceLens command line")
    p.add_argument("--api-key")
    p.add_argument("--host")
    sub = p.add_subparsers(dest="cmd", required=True)

    o = sub.add_parser("optimize", help="replay a step's real traffic on cheaper models and recommend a switch")
    o.add_argument("--project", help="project id/slug/name to double-check the key against")
    o.add_argument("--step", action="append", required=True, help="observation name or 'trace / parent / name' path (repeatable)")
    o.add_argument("--candidates", action="append", required=True, help="provider:model[,provider:model...] (repeatable)")
    o.add_argument("--sample-size", type=int)
    o.add_argument("--sampler")
    o.add_argument("--scorer", help="auto | tool_call_match | pairwise_judge | json_match | exact_match | similarity | eval:<evaluator>")
    o.add_argument("--scorer-config", help='JSON, e.g. \'{"args_mode": "strict"}\'')
    o.add_argument("--allow-tool-steps", action="store_true",
                   help="also replay tool-calling steps (one call per step with the recorded tool results; tools are never executed)")
    o.add_argument("--policy", help="quality_threshold | budget | weighted | <plugin>")
    o.add_argument("--policy-config", help='JSON, e.g. \'{"max_quality_drop": 0.03}\'')
    o.add_argument("--max-spend", type=float, help="USD cap for replay calls (default 1.0)")
    o.add_argument("--concurrency", type=int)
    o.add_argument("--redact", action="store_true", help="redact PII in prompts before replaying")
    o.add_argument("--name")
    o.add_argument("--no-wait", action="store_true")
    o.add_argument("--json", action="store_true")
    o.set_defaults(fn=cmd_optimize)

    s = sub.add_parser("steps", help="list LLM steps with volume and cost")
    s.add_argument("--lookback-days", type=float, default=30)
    s.set_defaults(fn=cmd_steps, project=None)

    r = sub.add_parser("runs", help="list optimizer runs, or show one")
    r.add_argument("run_id", nargs="?")
    r.set_defaults(fn=cmd_runs, project=None)

    a = p.parse_args(argv)
    try:
        a.fn(a)
    except urllib.error.HTTPError as e:
        sys.exit(f"error: HTTP {e.code}: {e.read().decode(errors='replace')[:500]}")
    except urllib.error.URLError as e:
        sys.exit(f"error: cannot reach the TraceLens server: {e.reason}")


if __name__ == "__main__":
    main()
