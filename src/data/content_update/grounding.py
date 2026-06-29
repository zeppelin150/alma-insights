"""Deterministic grounding check for a change plan.

This is a COARSE vocabulary-overlap heuristic, not a fabrication detector: each
change carries an ``evidence`` quote, and a change is dropped when too few of the
quote's content words appear in the source. It catches wholesale invention (a
quote with no shared vocabulary) but — being bag-of-words — cannot detect a
negation flip or a swapped number; the human reviews every staged diff, which is
the real safety gate. Comparison is stem-lite (plural/tense folded) so a faithful
paraphrase isn't dropped on an inflectional mismatch. Changes with no evidence
are kept (absence of a quote is terseness, not proof of fabrication), and the
gate is skipped entirely when the source tokenizes to nothing. No LLM, no cost.
"""

from __future__ import annotations

from src.data.content_catalog.vectorizer import tokenize

from .models import Change

_MIN_OVERLAP = 0.5


def _stem(t: str) -> str:
    """Fold common English suffixes so claim/claims and submit/submitted match."""
    for suf in ("ing", "ed", "es", "s"):
        if t.endswith(suf) and len(t) - len(suf) >= 3:
            return t[: -len(suf)]
    return t


def verify_grounding(changes: list[Change], source_text: str,
                     *, min_overlap: float = _MIN_OVERLAP) -> tuple[list[Change], list[dict]]:
    """Return (grounded_changes, dropped) — dropped carries the rejection reason."""
    src = {_stem(t) for t in tokenize(source_text)}
    grounded: list[Change] = []
    dropped: list[dict] = []
    for ch in changes:
        ev = [_stem(t) for t in tokenize(ch.evidence or "")]
        # Only gate when we have BOTH an evidence quote AND a usable source — an
        # empty/degenerate source must not nuke every change as "fabricated".
        if ev and src:
            overlap = sum(1 for t in ev if t in src) / len(ev)
            if overlap < min_overlap:
                dropped.append({
                    "type": ch.type, "section": ch.section,
                    "reason": f"ungrounded: only {overlap:.0%} of the cited evidence "
                              "appears in the source — likely fabricated"})
                continue
        grounded.append(ch)
    return grounded, dropped
