"""PII redaction helpers. Pass ``mask=tracelens.redact.redact_pii`` (or ``redact_pii=True``) to ``init``."""
from __future__ import annotations

import re
from typing import Any, Callable, Iterable, Optional, Pattern, Tuple

DEFAULT_PATTERNS: Tuple[Tuple[str, Pattern[str]], ...] = (
    ("EMAIL", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("API_KEY", re.compile(r"\b(?:sk|pk|rk|gsk|tl-sk|AIza)[-_A-Za-z0-9]{16,}\b")),
    ("CARD", re.compile(r"\b(?:\d[ -]?){13,19}\b")),
    ("SSN", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("PHONE", re.compile(r"(?<!\w)(?:\+?\d{1,3}[\s.-]?)?(?:\(?\d{2,4}\)?[\s.-]?)\d{3,4}[\s.-]?\d{3,4}(?!\w)")),
    ("IP", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")),
)


def make_redactor(patterns: Optional[Iterable[Tuple[str, Pattern[str]]]] = None, extra: Optional[Iterable[Tuple[str, Pattern[str]]]] = None) -> Callable[[Any], Any]:
    pats = list(patterns or DEFAULT_PATTERNS) + list(extra or [])

    def _redact_str(s: str) -> str:
        for label, pat in pats:
            s = pat.sub(f"[REDACTED_{label}]", s)
        return s

    def redact(value: Any) -> Any:
        if isinstance(value, str):
            return _redact_str(value)
        if isinstance(value, dict):
            return {k: redact(v) for k, v in value.items()}
        if isinstance(value, list):
            return [redact(v) for v in value]
        if isinstance(value, tuple):
            return tuple(redact(v) for v in value)
        return value

    return redact


redact_pii = make_redactor()
