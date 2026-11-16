"""Exporters ship batches of events somewhere. Subclass :class:`Exporter` to add your own.

An *event* is a dict ``{"type": "trace"|"observation"|"score", "body": {...}}``.
"""
from __future__ import annotations

import json
import logging
import queue
import random
import sys
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Sequence

from ._serialize import to_jsonable

logger = logging.getLogger("tracelens")
Event = Dict[str, Any]


class Exporter:
    """Base class. Implement :meth:`export`; raise to signal a retryable failure."""

    def export(self, events: List[Event]) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def shutdown(self) -> None:
        pass


class ConsoleExporter(Exporter):
    """Pretty-prints events to stderr. Handy for local debugging."""

    def __init__(self, stream=None):
        self.stream = stream or sys.stderr

    def export(self, events: List[Event]) -> None:
        for e in events:
            b = e["body"]
            extra = ""
            if e["type"] == "observation":
                extra = f" type={b.get('type')} model={b.get('model')} tokens={b.get('total_tokens')} cost={b.get('cost')}"
            print(f"[tracelens] {e['type']} {b.get('name', '')} id={b.get('id')}{extra}", file=self.stream)


class FileExporter(Exporter):
    """Appends events as JSON lines to a file (offline mode, replay later with ``tracelens replay``)."""

    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()

    def export(self, events: List[Event]) -> None:
        with self._lock, open(self.path, "a", encoding="utf-8") as f:
            for e in events:
                f.write(json.dumps(to_jsonable(e), ensure_ascii=False) + "\n")


class HttpExporter(Exporter):
    """Sends batches to a TraceLens server ``POST {host}/api/v1/ingest``."""

    def __init__(self, host: str, api_key: str, timeout: float = 5.0, max_retries: int = 3, headers: Optional[Dict[str, str]] = None):
        self.url = host.rstrip("/") + "/api/v1/ingest"
        self.api_key = api_key
        self.timeout = timeout
        self.max_retries = max_retries
        self.headers = headers or {}
        self._auth_warned = False

    def export(self, events: List[Event]) -> None:
        payload = json.dumps({"batch": to_jsonable(events)}, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}", "User-Agent": "tracelens-python/0.2.0", **self.headers}
        last: Optional[Exception] = None
        for attempt in range(self.max_retries + 1):
            try:
                req = urllib.request.Request(self.url, data=payload, headers=headers, method="POST")
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    resp.read()
                return
            except urllib.error.HTTPError as e:
                if e.code in (401, 403):
                    if not self._auth_warned:
                        logger.warning("tracelens: server rejected API key (HTTP %s); dropping events", e.code)
                        self._auth_warned = True
                    return
                if 400 <= e.code < 500 and e.code != 429:
                    logger.warning("tracelens: ingest rejected (HTTP %s): %s", e.code, e.read()[:300])
                    return
                last = e
            except Exception as e:  # connection errors, timeouts
                last = e
            if attempt < self.max_retries:
                time.sleep(min(8.0, 0.5 * (2 ** attempt)) * (0.5 + random.random()))
        raise RuntimeError(f"tracelens export failed after retries: {last}")


class BatchProcessor:
    """Non-blocking background batching with a bounded queue and a circuit breaker.

    The application thread only ever does ``queue.put_nowait`` - if the queue is full
    (e.g. server down for a long time) events are dropped, never blocking your app.
    """

    def __init__(self, exporters: Sequence[Exporter], batch_size: int = 100, flush_interval: float = 1.0, max_queue: int = 10_000):
        self.exporters = list(exporters)
        self.batch_size = batch_size
        self.flush_interval = flush_interval
        self._q: "queue.Queue[Any]" = queue.Queue(maxsize=max_queue)
        self._stop = threading.Event()
        self._flush_req = threading.Condition()
        self._pending = 0
        self._failures = 0
        self._open_until = 0.0
        self.dropped = 0
        self._thread = threading.Thread(target=self._run, name="tracelens-exporter", daemon=True)
        self._thread.start()

    def enqueue(self, event: Event) -> None:
        try:
            self._q.put_nowait(event)
            with self._flush_req:
                self._pending += 1
        except queue.Full:
            self.dropped += 1
            if self.dropped in (1, 100, 1000) or self.dropped % 10000 == 0:
                logger.warning("tracelens: export queue full, dropped %d events", self.dropped)

    def _drain(self) -> List[Event]:
        batch: List[Event] = []
        while len(batch) < self.batch_size:
            try:
                batch.append(self._q.get_nowait())
            except queue.Empty:
                break
        return batch

    def _send(self, batch: List[Event]) -> None:
        if time.time() < self._open_until:  # circuit open: server unreachable, drop quietly
            self.dropped += len(batch)
            return
        ok = True
        for exp in self.exporters:
            try:
                exp.export(batch)
            except Exception as e:
                ok = False
                logger.debug("tracelens exporter %s failed: %s", type(exp).__name__, e)
        if ok:
            self._failures = 0
        else:
            self._failures += 1
            if self._failures >= 3:
                self._open_until = time.time() + 30
                logger.warning("tracelens: exporter unreachable, pausing export for 30s (app is unaffected)")
                self._failures = 0

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                first = self._q.get(timeout=self.flush_interval)
            except queue.Empty:
                continue
            time.sleep(min(0.05, self.flush_interval))  # small linger to build batches
            batch = [first] + self._drain()
            self._send(batch)
            with self._flush_req:
                self._pending -= len(batch)
                self._flush_req.notify_all()

    def flush(self, timeout: float = 10.0) -> bool:
        deadline = time.time() + timeout
        with self._flush_req:
            while self._pending > 0:
                remaining = deadline - time.time()
                if remaining <= 0:
                    return False
                self._flush_req.wait(remaining)
        return True

    def shutdown(self, timeout: float = 10.0) -> None:
        self.flush(timeout)
        self._stop.set()
        for exp in self.exporters:
            try:
                exp.shutdown()
            except Exception:
                pass
