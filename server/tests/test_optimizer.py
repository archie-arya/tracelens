import asyncio
import time
import uuid

import pytest
from conftest import DEMO, KEY

from tracelens_server import pricing
from tracelens_server.optimizer import Completion, Provider, register_provider
from tracelens_server.optimizer.base import Sample
from tracelens_server.optimizer.policies import QualityThreshold
from tracelens_server.optimizer.replay import SKIP_TOOL_OUTPUT, build_sample, normalize_messages
from tracelens_server.optimizer.scorers import JsonMatch, PairwiseJudge

CALLS = []


@register_provider
class FakeProvider(Provider):
    name = "fake"
    description = "test provider"
    default_rpm = 10_000

    async def complete(self, model, messages, params):
        CALLS.append((model, messages))
        text = "Paris" if model == "fake-good" else "Lyon, probably"
        return Completion(text=text, input_tokens=100, output_tokens=5)


pricing.register("fake-good", 0.01, 0.01)
pricing.register("fake-bad", 0.01, 0.01)
pricing.register("orig-model", 1.0, 4.0)


def _seed(client, n=6):
    now = time.time()
    batch = []
    for i in range(n):
        tid, gen = uuid.uuid4().hex, uuid.uuid4().hex
        batch += [
            {"type": "trace", "body": {"id": tid, "name": "qa", "tags": ["prod"], "start_time": now - 100 + i, "end_time": now - 99 + i}},
            {"type": "observation", "body": {"id": gen, "trace_id": tid, "type": "generation", "name": "answer", "model": "orig-model",
                                             "input": [{"role": "system", "content": "Be brief."}, {"role": "user", "content": f"Capital of France? ({i})"}],
                                             "output": "Paris", "usage": {"input": 100, "output": 5}, "start_time": now - 100 + i, "end_time": now - 99.5 + i}},
            {"type": "observation", "body": {"id": uuid.uuid4().hex, "trace_id": tid, "type": "tool", "name": "answer", "input": {"x": 1},
                                             "output": {"ok": True}, "start_time": now - 100 + i, "end_time": now - 99.9 + i}},
        ]
    tid = uuid.uuid4().hex
    batch += [{"type": "trace", "body": {"id": tid, "name": "qa", "start_time": now - 50}},
              {"type": "observation", "body": {"id": uuid.uuid4().hex, "trace_id": tid, "type": "generation", "name": "answer", "model": "orig-model",
                                               "input": [{"role": "user", "content": "look it up"}],
                                               "output": {"role": "assistant", "content": None, "tool_calls": [{"name": "search", "arguments": "{}"}]},
                                               "start_time": now - 50}}]
    r = client.post("/api/v1/ingest", json={"batch": batch}, headers=KEY)
    assert r.status_code == 200 and r.json()["errors"] == []


def _wait(client, rid, timeout=20):
    t0 = time.time()
    while time.time() - t0 < timeout:
        r = client.get(f"/api/v1/optimizer/runs/{rid}", headers=KEY).json()
        if r["status"] not in ("queued", "running"):
            return r
        time.sleep(0.1)
    raise AssertionError("run did not finish")


def test_replay_safety_rules():
    span = {"id": "1", "trace_id": "t", "type": "span", "input": "x", "output": "y"}
    assert build_sample(span, "s", {})[1] == "not an LLM generation"
    tool = {"id": "2", "trace_id": "t", "type": "generation", "input": "q", "output": {"tool_calls": [{"name": "rm_rf"}]}}
    assert "tool call" in build_sample(tool, "s", {})[1]
    err = {"id": "3", "trace_id": "t", "type": "generation", "input": "q", "output": "a", "level": "ERROR"}
    assert build_sample(err, "s", {})[0] is None
    thoughts = {"id": "5", "trace_id": "t", "type": "generation", "input": "q", "output": {"parts": [{"text": "thinking...", "thought": True}], "role": "model"}}
    assert build_sample(thoughts, "s", {})[1] == "no visible output text"
    vis = {"id": "6", "trace_id": "t", "type": "generation", "input": "q", "output": {"parts": [{"text": "hmm", "thought": True}, {"text": "42"}]}}
    assert build_sample(vis, "s", {})[0].output_text == "42"
    pii = {"id": "4", "trace_id": "t", "type": "generation", "input": [{"role": "user", "content": "mail bob@example.com"}], "output": "ok"}
    assert "bob@example.com" in build_sample(pii, "s", {"redact": False})[0].messages[0]["content"]
    s = build_sample(pii, "s", {"redact": True})[0]
    assert "[REDACTED_EMAIL]" in s.messages[0]["content"] and s.redacted


def test_normalize_message_shapes():
    assert normalize_messages("hi") == [{"role": "user", "content": "hi"}]
    assert normalize_messages({"system": "S", "messages": [{"role": "user", "content": [{"type": "text", "text": "q"}]}]}) == \
        [{"role": "system", "content": "S"}, {"role": "user", "content": "q"}]
    assert normalize_messages([{"role": "model", "parts": [{"text": "a"}]}]) == [{"role": "assistant", "content": "a"}]
    assert normalize_messages([{"role": "tool", "content": "x"}]) is None


