import asyncio
import http.server
import json
import threading
import time

import pytest

import tracelens
from tracelens import pricing
from tracelens.exporters import BatchProcessor, HttpExporter


def test_nested_observe(mem):
    @tracelens.observe(type="retrieval")
    def retrieve(q):
        return ["doc1", "doc2"]

    @tracelens.observe()
    def answer(q, user="u1"):
        docs = retrieve(q)
        with tracelens.span("llm", type="generation", model="gpt-4o-mini") as g:
            g.update(output="42", usage={"input": 1000, "output": 500})
        tracelens.update_current_trace(user_id=user, tags=["t1"])
        return "42"

    assert answer("what?") == "42"
    tracelens.flush()
    traces, obs = mem.of("trace"), mem.of("observation")
    assert len(traces) == 1 and traces[0]["name"] == "answer"
    assert traces[0]["user_id"] == "u1" and "t1" in traces[0]["tags"]
    assert traces[0]["input"] == {"q": "what?", "user": "u1"} or traces[0]["input"] == {"q": "what?"}
    assert traces[0]["output"] == "42"
    by = {o["name"]: o for o in obs}
    root = by["answer"]
    assert root["parent_id"] is None
    assert by["retrieve"]["parent_id"] == root["id"] and by["retrieve"]["type"] == "retrieval"
    assert by["llm"]["parent_id"] == root["id"]
    assert by["llm"]["cost"] == pytest.approx((1000 * 0.15 + 500 * 0.6) / 1e6)
    assert by["llm"]["total_tokens"] == 1500
    assert all(o["trace_id"] == traces[0]["id"] for o in obs)


def test_async_isolation(mem):
    @tracelens.observe()
    async def step(i):
        await asyncio.sleep(0.01)
        return i

    @tracelens.observe()
    async def job(i):
        return await step(i)

    async def main():
        return await asyncio.gather(*(job(i) for i in range(5)))

    assert asyncio.run(main()) == list(range(5))
    tracelens.flush()
    traces = mem.of("trace")
    assert len(traces) == 5
    obs = mem.of("observation")
    for o in obs:
        if o["name"].endswith("step"):
            parent = next(p for p in obs if p["id"] == o["parent_id"])
            assert parent["trace_id"] == o["trace_id"]


def test_error_recorded(mem):
    @tracelens.observe()
    def boom():
        raise ValueError("bad")

    with pytest.raises(ValueError):
        boom()
    tracelens.flush()
    o = mem.of("observation")[0]
    assert o["level"] == "ERROR" and "ValueError" in o["status_message"]
    assert "error" in mem.of("trace")[0]["metadata"]


def test_generator_and_score(mem):
    @tracelens.observe()
    def stream():
        yield "a"
        yield "b"

    with tracelens.trace("chat", session_id="s1") as t:
        assert "".join(stream()) == "ab"
        tracelens.score("feedback", True, comment="thumbs up")
    tracelens.flush()
    assert mem.of("observation")[0]["output"] == "ab"
    s = mem.of("score")[0]
    assert s["trace_id"] == t.id and s["value"] == 1.0


def test_sampling_zero():
    from tests.conftest import MemoryExporter
    exp = MemoryExporter()
    c = tracelens.init(exporters=[exp], sample_rate=0.0, flush_interval=0.05, auto_instrument=False)

    @tracelens.observe()
    def f():
        return 1

    for _ in range(10):
        f()
    c.flush()
    assert exp.events == []
    c.shutdown()


def test_noop_without_key(monkeypatch):
    monkeypatch.delenv("TRACELENS_API_KEY", raising=False)
    c = tracelens.init(auto_instrument=False)
    assert not c.enabled

    @tracelens.observe()
    def f(x):
        return x * 2

    assert f(2) == 4


def test_redaction():
    from tests.conftest import MemoryExporter
    exp = MemoryExporter()
    c = tracelens.init(exporters=[exp], redact_pii=True, flush_interval=0.05, auto_instrument=False)
    with tracelens.span("x", input="mail me at jane@example.com or +1 415 555 0101") as s:
        s.output = {"card": "4242 4242 4242 4242"}
    c.flush()
    o = exp.of("observation")[0]
    assert "jane@example.com" not in json.dumps(o) and "[REDACTED_EMAIL]" in o["input"]
    assert "4242 4242" not in json.dumps(o["output"])
    c.shutdown()


def test_pricing():
    assert pricing.lookup("gpt-4o-mini-2024-07-18") == (0.15, 0.6)
    assert pricing.lookup("models/gemini-2.5-flash-lite") == (0.10, 0.40)
    assert pricing.lookup("groq/llama-3.1-8b-instant") == (0.05, 0.08)
    pricing.register("my-model", input=1, output=2)
    assert pricing.calculate("my-model-v2", 1_000_000, 1_000_000) == 3
    assert pricing.calculate("unknown-xyz", 10, 10) is None


class _Handler(http.server.BaseHTTPRequestHandler):
    calls = []
    fail_first = 1

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        _Handler.calls.append((self.headers.get("Authorization"), json.loads(body)))
        if _Handler.fail_first > 0:
            _Handler.fail_first -= 1
            self.send_response(503)
            self.end_headers()
            return
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *a):
        pass


def test_http_exporter_retries():
    srv = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    exp = HttpExporter(f"http://127.0.0.1:{srv.server_port}", "tl-sk-test", max_retries=2)
    exp.export([{"type": "trace", "body": {"id": "1"}}])
    assert len(_Handler.calls) == 2
    assert _Handler.calls[-1][0] == "Bearer tl-sk-test"
    srv.shutdown()


def test_unreachable_server_never_blocks():
    c = tracelens.TraceLens(api_key="x", host="http://127.0.0.1:9", flush_interval=0.01, timeout=0.2)
    t0 = time.time()
    for _ in range(200):
        with c.span("x"):
            pass
    assert time.time() - t0 < 1.0
    c.shutdown(0.1)
