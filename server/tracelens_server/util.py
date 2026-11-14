from __future__ import annotations

import datetime as _dt
import json
import math
import time
import uuid
from typing import Any, Iterable, List, Optional, Sequence


def new_id(prefix: str = "") -> str:
    return prefix + uuid.uuid4().hex


def now() -> float:
    return time.time()


def parse_time(v: Any) -> Optional[float]:
    """Accept ISO-8601 strings, epoch seconds/ms/ns numbers, datetimes. Returns epoch seconds."""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        x = float(v)
        if x > 1e17:  # ns
            return x / 1e9
        if x > 1e14:  # us
            return x / 1e6
        if x > 1e11:  # ms
            return x / 1e3
        return x
    if isinstance(v, _dt.datetime):
        if v.tzinfo is None:
            v = v.replace(tzinfo=_dt.timezone.utc)
        return v.timestamp()
    if isinstance(v, str):
        s = v.strip()
        try:
            return parse_time(float(s))
        except ValueError:
            pass
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            d = _dt.datetime.fromisoformat(s)
        except ValueError:
            return None
        return parse_time(d)
    return None


def iso(t: Optional[float]) -> Optional[str]:
    if t is None:
        return None
    return _dt.datetime.fromtimestamp(t, tz=_dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def percentile(values: Sequence[float], p: float) -> Optional[float]:
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    if len(vals) == 1:
        return vals[0]
    k = (len(vals) - 1) * p / 100.0
    f, c = math.floor(k), math.ceil(k)
    if f == c:
        return vals[int(k)]
    return vals[f] + (vals[c] - vals[f]) * (k - f)


def text_of(v: Any, limit: int = 4000) -> str:
    if v is None:
        return ""
    if isinstance(v, str):
        return v[:limit]
    try:
        return json.dumps(v, ensure_ascii=False)[:limit]
    except Exception:
        return str(v)[:limit]


def pipe_join(values: Iterable[Any]) -> str:
    vals = [str(v) for v in values if v not in (None, "")]
    return "|" + "|".join(vals) + "|" if vals else ""


def to_float(v: Any) -> Optional[float]:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def to_int(v: Any) -> Optional[int]:
    try:
        return None if v is None else int(v)
    except (TypeError, ValueError):
        return None
