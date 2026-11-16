"""Tool-calling steps: capture shapes, replay gating, the tool_call_match scorer, trajectories and the policy gate."""
import asyncio
import json
import time
import uuid

import httpx
import pytest
from conftest import KEY

from tracelens_server import pricing
from tracelens_server.optimizer import Completion, Provider, register_provider
from tracelens_server.optimizer.policies import QualityThreshold
from tracelens_server.optimizer.providers import GeminiProvider, OpenAIProvider, gemini_schema, openai_messages
from tracelens_server.optimizer.replay import SKIP_TOOL_INPUT, SKIP_TOOL_OUTPUT, build_sample
from tracelens_server.optimizer.scorers import Auto, ToolCallMatch
from tracelens_server.optimizer.tools import (args_equal, compare_tool_calls, extract_tool_calls, normalize_tool_messages,
                                              tool_definitions)

TOOLS = [{"name": "get_account", "description": "Look up a customer", "parameters": {"type": "object", "properties": {"customer_id": {"type": "string"}}, "required": ["customer_id"]}},
         {"name": "calculator", "description": "Evaluate arithmetic", "parameters": {"type": "object", "properties": {"expression": {"type": "string"}}}}]
EXECUTED = []  # any tool "execution" would land here; it must stay empty


def get_account(customer_id):  # the real tool: the optimizer must never call it
    EXECUTED.append(customer_id)
    return {"plan": "Pro"}


def call(name, **args):
    return {"id": f"c_{name}", "name": name, "arguments": args}


# ---------------------------------------------------------------- comparison

def test_compare_identical_and_no_calls():
    r = compare_tool_calls([call("get_account", customer_id="cus_1")], [call("get_account", customer_id="cus_1")])
    assert r["agreement"] and r["score"] == 1.0 and r["decision"] == 1.0
    r = compare_tool_calls([], [])
    assert r["agreement"] and r["label"] == "no_call_match"


def test_compare_decision_mismatch_both_ways():
    r = compare_tool_calls([call("get_account", customer_id="cus_1")], [])
    assert r["decision"] == 0 and r["score"] == 0 and not r["agreement"] and "without calling" in r["reason"]
    r = compare_tool_calls([], [call("calculator", expression="1+1")])
    assert r["label"] == "decision_mismatch" and "did not need" in r["reason"]


def test_compare_order_names_and_extra_calls():
    a, b = call("get_account", customer_id="x"), call("calculator", expression="1+1")
    r = compare_tool_calls([a, b], [b, a])
    assert r["names"] == 1.0 and r["order"] == 0.5 and r["arguments"] == 1.0 and not r["agreement"]
    assert any(d["issue"] == "order" for d in r["diffs"])
    r = compare_tool_calls([a], [a, b])
    assert r["names"] == pytest.approx(2 / 3, abs=1e-3) and not r["agreement"] and r["diffs"][0]["issue"] == "extra"
    r = compare_tool_calls([a, b], [a])
    assert r["diffs"][0] == {"name": "calculator", "issue": "missing", "original": {"expression": "1+1"}}
    r = compare_tool_calls([a], [call("search_docs", query="x")])
    assert r["names"] == 0 and r["arguments"] == 0


def test_argument_modes():
    o = [{"name": "f", "arguments": '{"a": 1, "b": "Mumbai"}'}]  # OpenAI sends a JSON string
    c = [{"name": "f", "arguments": {"b": " mumbai ", "a": "1"}}]
    assert compare_tool_calls(o, c, "lenient")["agreement"]
    strict = compare_tool_calls(o, c, "strict")
    assert not strict["agreement"] and strict["arguments"] == 0 and strict["arguments_exact"] == 0
    assert compare_tool_calls(o, [{"name": "f", "arguments": {"b": "Mumbai", "a": 1}}], "strict")["agreement"]  # key order is irrelevant
    part = compare_tool_calls(o, [{"name": "f", "arguments": {"a": 1, "b": "Delhi"}}], "partial")
    assert part["arguments_partial"] == 0.5 and not part["agreement"]
    assert args_equal({"x": None, "y": 2.0}, {"y": 2}) and not args_equal({"x": 1}, {"x": 2})
    assert args_equal("not json", "not json") and not args_equal("{bad", {"a": 1})
    assert args_equal({"n": "1,000"}, {"n": 1000}) and args_equal({"ok": "true"}, {"ok": True})
    q1, q2 = [call("search_docs", query="Pro plan price", k=2)], [call("search_docs", query="pricing of Pro", k=2)]
    assert not compare_tool_calls(q1, q2)["agreement"]
    assert compare_tool_calls(q1, q2, ignore_args=["query"])["agreement"] and compare_tool_calls(q1, q2, ignore_args=["search_docs.query"])["agreement"]
    assert not compare_tool_calls(q1, [call("search_docs", query="x", k=5)], ignore_args=["query"])["agreement"]


