"""Safe, bounded JSON serialization for arbitrary Python objects."""
from __future__ import annotations

import base64
import dataclasses
import datetime as _dt
import enum
import json
import uuid
from typing import Any

MAX_STRING = 50_000
MAX_DEPTH = 12
MAX_ITEMS = 500


def to_jsonable(obj: Any, _depth: int = 0) -> Any:
    """Convert ``obj`` into something ``json.dumps`` accepts, never raising."""
    try:
        if _depth > MAX_DEPTH:
            return "<max depth>"
        if obj is None or isinstance(obj, (bool, int, float)):
            return obj
        if isinstance(obj, str):
            return obj if len(obj) <= MAX_STRING else obj[:MAX_STRING] + f"... <truncated {len(obj) - MAX_STRING} chars>"
        if isinstance(obj, bytes):
            if len(obj) > 2048:
                return f"<bytes len={len(obj)}>"
            return "base64:" + base64.b64encode(obj).decode()
        if isinstance(obj, (_dt.datetime, _dt.date)):
            return obj.isoformat()
        if isinstance(obj, (uuid.UUID, enum.Enum)):
            return str(obj.value if isinstance(obj, enum.Enum) else obj)
        if isinstance(obj, dict):
            out = {}
            for i, (k, v) in enumerate(obj.items()):
                if i >= MAX_ITEMS:
                    out["<truncated>"] = f"{len(obj) - MAX_ITEMS} more keys"
                    break
                out[str(k)] = to_jsonable(v, _depth + 1)
            return out
        if isinstance(obj, (list, tuple, set, frozenset)):
            seq = list(obj)
            res = [to_jsonable(v, _depth + 1) for v in seq[:MAX_ITEMS]]
            if len(seq) > MAX_ITEMS:
                res.append(f"<{len(seq) - MAX_ITEMS} more items>")
            return res
        # pydantic v2 / v1
        for attr in ("model_dump", "dict", "to_dict", "to_json_dict"):
            fn = getattr(obj, attr, None)
            if callable(fn):
                try:
                    return to_jsonable(fn(), _depth + 1)
                except Exception:
                    pass
        if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
            return to_jsonable(dataclasses.asdict(obj), _depth + 1)
        # LangChain messages & similar objects
        if hasattr(obj, "content") and hasattr(obj, "type"):
            return {"role": getattr(obj, "type"), "content": to_jsonable(getattr(obj, "content"), _depth + 1)}
        if hasattr(obj, "__dict__") and not isinstance(obj, type):
            d = {k: v for k, v in vars(obj).items() if not k.startswith("_")}
            if d:
                return to_jsonable(d, _depth + 1)
        return to_jsonable(repr(obj), _depth + 1)
    except Exception as e:  # pragma: no cover - defensive
        return f"<unserializable {type(obj).__name__}: {e}>"


def dumps(obj: Any) -> str:
    return json.dumps(to_jsonable(obj), ensure_ascii=False, separators=(",", ":"))
