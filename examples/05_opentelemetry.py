"""Send traces with plain OpenTelemetry (no TraceLens SDK in the hot path). Any language with an OTLP/HTTP exporter
can do the same: point it at {host}/v1/traces with `Authorization: Bearer <key>`.

    pip install opentelemetry-sdk opentelemetry-exporter-otlp-proto-http
    python examples/05_opentelemetry.py
"""
import os
import time

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

host = os.environ.get("TRACELENS_HOST", "http://localhost:8000").rstrip("/")
provider = TracerProvider(resource=Resource.create({"service.name": "otel-demo", "service.version": "1.4.0"}))
provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{host}/v1/traces", headers={"Authorization": f"Bearer {os.environ['TRACELENS_API_KEY']}"})))
trace.set_tracer_provider(provider)
tracer = trace.get_tracer("my-llm-app")

with tracer.start_as_current_span("classify-ticket") as root:
    root.set_attribute("user.id", "otel-user-7")
    root.set_attribute("session.id", "otel-session-1")
    with tracer.start_as_current_span("chat gpt-4o-mini") as span:  # GenAI semantic conventions
        span.set_attribute("gen_ai.operation.name", "chat")
        span.set_attribute("gen_ai.system", "openai")
        span.set_attribute("gen_ai.request.model", "gpt-4o-mini")
        span.set_attribute("gen_ai.request.temperature", 0.0)
        span.set_attribute("gen_ai.prompt.0.role", "user")
        span.set_attribute("gen_ai.prompt.0.content", "Classify: 'My invoice is wrong'")
        time.sleep(0.2)
        span.set_attribute("gen_ai.completion.0.content", "billing")
        span.set_attribute("gen_ai.usage.input_tokens", 18)
        span.set_attribute("gen_ai.usage.output_tokens", 1)

provider.shutdown()
print("Sent OTLP spans to", host)
