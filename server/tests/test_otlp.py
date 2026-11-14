import json
import time

from conftest import KEY


def _span_proto():
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
    from opentelemetry.proto.common.v1.common_pb2 import AnyValue, KeyValue
    from opentelemetry.proto.trace.v1.trace_pb2 import ResourceSpans, ScopeSpans, Span, Status
    from opentelemetry.proto.resource.v1.resource_pb2 import Resource

    def kv(k, v):
        if isinstance(v, str):
            return KeyValue(key=k, value=AnyValue(string_value=v))
        if isinstance(v, float):
            return KeyValue(key=k, value=AnyValue(double_value=v))
        return KeyValue(key=k, value=AnyValue(int_value=v))

    now = int(time.time() * 1e9)
    tid = bytes.fromhex("0af7651916cd43dd8448eb211c80319c")
    root = Span(trace_id=tid, span_id=bytes.fromhex("b7ad6b7169203331"), name="agent.run", start_time_unix_nano=now, end_time_unix_nano=now + 2_000_000_000,
                attributes=[kv("user.id", "otel-user"), kv("session.id", "otel-s")])
    llm = Span(trace_id=tid, span_id=bytes.fromhex("00f067aa0ba902b7"), parent_span_id=bytes.fromhex("b7ad6b7169203331"), name="chat gpt-4o-mini",
               start_time_unix_nano=now + 1000, end_time_unix_nano=now + 1_500_000_000,
               attributes=[kv("gen_ai.operation.name", "chat"), kv("gen_ai.system", "openai"), kv("gen_ai.request.model", "gpt-4o-mini"),
                           kv("gen_ai.usage.input_tokens", 200), kv("gen_ai.usage.output_tokens", 50), kv("gen_ai.request.temperature", 0.3),
                           kv("gen_ai.prompt.0.role", "user"), kv("gen_ai.prompt.0.content", "Hello?"), kv("gen_ai.completion.0.content", "Hi there")])
    bad = Span(trace_id=tid, span_id=bytes.fromhex("11f067aa0ba902b7"), parent_span_id=bytes.fromhex("b7ad6b7169203331"), name="tool.search",
               start_time_unix_nano=now + 1000, end_time_unix_nano=now + 100_000_000, status=Status(code=2, message="timeout"),
               attributes=[kv("openinference.span.kind", "TOOL"), kv("input.value", '{"q": "x"}')])
    req = ExportTraceServiceRequest(resource_spans=[ResourceSpans(resource=Resource(attributes=[kv("service.name", "my-agent")]),
                                                                  scope_spans=[ScopeSpans(spans=[root, llm, bad])])])
    return req.SerializeToString()


def test_otlp_protobuf(client):
    r = client.post("/v1/traces", content=_span_proto(), headers={**KEY, "Content-Type": "application/x-protobuf"})
    assert r.status_code == 200, r.text
    t = client.get("/api/v1/traces/0af7651916cd43dd8448eb211c80319c", headers=KEY).json()
    assert t["name"] == "agent.run" and t["user_id"] == "otel-user" and t["session_id"] == "otel-s"
    assert t["status"] == "error" and t["total_tokens"] == 250 and t["total_cost"] > 0
    by = {o["name"]: o for o in t["observations"]}
    gen = by["chat gpt-4o-mini"]
    assert gen["type"] == "generation" and gen["model"] == "gpt-4o-mini" and gen["input"] == [{"role": "user", "content": "Hello?"}]
    assert gen["output"] == "Hi there" and gen["model_parameters"] == {"temperature": 0.3} and gen["metadata"]["provider"] == "openai"
    assert by["tool.search"]["type"] == "tool" and by["tool.search"]["level"] == "ERROR" and by["tool.search"]["input"] == {"q": "x"}
    assert abs(t["latency_ms"] - 2000) < 1


def test_otlp_json(client):
    now = int(time.time() * 1e9)
    payload = {"resourceSpans": [{"resource": {"attributes": [{"key": "service.name", "value": {"stringValue": "svc"}}]},
                                  "scopeSpans": [{"scope": {"name": "manual"}, "spans": [{
                                      "traceId": "5b8efff798038103d269b633813fc60c", "spanId": "eee19b7ec3c1b174", "name": "gen",
                                      "startTimeUnixNano": str(now), "endTimeUnixNano": str(now + 10**9),
                                      "attributes": [{"key": "gen_ai.request.model", "value": {"stringValue": "gemini-2.5-flash"}},
                                                     {"key": "gen_ai.usage.input_tokens", "value": {"intValue": "10"}},
                                                     {"key": "gen_ai.usage.output_tokens", "value": {"intValue": "20"}}]}]}]}]}
    r = client.post("/v1/traces", content=json.dumps(payload), headers={**KEY, "Content-Type": "application/json"})
    assert r.status_code == 200
    t = client.get("/api/v1/traces/5b8efff798038103d269b633813fc60c", headers=KEY).json()
    assert t["observations"][0]["type"] == "generation" and t["total_tokens"] == 30
