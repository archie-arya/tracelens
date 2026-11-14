"""Send OpenTelemetry spans (e.g. from OpenLLMetry / OpenInference / your own OTel setup) to TraceLens.

    pip install "tracelens[otel]"
    from tracelens.otel import configure_otel
    configure_otel(api_key="tl-sk-...", host="http://localhost:8000")

TraceLens understands the OpenTelemetry GenAI semantic conventions (``gen_ai.*``) and OpenInference (``llm.*``).
"""
from __future__ import annotations

import os
from typing import Any, Optional


def configure_otel(api_key: Optional[str] = None, host: Optional[str] = None, service_name: str = "llm-app", set_global: bool = True) -> Any:
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    api_key = api_key or os.environ["TRACELENS_API_KEY"]
    host = (host or os.environ.get("TRACELENS_HOST") or "http://localhost:8000").rstrip("/")
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{host}/v1/traces", headers={"Authorization": f"Bearer {api_key}"})))
    if set_global:
        trace.set_tracer_provider(provider)
    return provider
