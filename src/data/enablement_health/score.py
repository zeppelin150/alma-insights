"""Deterministic composite card-health scorer — pure, no I/O.

``score_card`` turns one ``CardSignals`` record into a ``CardHealth``: four
named components (freshness, demand, comments, content_health) combined by
fixed weights into a 0..1 score (higher = healthier), plus a single ``bucket``
naming the worst standing the card falls into.

No Guru, no DB, no settings — every input is already on the ``CardSignals``
record. That makes the whole module trivially unit-testable in isolation.

PHI boundary: this module reads only its ``CardSignals`` argument, which is
itself Guru-native / enablement-local. Nothing here touches a warehouse table.
"""

from __future__ import annotations

import math

from .models import CardHealth, CardSignals

# ── component weights (sum to 1.0) ──
W_FRESHNESS = 0.40
W_DEMAND = 0.25
W_COMMENTS = 0.15
W_CONTENT = 0.20

# ── normalization constants ──
OVERDUE_FULL_DAYS = 30.0  # days_overdue at/above this → freshness fully penalized
VIEW_LOG_BASE = 50.0  # log-damp denominator: ~this many views → full demand
COMMENT_FULL_COUNT = 5.0  # open comments at/above this → comments fully penalized
DUP_PENALTY = 0.5  # content_health hit when the card duplicates another
GAP_PENALTY = 0.5  # content_health hit when the card has a coverage gap


def _freshness(days_overdue: float) -> float:
    """1.0 when on time, decaying to 0.0 by ``OVERDUE_FULL_DAYS`` overdue."""
    if days_overdue <= 0:
        return 1.0
    return max(0.0, 1.0 - days_overdue / OVERDUE_FULL_DAYS)


def _demand(view_count: int) -> float:
    """Log-damped demand in 0..1 — heavy traffic saturates, never overflows."""
    if view_count <= 0:
        return 0.0
    damped = math.log1p(view_count) / math.log1p(VIEW_LOG_BASE)
    return min(1.0, damped)


def _comments(open_comment_count: int) -> float:
    """1.0 with no open comments, decaying to 0.0 by ``COMMENT_FULL_COUNT``."""
    if open_comment_count <= 0:
        return 1.0
    return max(0.0, 1.0 - open_comment_count / COMMENT_FULL_COUNT)


def _content_health(signals: CardSignals) -> float:
    """1.0 clean; each of duplicate / gap subtracts its penalty (floored at 0)."""
    score = 1.0
    if signals.duplicate_of:
        score -= DUP_PENALTY
    if signals.gap:
        score -= GAP_PENALTY
    return max(0.0, score)


def _bucket(signals: CardSignals) -> str:
    """The single worst standing, by fixed priority (highest first)."""
    if signals.source_changed:
        return "source_changed"
    if signals.days_overdue > 0:
        return "verification_overdue"
    if signals.duplicate_of or signals.gap:
        return "gap_dup"
    return "healthy"


def score_card(signals: CardSignals) -> CardHealth:
    """Score one card: weighted composite + priority bucket. Pure function."""
    components = {
        "freshness": _freshness(signals.days_overdue),
        "demand": _demand(signals.view_count),
        "comments": _comments(signals.open_comment_count),
        "content_health": _content_health(signals),
    }
    score = (
        W_FRESHNESS * components["freshness"]
        + W_DEMAND * components["demand"]
        + W_COMMENTS * components["comments"]
        + W_CONTENT * components["content_health"]
    )
    score = min(1.0, max(0.0, score))
    return CardHealth(
        card_id=signals.card_id,
        score=score,
        bucket=_bucket(signals),
        components=components,
        signals=signals,
    )


def score_all(signals_list: list[CardSignals]) -> list[CardHealth]:
    """Score a batch of cards in order. Pure function."""
    return [score_card(s) for s in signals_list]