# ---------------------------------------------------------------- shapes

def test_extract_tool_calls_shapes():
    sdk = {"role": "assistant", "content": None, "tool_calls": [{"id": "1", "name": "get_account", "arguments": {"customer_id": "c"}}]}
    wire = {"choices": [{"message": {"tool_calls": [{"id": "1", "type": "function", "function": {"name": "get_account", "arguments": '{"customer_id": "c"}'}}]}}]}
    anth = [{"type": "text", "text": "let me check"}, {"type": "tool_use", "id": "1", "name": "get_account", "input": {"customer_id": "c"}}]
    gem = {"role": "model", "parts": [{"functionCall": {"name": "get_account", "args": {"customer_id": "c"}}}]}
    v01 = {"content": "", "tool_calls": [{"name": "get_account", "input": {"customer_id": "c"}}]}  # SDK 0.1 Anthropic shape
    for out in (sdk, wire, anth, gem, v01):
        calls = extract_tool_calls(out)
        assert [(c["name"], c["arguments"]) for c in calls] == [("get_account", {"customer_id": "c"})], out
    assert extract_tool_calls("plain answer") == [] and extract_tool_calls({"content": "hi"}) == []


def test_normalize_tool_messages_openai_anthropic_gemini():
    oa = [{"role": "user", "content": "q"},
          {"role": "assistant", "content": "", "tool_calls": [{"id": "t1", "type": "function", "function": {"name": "get_account", "arguments": '{"customer_id":"c"}'}}]},
          {"role": "tool", "tool_call_id": "t1", "content": '{"plan": "Pro"}'}]
    an = [{"role": "user", "content": "q"},
          {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "get_account", "input": {"customer_id": "c"}}]},
          {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": '{"plan": "Pro"}'}]}]
    ge = [{"role": "user", "parts": [{"text": "q"}]},
          {"role": "model", "parts": [{"function_call": {"name": "get_account", "args": {"customer_id": "c"}}}]},
          {"role": "user", "parts": [{"function_response": {"name": "get_account", "response": {"plan": "Pro"}}}]}]
    for msgs in (oa, an, ge):
        n = normalize_tool_messages(msgs)
        assert [m["role"] for m in n] == ["user", "assistant", "tool"], n
        assert n[1]["tool_calls"][0]["name"] == "get_account" and n[1]["tool_calls"][0]["arguments"] == {"customer_id": "c"}
        assert n[2]["name"] == "get_account" and json.loads(n[2]["content"]) == {"plan": "Pro"}
        assert n[2]["tool_call_id"] == n[1]["tool_calls"][0]["id"]


def test_tool_definitions_sources():
    assert tool_definitions({"metadata": {"tool_definitions": TOOLS}})[0]["name"] == "get_account"
    wire = [{"type": "function", "function": TOOLS[1]}]
    assert tool_definitions({"model_parameters": {"tools": wire}})[0]["name"] == "calculator"
    assert tool_definitions({"input": {"messages": [], "tools": [{"name": "x", "input_schema": {"type": "object"}}]}})[0]["parameters"] == {"type": "object"}
    assert tool_definitions({"metadata": {"tools": ["get_account"]}}) == []  # SDK 0.1 recorded names only


# ---------------------------------------------------------------- gating

def _obs(output, input_=None, tools=TOOLS, **kw):
    md = {"tool_definitions": tools, "tools": [t["name"] for t in tools]} if tools else {}
    return {"id": uuid.uuid4().hex, "trace_id": "t", "type": "generation", "model": "orig-model", "metadata": md,
            "input": input_ or [{"role": "user", "content": "plan of cus_1?"}], "output": output, **kw}


def test_tool_steps_are_opt_in():
    first = _obs({"role": "assistant", "content": None, "tool_calls": [call("get_account", customer_id="cus_1")]})
    assert build_sample(first, "s", {}) == (None, SKIP_TOOL_OUTPUT)
    history = [{"role": "user", "content": "q"}, {"role": "assistant", "tool_calls": [call("get_account", customer_id="cus_1")]},
               {"role": "tool", "tool_call_id": "c_get_account", "content": '{"plan": "Pro"}'}]
    final = _obs("They are on Pro.", history)
    assert build_sample(final, "s", {}) == (None, SKIP_TOOL_INPUT)

    s, why = build_sample(first, "s", {"allow_tool_steps": True})
    assert why is None and s.tool_step and s.tools == TOOLS and s.tool_calls[0]["arguments"] == {"customer_id": "cus_1"}
    s2, _ = build_sample(final, "s", {"allow_tool_steps": True})
    assert s2.tool_step and s2.tool_calls == [] and s2.output_text == "They are on Pro." and s2.messages[-1]["role"] == "tool"

    no_defs = _obs({"tool_calls": [call("get_account", customer_id="1")]}, tools=None)
    no_defs["metadata"] = {"tools": ["get_account"]}
    assert "not recorded" in build_sample(no_defs, "s", {"allow_tool_steps": True})[1]
    builtin = _obs({"tool_calls": [call("web_search", q="x")]}, tools=[{"name": "web_search", "builtin": True}])
    assert "built-in" in build_sample(builtin, "s", {"allow_tool_steps": True})[1]
    # offered tools, answered in text, names only (SDK 0.1): still replayable as a plain prompt
    plain = _obs("hello", tools=None)
    plain["metadata"] = {"tools": ["get_account"]}
    assert build_sample(plain, "s", {"allow_tool_steps": True})[0].tool_step is False


def test_redaction_covers_tool_results_and_args():
    history = [{"role": "user", "content": "q"}, {"role": "assistant", "tool_calls": [call("lookup", email="bob@example.com")]},
               {"role": "tool", "tool_call_id": "c_lookup", "content": '{"email": "bob@example.com"}'}]
    s, _ = build_sample(_obs({"tool_calls": [call("send", to="bob@example.com")]}, history), "s", {"allow_tool_steps": True, "redact": True})
    assert "bob@example.com" not in json.dumps(s.messages) and "bob@example.com" not in json.dumps(s.tool_calls)


# ---------------------------------------------------------------- scorer

def _sample(tool_calls, text=""):
    s, _ = build_sample(_obs({"role": "assistant", "content": text or None, "tool_calls": tool_calls} if tool_calls else text), "s", {"allow_tool_steps": True})
    return s


def test_tool_call_match_scorer():
    sc = ToolCallMatch()
    smp = _sample([call("get_account", customer_id="cus_1")])
    assert Auto().pick(smp).name == "tool_call_match"
    good = asyncio.run(sc.compare(smp, Completion(tool_calls=[{"name": "get_account", "arguments": '{"customer_id": "CUS_1"}'}]), {}))
    assert good.score == 1.0 and good.metadata["agreement"]
    strict = asyncio.run(sc.compare(smp, Completion(tool_calls=[{"name": "get_account", "arguments": {"customer_id": "CUS_1"}}]), {"args_mode": "strict"}))
    assert strict.score == 0.5 and strict.label == "partial" and "cus_1" in strict.reason.lower()
    none = asyncio.run(sc.compare(smp, Completion(text="I can't look that up", tool_calls=[]), {}))
    assert none.score == 0 and none.label == "decision_mismatch"
    # final answer turn: both answered in text -> the text scorer decides
    fin = _sample([], "They are on the Pro plan.")
    t = asyncio.run(sc.compare(fin, Completion(text="They are on the Pro plan.", tool_calls=[]), {"text_scorer": "similarity"}))
    assert t.score == 1.0 and t.metadata["text_scorer"] == "similarity"
    # blend: same call, different preamble text
    smp2 = _sample([call("get_account", customer_id="cus_1")], "Checking the account")
    b = asyncio.run(sc.compare(smp2, Completion(text="zzz", tool_calls=[call("get_account", customer_id="cus_1")]), {"judge_weight": 0.5, "text_scorer": "similarity"}))
    assert b.score == 0.5 and b.metadata["text_score"] == 0.0


def test_policy_requires_tool_agreement():
    base = {"model": "a", "cost_per_call": 1.0}
    c = {"candidate": "x:cheap", "n": 10, "quality_mean": 0.97, "quality_ci": [0.9, 1.0], "price_known": True, "cost_savings_pct": 80,
         "tool_agreement": 0.8, "tool_n": 10, "decision_agreement": 0.9}
    r = QualityThreshold().recommend("s", base, [c], {})
    assert r["action"] == "keep" and "same tool calls in only 80%" in r["rationale"] and r["tool_agreement"] == 0.8
    assert QualityThreshold().recommend("s", base, [c], {"min_tool_agreement": 0.75})["action"] == "switch"
    assert QualityThreshold().recommend("s", base, [c], {"min_tool_agreement": None})["action"] == "switch"
    ok = QualityThreshold().recommend("s", base, [{**c, "tool_agreement": 1.0}], {})
    assert ok["action"] == "switch" and "same tool calls in 100% of them" in ok["rationale"]
    assert QualityThreshold().recommend("s", base, [{**c, "tool_agreement": None}], {})["action"] == "switch"  # non-tool step


# ---------------------------------------------------------------- providers (wire format, no network)

def test_openai_and_gemini_wire_format(monkeypatch):
    msgs = normalize_tool_messages([{"role": "system", "content": "S"}, {"role": "user", "content": "q"},
                                    {"role": "assistant", "content": "", "tool_calls": [{"id": "t1", "type": "function", "function": {"name": "get_account", "arguments": '{"customer_id":"c"}'}}]},
                                    {"role": "tool", "tool_call_id": "t1", "content": '{"plan": "Pro"}'}])
    wire = openai_messages(msgs)
    assert wire[2]["tool_calls"][0]["function"] == {"name": "get_account", "arguments": '{"customer_id": "c"}'} and wire[3]["tool_call_id"] == "t1"
    assert gemini_schema({"type": ["string", "null"], "additionalProperties": False, "$schema": "x"}) == {"type": "string", "nullable": True}

    seen = {}
    real = httpx.AsyncClient

    def handler(req):
        body = json.loads(req.content)
        seen[req.url.host] = body
        if "googleapis" in req.url.host:
            return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"functionCall": {"name": "calculator", "args": {"expression": "2*3"}}}]}}],
                                             "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 3}})
        return httpx.Response(200, json={"model": "m", "usage": {"prompt_tokens": 10, "completion_tokens": 3}, "choices": [{"message": {
            "content": None, "tool_calls": [{"id": "z", "type": "function", "function": {"name": "calculator", "arguments": '{"expression":"2*3"}'}}]}}]})

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    params = {"tools": TOOLS, "tool_choice": "auto", "temperature": 0}
    monkeypatch.setattr("tracelens_server.optimizer.providers.settings.openai_api_key", "k", raising=False)
    monkeypatch.setattr("tracelens_server.optimizer.providers.settings.gemini_api_key", "k", raising=False)
    o = asyncio.run(OpenAIProvider().complete("gpt-x", msgs, params))
    g = asyncio.run(GeminiProvider().complete("gemini-x", msgs, params))
    for comp in (o, g):
        assert comp.tool_calls == [{"id": comp.tool_calls[0]["id"], "name": "calculator", "arguments": {"expression": "2*3"}}]
    ob = seen["api.openai.com"]
    assert ob["tools"][0]["function"]["name"] == "get_account" and ob["tool_choice"] == "auto" and ob["messages"][-1]["role"] == "tool"
    gb = seen["generativelanguage.googleapis.com"]
    assert gb["tools"][0]["functionDeclarations"][1]["name"] == "calculator" and gb["toolConfig"]["functionCallingConfig"]["mode"] == "AUTO"
    model_turn, result_turn = gb["contents"][1], gb["contents"][2]
    assert model_turn["role"] == "model" and model_turn["parts"][0]["functionCall"]["name"] == "get_account"
    assert result_turn["parts"][0]["functionResponse"] == {"name": "get_account", "response": {"plan": "Pro"}}
    assert gb["systemInstruction"]["parts"][0]["text"] == "S"


