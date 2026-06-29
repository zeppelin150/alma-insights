"""Unified text diff for review (stdlib difflib, no dependency)."""

from __future__ import annotations

import difflib


def unified(existing_md: str, proposed_md: str, *, title: str = "card") -> str:
    """Return a unified diff of current vs proposed card markdown."""
    a = (existing_md or "").splitlines()
    b = (proposed_md or "").splitlines()
    lines = difflib.unified_diff(
        a, b,
        fromfile=f"{title} (current)",
        tofile=f"{title} (proposed)",
        lineterm="",
    )
    return "\n".join(lines)
