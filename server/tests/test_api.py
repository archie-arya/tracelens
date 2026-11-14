import time
import uuid

from conftest import DEMO, KEY


def _ev(t, **b):
    return {"type": t, "body": b}


def _seed(client, n=3, model="gpt-4o-mini", user="u1", error_on=None, name="chat"):
    ids = []
    now = time.time()
    batch = []
    for i in range(n):
        tid = uuid.uuid4().hex
        ids.append(tid)
        root, gen, ret = uuid.uuid4().hex, uuid.uuid4().hex, uuid.uuid4().hex
        batch += [
            _ev("trace", id=tid, name=name, user_id=user, session_id="s1", tags=["prod", f"v{i}"], input={"q": f"question {i}"},
                output=f"answer {i}", start_time=now - 10 + i, end_time=now - 9 + i, metadata={"k": i}),
            _ev("observation", id=root, trace_id=tid, type="span", name="pipeline", start_time=now - 10 + i, end_time=now - 9 + i),
            _ev("observation", id=ret, trace_id=tid, parent_id=root, type="retrieval", name="search", output=["Paris is the capital of France."],
                start_time=now - 10 + i, end_time=now - 9.8 + i),
            _ev("observation", id=gen, trace_id=tid, parent_id=root, type="generation", name="llm", model=model,
                usage={"input": 1000, "output": 100}, start_time=now - 9.8 + i, end_time=now - 9.1 + i,
                level="ERROR" if error_on == i else "DEFAULT", output="answer"),
        ]
    r = client.post("/api/v1/ingest", json={"batch": batch}, headers=KEY)
    assert r.status_code == 200, r.text
    assert r.json()["errors"] == []
    return ids


def test_health(client):
    assert client.get("/api/health").json()["status"] == "ok"


def test_auth_required_for_write(client):
    assert client.post("/api/v1/ingest", json={"batch": []}).status_code == 401
    assert client.post("/api/v1/ingest", json={"batch": []}, headers={"Authorization": "Bearer nope"}).status_code == 401


def test_ingest_and_query(client):
    ids = _seed(client, 3, error_on=1)
    r = client.get("/api/v1/traces", headers=KEY).json()
    assert r["total"] == 3
    t = r["data"][0]
    assert t["observation_count"] == 3 and t["total_tokens"] == 1100
    assert abs(t["total_cost"] - (1000 * 0.15 + 100 * 0.6) / 1e6) < 1e-9
    assert t["models"] == ["gpt-4o-mini"] and t["latency_ms"] > 900
    full = client.get(f"/api/v1/traces/{ids[1]}", headers=KEY).json()
    assert full["status"] == "error" and len(full["observations"]) == 3
    # filters
    assert client.get("/api/v1/traces?status=error", headers=KEY).json()["total"] == 1
    assert client.get("/api/v1/traces?tag=v2", headers=KEY).json()["total"] == 1
    assert client.get("/api/v1/traces?q=question 2", headers=KEY).json()["total"] == 1
    assert client.get("/api/v1/traces?model=gpt-4o-mini&user_id=u1", headers=KEY).json()["total"] == 3
    assert client.get("/api/v1/traces?order_by=cost&limit=1", headers=KEY).json()["data"][0]["total_cost"] > 0
    assert client.get("/api/v1/sessions", headers=KEY).json()["data"][0]["traces"] == 3
    assert client.get("/api/v1/observations?type=generation", headers=KEY).json()["total"] == 3


def test_upsert_merges(client):
    tid = uuid.uuid4().hex
    client.post("/api/v1/ingest", json={"batch": [_ev("trace", id=tid, name="x", tags=["a"], metadata={"a": 1})]}, headers=KEY)
    client.post("/api/v1/ingest", json={"batch": [_ev("trace", id=tid, output="done", tags=["b"], metadata={"b": 2})]}, headers=KEY)
    t = client.get(f"/api/v1/traces/{tid}", headers=KEY).json()
    assert t["name"] == "x" and t["output"] == "done" and set(t["tags"]) == {"a", "b"} and t["metadata"] == {"a": 1, "b": 2}