# ---------------------------------------------------------------- end to end

PROVIDER_CALLS = []


@register_provider
class FakeToolProvider(Provider):
    name = "faketool"
    default_rpm = 10_000
    supports_tools = True

    async def complete(self, model, messages, params):
        PROVIDER_CALLS.append({"model": model, "messages": messages, "params": params})
        last = messages[-1]
        if last["role"] == "tool":  # saw the recorded tool result -> final answer
            return Completion(text="They are on the Pro plan.", input_tokens=50, output_tokens=8, tool_calls=[])
        if model == "agrees":
            return Completion(text="", input_tokens=50, output_tokens=8, tool_calls=[{"id": "x", "name": "get_account", "arguments": '{"customer_id": "cus_1"}'}])
        return Completion(text="Probably Pro.", input_tokens=50, output_tokens=8, tool_calls=[])  # skips the lookup


@register_provider
class NoToolsProvider(Provider):
    name = "notools"
    default_rpm = 10_000

    async def complete(self, model, messages, params):
        raise AssertionError("must not be called for tool steps")


pricing.register("agrees", 0.01, 0.01)
pricing.register("lazy", 0.01, 0.01)
pricing.register("nt", 0.01, 0.01)
pricing.register("orig-model", 1.0, 4.0)


def _seed_agent(client, n=6):
    now = time.time()
    batch = []
    for i in range(n):
        tid, root, g1, tool, g2 = (uuid.uuid4().hex for _ in range(5))
        hist = [{"role": "system", "content": "You are a billing agent."}, {"role": "user", "content": f"What plan is cus_1 on? ({i})"}]
        tc = [{"id": f"call_{i}", "name": "get_account", "arguments": {"customer_id": "cus_1"}}]
        md = {"provider": "openai", "tools": ["get_account", "calculator"], "tool_definitions": TOOLS}
        batch += [
            {"type": "trace", "body": {"id": tid, "name": "billing-agent", "start_time": now - 100 + i}},
            {"type": "observation", "body": {"id": root, "trace_id": tid, "type": "agent", "name": "billing-agent", "start_time": now - 100 + i}},
            {"type": "observation", "body": {"id": g1, "trace_id": tid, "parent_id": root, "type": "generation", "name": "openai.chat", "model": "orig-model",
                                             "input": hist, "output": {"role": "assistant", "content": None, "tool_calls": tc}, "metadata": md,
                                             "model_parameters": {"temperature": 0, "tool_choice": "auto"},
                                             "usage": {"input": 100, "output": 10}, "start_time": now - 100 + i, "end_time": now - 99.5 + i}},
            {"type": "observation", "body": {"id": tool, "trace_id": tid, "parent_id": root, "type": "tool", "name": "get_account",
                                             "input": {"customer_id": "cus_1"}, "output": {"plan": "Pro"}, "start_time": now - 99.4 + i}},
            {"type": "observation", "body": {"id": g2, "trace_id": tid, "parent_id": root, "type": "generation", "name": "openai.chat", "model": "orig-model",
                                             "input": hist + [{"role": "assistant", "content": None, "tool_calls": [{"id": f"call_{i}", "type": "function", "function": {"name": "get_account", "arguments": '{"customer_id": "cus_1"}'}}]},
                                                              {"role": "tool", "tool_call_id": f"call_{i}", "content": '{"plan": "Pro"}'}],
                                             "output": "They are on the Pro plan.", "metadata": md, "usage": {"input": 150, "output": 10},
                                             "start_time": now - 99.3 + i, "end_time": now - 99 + i}},
        ]
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


