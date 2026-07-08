# -*- coding: utf-8 -*-
"""Redaction helpers for user-sim logs.

Ported (slimmed) from CuES-plus ``src/runtime/redaction.py``
(source commit 7f71d5cb3b8fba4f0ba90cee10d0b102f3afe2fc). Kept self-contained
so the user-sim subpackage has no dependency on the CuES source tree.
"""

from __future__ import annotations

import re

SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bdata:[^,\s]+,[^\s'\"}]+"), "data:<redacted>"),
    (re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/=-]+"), "Bearer <redacted>"),
    (re.compile(r"\bsk-[A-Za-z0-9][A-Za-z0-9_-]{7,}\b"), "sk-<redacted>"),
    (
        re.compile(
            r"(?i)\b(api[_-]?key|access[_-]?key|secret|token|authorization)"
            r"\s*[:=]\s*['\"]?[^'\"\s,;}]+"
        ),
        r"\1=<redacted>",
    ),
)


def redact_text(text: object) -> str:
    """Redact known secret-like substrings while preserving original whitespace."""
    redacted = str(text)
    for pattern, replacement in SECRET_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted


def sanitize_snippet(text: object, *, max_chars: int = 240) -> str:
    """Return a single-line, bounded, best-effort redacted diagnostic snippet."""
    snippet = " ".join(str(text).split())
    snippet = redact_text(snippet)
    if len(snippet) > max_chars:
        return snippet[: max_chars - 3] + "..."
    return snippet