def test_observation_only_creates_trace(client):
    tid, oid = uuid.uuid4().hex, uuid.uuid4().hex
    client.post("/api/v1/ingest", json={"batch": [_ev("generation", id=oid, traceId=tid, name="solo", model="claude-3-5-haiku",
                                                      input="hi", output="yo", usage={"input_tokens": 10, "output_tokens": 5},
                                                      startTime="2026-01-01T00:00:00Z", endTime="2026-01-01T00:00:01.5Z")]}, headers=KEY)
    t = client.get(f"/api/v1/traces/{tid}", headers=KEY).json()
    assert t["name"] == "solo" and t["input"] == "hi" and t["output"] == "yo" and t["latency_ms"] == 1500
    assert t["observations"][0]["type"] == "generation" and t["total_cost"] > 0


def test_project_isolation_and_demo_readonly(client):
    _seed(client, 2)
    # anonymous reads go to the (empty) demo project
    assert client.get("/api/v1/traces").json()["total"] == 0
    assert client.get("/api/v1/project").json()["read_only"] is True
    _seed_demo = client.post("/api/v1/ingest", json={"batch": [_ev("trace", id="d1", name="demo", start_time=time.time())]}, headers=DEMO)
    assert _seed_demo.status_code == 200
    assert client.get("/api/v1/traces").json()["total"] == 1
    # a new project sees nothing from others
    r = client.post("/api/v1/projects", json={"name": "Mine"}).json()
    mine = {"Authorization": f"Bearer {r['api_key']}"}
    assert client.get("/api/v1/traces", headers=mine).json()["total"] == 0
    assert client.get("/api/v1/project", headers=mine).json()["read_only"] is False
    # keys
    k = client.post("/api/v1/keys", json={"name": "ci"}, headers=mine).json()
    assert k["api_key"].startswith("tl-sk-")
    assert len(client.get("/api/v1/keys", headers=mine).json()["data"]) == 2
    client.delete(f"/api/v1/keys/{k['id']}", headers=mine)
    assert client.get("/api/v1/traces", headers={"Authorization": f"Bearer {k['api_key']}"}).status_code == 401


def test_metrics(client):
    _seed(client, 4, error_on=0)
    _seed(client, 2, model="gemini-2.5-flash", user="u2", name="agent")
    m = client.get("/api/v1/metrics?range=24h", headers=KEY).json()
    assert m["totals"]["traces"] == 6 and m["totals"]["errors"] == 1
    assert {x["model"] for x in m["by_model"]} == {"gpt-4o-mini", "gemini-2.5-flash"}
    assert m["totals"]["p95_latency_ms"] > 0
    assert sum(b["traces"] for b in m["timeseries"]) == 6
    assert {u["user_id"] for u in m["by_user"]} == {"u1", "u2"}
    f = client.get("/api/v1/filters", headers=KEY).json()
    assert "agent" in f["names"] and "prod" in f["tags"]


def test_scores_and_heuristic_evals(client):
    ids = _seed(client, 2)
    client.post("/api/v1/scores", json={"trace_id": ids[0], "name": "user_feedback", "value": 1, "comment": "great"}, headers=KEY)
    r = client.post("/api/v1/evals/run", json={"evaluator": "length", "config": {"min": 1, "max": 100}, "trace_ids": ids}, headers=KEY).json()
    for _ in range(50):
        job = client.get(f"/api/v1/evals/jobs/{r['id']}", headers=KEY).json()
        if job["status"] == "completed":
            break
        time.sleep(0.05)
    assert job["status"] == "completed" and job["avg_score"] == 1.0
    t = client.get(f"/api/v1/traces/{ids[0]}", headers=KEY).json()
    assert {s["name"] for s in t["scores"]} == {"user_feedback", "length"}
    assert client.post("/api/v1/evals/test", json={"evaluator": "json_valid", "output": '{"a": 1}', "config": {"required_keys": ["a"]}}, headers=KEY).json()["score"] == 1.0
    assert client.post("/api/v1/evals/test", json={"evaluator": "regex", "output": "call 555", "config": {"pattern": "\\d+"}}, headers=KEY).json()["score"] == 1.0
    names = {e["name"] for e in client.get("/api/v1/evaluators").json()["data"]}
    assert {"llm_relevance", "llm_faithfulness", "llm_toxicity", "llm_rubric", "json_valid", "regex", "length", "similarity"} <= names


def test_llm_judge_with_fake(client, monkeypatch):
    from tracelens_server.evals import llm

    class Fake(llm.JudgeLLM):
        def available(self):
            return True

        async def complete_json(self, system, prompt):
            assert "Paris is the capital" in prompt  # retrieval context is passed to faithfulness
            return {"score": 0.9, "label": "faithful", "reason": "supported"}, "fake:judge"

    llm.set_judge(Fake([]))
    try:
        ids = _seed(client, 1)
        s = client.post(f"/api/v1/traces/{ids[0]}/evaluate", json={"evaluator": "llm_faithfulness"}, headers=KEY).json()
        assert s["value"] == 0.9 and s["name"] == "faithfulness" and s["metadata"]["judge_model"] == "fake:judge"
    finally:
        llm.set_judge(None)


