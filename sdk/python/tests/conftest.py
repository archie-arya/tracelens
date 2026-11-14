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


@pytest.fixture
def mem():
    exp = MemoryExporter()
    client = tracelens.init(exporters=[exp], flush_interval=0.05, auto_instrument=True)
    yield exp
    client.shutdown()
