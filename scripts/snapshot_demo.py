"""Export the public demo project from a running server into static JSON for the GitHub Pages dashboard.

    python scripts/snapshot_demo.py http://localhost:8080 demo/snapshot
"""
import json
import os
import sys
import urllib.request

base = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "http://localhost:8080"
out = sys.argv[2] if len(sys.argv) > 2 else "demo/snapshot"
os.makedirs(f"{out}/traces", exist_ok=True)
os.makedirs(f"{out}/datasets", exist_ok=True)


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
print(f"snapshot: {len(traces)} traces -> {out}")
