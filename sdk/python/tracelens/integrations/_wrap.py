from __future__ import annotations

from typing import Any, Callable, List, Tuple

_patched: List[Tuple[Any, str, Any]] = []


def wrap(owner: Any, attr: str, factory: Callable[[Any], Any]) -> bool:
    orig = getattr(owner, attr, None)
    if orig is None:
        return False
    if getattr(orig, "__tracelens_wrapped__", False):
        return True
    wrapped = factory(orig)
    wrapped.__tracelens_wrapped__ = True
    wrapped.__wrapped__ = orig
    setattr(owner, attr, wrapped)
    _patched.append((owner, attr, orig))
    return True


def unwrap_all(module_prefix: str = "") -> None:
    for owner, attr, orig in list(_patched):
        if getattr(owner, "__module__", "").startswith(module_prefix):
            setattr(owner, attr, orig)
            _patched.remove((owner, attr, orig))


def safe(fn: Callable[[], Any], default: Any = None) -> Any:
    try:
        return fn()
    except Exception:
        return default