def test_json_match_and_pairwise_judge():
    smp = Sample("o", "t", "s", "m", [{"role": "user", "content": "classify"}], {}, None, '{"category": "billing", "priority": "high"}')
    c = asyncio.run(JsonMatch().compare(smp, Completion(text='```json\n{"category":"Billing","priority":"low"}\n```'), {}))
    assert c.score == 0.5 and "priority" in c.reason

    from tracelens_server.evals import llm

    class FakeJudge(llm.JudgeLLM):
        def available(self):
            return True

        async def complete_json(self, system, prompt):
            first = prompt.split("RESPONSE 1:\n")[1].split("\n\nRESPONSE 2")[0]
            return ({"score_1": 0.6, "score_2": 0.9, "reason": "x"} if first == "cand" else {"score_1": 0.9, "score_2": 0.6, "reason": "x"}), "fake:judge"

    llm.set_judge(FakeJudge([]))
    try:
        smp2 = Sample("o2", "t", "s", "m", [{"role": "user", "content": "q"}], {}, "orig", "orig")
        c2 = asyncio.run(PairwiseJudge().compare(smp2, Completion(text="cand"), {}))
        assert c2.score == pytest.approx(0.7) and c2.label == "worse"  # position randomization is undone correctly
    finally:
        llm.set_judge(None)


def test_quality_threshold_policy():
    base = {"model": "a", "cost_per_call": 1.0}
    cands = [{"candidate": "x:cheap-bad", "n": 10, "quality_mean": 0.7, "quality_ci": [0.6, 0.8], "price_known": True, "cost_savings_pct": 90},
             {"candidate": "x:mid", "n": 10, "quality_mean": 0.97, "quality_ci": [0.9, 1.0], "price_known": True, "cost_savings_pct": 60},
             {"candidate": "x:few", "n": 2, "quality_mean": 1.0, "price_known": True, "cost_savings_pct": 99}]
    r = QualityThreshold().recommend("step", base, cands, {})
    assert r["action"] == "switch" and r["to_model"] == "x:mid" and r["confidence"] == "medium" and r["estimate"]
    r2 = QualityThreshold().recommend("step", base, cands[:1], {})
    assert r2["action"] == "keep"
    cands[1]["latency_delta_pct"] = 400.0
    r3 = QualityThreshold().recommend("step", base, cands, {"max_latency_increase_pct": 50})
    assert r3["action"] == "keep" and "slower" in r3["rationale"]


def test_optimizer_end_to_end(client):
    _seed(client)
    steps = client.get("/api/v1/optimizer/steps", headers=KEY).json()["data"]
    st = next(s for s in steps if s["name"] == "answer")
    assert st["step"] == "qa / answer" and st["calls"] == 7 and st["replayable"] == 6
    assert SKIP_TOOL_OUTPUT in st["skipped"]

    assert client.post("/api/v1/optimizer/runs", json={"steps": ["answer"], "candidates": ["fake:fake-good"]}).status_code in (401, 403)
    bad = client.post("/api/v1/optimizer/runs", json={"steps": ["answer"], "candidates": ["nope:x"]}, headers=KEY)
    assert bad.status_code == 422 and "unknown provider" in bad.text

    CALLS.clear()
    body = {"steps": ["qa / answer"], "candidates": ["fake:fake-good", "fake:fake-bad"], "scorer": "exact_match", "sample_size": 5}
    run = client.post("/api/v1/optimizer/runs", json=body, headers=KEY).json()
    done = _wait(client, run["id"])
    assert done["status"] == "completed", done.get("error")
    step = done["results"]["steps"][0]
    assert step["sampled"] == 5 and step["skipped"] == {SKIP_TOOL_OUTPUT: 1}
    good = next(c for c in step["candidates"] if c["model"] == "fake-good")
    badc = next(c for c in step["candidates"] if c["model"] == "fake-bad")
    assert good["quality_mean"] == 1.0 and badc["quality_mean"] == 0.0 and good["cost_savings_pct"] > 90
    assert good["examples"] and good["examples"][0]["original"] == "Paris"
    rec = step["recommendation"]
    assert rec["action"] == "switch" and rec["to_model"] == "fake:fake-good" and rec["estimate"] is True
    assert len(CALLS) == 10 and all(m[-1]["role"] == "user" for _, m in CALLS)
    assert done["results"]["summary"]["switches"] == 1

    # identical replays are served from the cache
    CALLS.clear()
    again = _wait(client, client.post("/api/v1/optimizer/runs", json=body, headers=KEY).json()["id"])
    assert again["progress"]["cache_hits"] == 10 and CALLS == []
    runs = client.get("/api/v1/optimizer/runs", headers=KEY).json()["data"]
    assert len(runs) == 2 and "results" not in runs[0]
    assert client.get(f"/api/v1/optimizer/runs/{run['id']}", headers=DEMO).status_code == 404  # other project
    assert client.delete(f"/api/v1/optimizer/runs/{run['id']}", headers=KEY).json()["ok"]


def test_spend_cap(client):
    _seed(client, 3)
    pricing.register("fake-pricey", 1000.0, 1000.0)
    run = client.post("/api/v1/optimizer/runs", json={"steps": ["answer"], "candidates": ["fake:fake-pricey"], "scorer": "exact_match",
                                                      "max_spend_usd": 0.01, "use_cache": False}, headers=KEY).json()
    done = _wait(client, run["id"])
    cand = done["results"]["steps"][0]["candidates"][0]
    assert cand["n"] == 0 and cand["skipped"] == {"max_spend_usd reached": 3}
    assert done["results"]["steps"][0]["recommendation"]["action"] == "insufficient_data"


def test_plugins_catalog(client):
    c = client.get("/api/v1/optimizer/plugins").json()
    names = {k: [x["name"] for x in v] for k, v in c.items() if isinstance(v, list)}
    assert {"gemini", "groq", "ollama", "openai", "litellm", "fake"} <= set(names["providers"])
    assert {"pairwise_judge", "json_match", "exact_match", "auto"} <= set(names["scorers"])
    assert {"quality_threshold", "budget", "weighted"} <= set(names["policies"])
