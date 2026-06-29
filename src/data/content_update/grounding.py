"""Deterministic grounding check for a change plan.

Haiku can propose a plausible-but-fabricated change. Each change carries an
``evidence`` quote; a change is rejected when the quote's content words don't
actually appear in the source material (the fabrication signal). Changes with no
evidence are kept — absence of a quote is terseness, not proof of fabrication —
but the human still reviews every staged diff. No LLM, no extra cost.
"""

from __future__ import annotations

from src.data.content_catalog.vectorizer import tokenize

from .models import Change

_MIN_OVERLAP = 0.5


def verify_grounding(changes: list[Change], source_text: str,
                     *, min_overlap: float = _MIN_OVERLAP) -> tuple[list[Change], list[dict]]:
    """Return (grounded_changes, dropped) — dropped carries the rejection reason."""
    src = set(tokenize(source_text))
    grounded: list[Change] = []
    dropped: list[dict] = []
    for ch in changes:
        ev = tokenize(ch.evidence or "")
        if ev:
            overlap = sum(1 for t in ev if t in src) / len(ev)
            if overlap < min_overlap:
                dropped.append({
                    "type": ch.type, "section": ch.section,
                    "reason": f"ungrounded: only {overlap:.0%} of the cited evidence "
                              "appears in the source — likely fabricated"})
                continue
        grounded.append(ch)
    return grounded, dropped
