"""Frozen data contracts for the enablement card-health score.

Plain data only — no logic, no I/O. The signal collector, scorer, and bucketer
(built in the Modules phase) and the ``compute_health`` assembler all pass these
small frozen records between stages, keeping every function single-purpose and
unit-testable in isolation.

PHI boundary: every field here is derived from the Guru live API or an
enablement-local table. Nothing comes from a ticket / RCM / warehouse table.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CardSignals:
    """Raw per-card signals gathered for one card before scoring.

    All Guru-native or enablement-local: verification freshness, demand
    (view_count), open comments, source-changed flag (an enablement-local
    provenance signal), duplicate ids, and a gap flag.
    """

    card_id: str
    title: str = ""
    verification_state: str = ""
    next_verification_date: str = ""
    days_overdue: float = 0.0
    last_modified: str = ""
    view_count: int = 0
    open_comment_count: int = 0
    source_changed: bool = False
    duplicate_of: list[str] = field(default_factory=list)
    gap: bool = False


@dataclass(frozen=True)
class CardHealth:
    """Scored health for one card.

    ``score`` is 0..1 (higher = healthier). ``bucket`` is the single worst-
    standing category the card falls into. ``components`` holds the per-signal
    sub-scores that combined into ``score`` (kept for transparency in the UI).
    """

    card_id: str
    score: float
    bucket: str  # source_changed | verification_overdue | gap_dup | healthy
    components: dict[str, float] = field(default_factory=dict)
    signals: CardSignals | None = None