def test_agent_replay_end_to_end_never_executes_tools(client):
    _seed_agent(client)
    step = next(s for s in client.get("/api/v1/optimizer/steps", headers=KEY).json()["data"] if s["name"] == "openai.chat")
    assert step["calls"] == 12 and step["replayable"] == 0 and step["replayable_with_tools"] == 12 and step["tool_calls"] == 12

    # default: tool steps are not replayed at all
    PROVIDER_CALLS.clear()
    off = _wait(client, client.post("/api/v1/optimizer/runs", json={"steps": ["billing-agent / openai.chat"], "candidates": ["faketool:agrees"]}, headers=KEY).json()["id"])
    assert off["results"]["steps"][0]["sampled"] == 0 and PROVIDER_CALLS == []
    assert off["results"]["steps"][0]["recommendation"]["action"] == "insufficient_data"

    body = {"steps": ["billing-agent / openai.chat"], "candidates": ["faketool:agrees", "faketool:lazy", "notools:nt"], "sample_size": 12,
            "allow_tool_steps": True, "scorer": "auto", "scorer_config": {"text_scorer": "similarity"}, "examples_per_candidate": 20}
    run = _wait(client, client.post("/api/v1/optimizer/runs", json=body, headers=KEY).json()["id"])
    assert run["status"] == "completed", run.get("error")
    st = run["results"]["steps"][0]
    assert st["tool_step"] and st["sampled"] == 12 and st["tool_calls_sampled"] == 6
    # exactly one model call per recorded step and candidate: no follow-up turn after a tool call, nothing executed
    assert len(PROVIDER_CALLS) == 24 and EXECUTED == []
    for c in PROVIDER_CALLS:
        assert [t["name"] for t in c["params"]["tools"]] == ["get_account", "calculator"] and c["params"].get("tool_choice") in ("auto", None)
        if c["messages"][-1]["role"] == "tool":
            assert c["messages"][-1]["content"] == '{"plan": "Pro"}'  # the recorded result, sent back as data
            assert c["messages"][-2]["tool_calls"][0]["name"] == "get_account"

    by = {c["candidate"]: c for c in st["candidates"]}
    good, lazy, nt = by["faketool:agrees"], by["faketool:lazy"], by["notools:nt"]
    assert good["tool_agreement"] == 1.0 and good["decision_agreement"] == 1.0 and good["quality_mean"] == 1.0 and good["tool_n"] == 12
    assert lazy["tool_agreement"] == 0.5 and lazy["decision_agreement"] == 0.5 and lazy["tool_mismatches"]
    mm = lazy["tool_mismatches"][0]
    assert mm["original"] == ['get_account(customer_id="cus_1")'] and mm["candidate"] == ["(answered without a tool call)"]
    assert nt["n"] == 0 and nt["skipped"] == {"provider notools does not support tool calling": 12}
    rec = st["recommendation"]
    assert rec["action"] == "switch" and rec["to_model"] == "faketool:agrees" and rec["tool_agreement"] == 1.0, rec["rationale"]
    ex = next(e for e in good["examples"] if e.get("original_tool_calls"))
    assert ex["candidate_tool_calls"] == ['get_account(customer_id="cus_1")'] and ex["tool_agreement"] is True

    traj = run["results"]["trajectories"]
    assert len(traj) == 6 and run["results"]["summary"]["tool_steps"] == 1
    t0 = traj[0]
    assert [s["calls"] for s in t0["steps"]] == [['get_account(customer_id="cus_1")'], []]
    assert t0["agreement"] == {"faketool:agrees": 1.0, "faketool:lazy": 0.5, "notools:nt": None}
    assert t0["steps"][0]["candidates"]["faketool:lazy"]["agree"] is False

    # cached replays keep their tool calls
    PROVIDER_CALLS.clear()
    again = _wait(client, client.post("/api/v1/optimizer/runs", json=body, headers=KEY).json()["id"])
    assert PROVIDER_CALLS == [] and again["progress"]["cache_hits"] == 24
    assert next(c for c in again["results"]["steps"][0]["candidates"] if c["candidate"] == "faketool:agrees")["tool_agreement"] == 1.0
