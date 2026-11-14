import pytest

import tracelens
from tracelens.exporters import Exporter


class MemoryExporter(Exporter):
    def __init__(self):
        self.events = []

    def export(self, events):
        self.events.extend(events)

    def of(self, kind):
        return [e["body"] for e in self.events if e["type"] == kind]


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch):
    """Never let a developer's TRACELENS_* env send test traces to a real server."""
    for k in ("TRACELENS_API_KEY", "TRACELENS_PUBLIC_KEY", "TRACELENS_SECRET_KEY", "TRACELENS_HOST", "TRACELENS_ENABLED",
              "TRACELENS_SAMPLE_RATE", "TRACELENS_DEBUG", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"):
        monkeypatch.delenv(k, raising=False)


@pytest.fixture
def mem():
    exp = MemoryExporter()
    client = tracelens.init(exporters=[exp], flush_interval=0.05, auto_instrument=True)
    yield exp
    client.shutdown()
