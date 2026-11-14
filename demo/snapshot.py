"""Export the public demo project from a running server into static JSON for the GitHub Pages dashboard.

    python demo/snapshot.py http://localhost:8080 demo/snapshot
"""
import json
import os
import shutil
import sys
import urllib.request

base = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "http://localhost:8080"
out = sys.argv[2] if len(sys.argv) > 2 else "demo/snapshot"
for d in ("traces", "optimizer"):  # start clean so deleted traces and runs disappear from the demo
    shutil.rmtree(f"{out}/{d}", ignore_errors=True)
os.makedirs(f"{out}/traces", exist_ok=True)
os.makedirs(f"{out}/datasets", exist_ok=True)
os.makedirs(f"{out}/optimizer", exist_ok=True)


def get(path):
    with urllib.request.urlopen(base + path, timeout=60) as r:
        return json.loads(r.read())


def put(name, data):
    with open(f"{out}/{name}", "w") as f:
        json.dump(data, f, separators=(",", ":"), default=str)


put("health.json", get("/api/health"))
proj = get("/api/v1/project")
put("project.json", {**proj, "read_only": True})
for r in ("1h", "24h", "7d", "30d"):
    put(f"metrics-{r}.json", get(f"/api/v1/metrics?range={r}"))
traces = []
offset = 0
while True:
    page = get(f"/api/v1/traces?limit=500&offset={offset}")
    traces += page["data"]
    offset += 500
    if offset >= page["total"]:
        break
put("traces.json", {"data": traces, "total": len(traces)})
for t in traces:
    put(f"traces/{t['id']}.json", get(f"/api/v1/traces/{t['id']}"))
put("scores.json", get("/api/v1/scores?limit=1000"))
for name in ("sessions", "filters", "evaluators", "eval-rules", "datasets"):
    put(f"{name}.json", get(f"/api/v1/{name}" + ("?limit=500" if name == "sessions" else "")))
put("alert-rules.json", get("/api/v1/alerts/rules"))
put("alert-events.json", get("/api/v1/alerts/events"))
for d in get("/api/v1/datasets")["data"]:
    put(f"datasets/{d['id']}.json", get(f"/api/v1/datasets/{d['id']}"))
    put(f"datasets/{d['id']}-runs.json", get(f"/api/v1/datasets/{d['id']}/runs"))
put("optimizer-plugins.json", get("/api/v1/optimizer/plugins"))
put("optimizer-steps.json", get("/api/v1/optimizer/steps"))
runs = [r for r in get("/api/v1/optimizer/runs")["data"] if r["status"] == "completed"]
put("optimizer-runs.json", {"data": runs})
for r in runs:
    put(f"optimizer/{r['id']}.json", get(f"/api/v1/optimizer/runs/{r['id']}"))
print(f"snapshot: {len(runs)} optimizer runs")
print(f"snapshot: {len(traces)} traces -> {out}")