def test_eval_rules_online(client, monkeypatch):
    r = client.post("/api/v1/eval-rules", json={"evaluator": "contains", "config": {"keywords": ["answer"]}, "filter": {"tags": ["prod"]}}, headers=KEY)
    assert r.status_code == 200
    ids = _seed(client, 2)
    time.sleep(0.3)
    t = client.get(f"/api/v1/traces/{ids[0]}", headers=KEY).json()
    assert any(s["name"] == "contains" and s["value"] == 1.0 for s in t["scores"])
    # idempotent: re-ingest doesn't duplicate
    client.post("/api/v1/ingest", json={"batch": [_ev("trace", id=ids[0], end_time=time.time())]}, headers=KEY)
    time.sleep(0.2)
    t = client.get(f"/api/v1/traces/{ids[0]}", headers=KEY).json()
    assert len([s for s in t["scores"] if s["name"] == "contains"]) == 1


def test_datasets_and_runs(client):
    ds = client.post("/api/v1/datasets", json={"name": "qa"}, headers=KEY).json()
    items = client.post(f"/api/v1/datasets/{ds['id']}/items", json={"items": [{"input": "capital of France?", "expected_output": "Paris"},
                                                                            {"input": "2+2", "expected_output": "4"}]}, headers=KEY).json()["data"]
    for run, answers in (("baseline", ["Paris", "5"]), ("improved", ["Paris", "4"])):
        links = []
        for it, ans in zip(items, answers):
            tid = uuid.uuid4().hex
            client.post("/api/v1/ingest", json={"batch": [_ev("trace", id=tid, name="exp", input=it["input"], output=ans, start_time=time.time(), end_time=time.time() + 0.5)]}, headers=KEY)
            links.append({"item_id": it["id"], "trace_id": tid})
        res = client.post(f"/api/v1/datasets/{ds['id']}/runs", json={"run_name": run, "links": links, "evaluators": [{"evaluator": "exact_match"}]}, headers=KEY).json()
        assert res["run_name"] == run
    runs = client.get(f"/api/v1/datasets/{ds['id']}/runs", headers=KEY).json()
    by = {r["run_name"]: r for r in runs["runs"]}
    assert by["baseline"]["avg_scores"]["exact_match"] == 0.5 and by["improved"]["avg_scores"]["exact_match"] == 1.0
    assert len(runs["matrix"]) == 2
    assert client.get("/api/v1/datasets/by-name/qa", headers=KEY).json()["items"][0]["expected_output"] == "Paris"


def test_alerts_webhook(client):
    import http.server
    import json as _j
    import threading

    got = []

    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            got.append(_j.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    _seed(client, 3, error_on=0)
    rule = client.post("/api/v1/alerts/rules", json={"metric": "error_rate", "comparator": "gt", "threshold": 0.2, "window_minutes": 60,
                                                     "webhook_url": f"http://127.0.0.1:{srv.server_port}/hook"}, headers=KEY).json()
    r = client.post(f"/api/v1/alerts/rules/{rule['id']}/check", headers=KEY).json()
    assert abs(r["value"] - 1 / 3) < 1e-6 and r["event"]["delivered"] is True
    assert got and "error_rate" in got[0]["text"]
    # cooldown prevents re-trigger
    r2 = client.post(f"/api/v1/alerts/rules/{rule['id']}/check", headers=KEY).json()
    assert r2["event"] is None
    assert len(client.get("/api/v1/alerts/events", headers=KEY).json()["data"]) == 1
    srv.shutdown()


def test_langfuse_style_alias(client):
    tid = uuid.uuid4().hex
    batch = [{"type": "trace-create", "body": {"id": tid, "name": "lf", "userId": "x"}},
             {"type": "generation-create", "body": {"id": "g1" + tid, "traceId": tid, "name": "gen", "model": "gpt-4o", "usage": {"promptTokens": 5, "completionTokens": 5}}}]
    assert client.post("/api/public/ingestion", json={"batch": batch}, headers=KEY).status_code == 200
    t = client.get(f"/api/v1/traces/{tid}", headers=KEY).json()
    assert t["user_id"] == "x" and t["total_tokens"] == 10
