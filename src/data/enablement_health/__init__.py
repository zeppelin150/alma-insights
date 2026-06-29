"""Enablement card-health score — a Guru-native effectiveness proxy.

Scores each Guru card 0..1 from signals that are FULLY DECOUPLED from the
ticket / RCM / product warehouse: the Guru live API (verification freshness,
per-card view demand, open comments, last-modified — all via ``GuruSignals``)
plus enablement-local tables (content_catalog, guru_content_drafts,
card_update_provenance, and the health/snapshot tables). The PHI boundary is
absolute: no ticket_index, source_trc_daily, sub_patterns, guru_effectiveness,
guru_friction_coverage, or trc_* table is ever read here.

Stages (one small single-purpose module each, filled in the Modules phase):
collect signals -> score components -> bucket -> assemble. ``compute_health``
is the public assembler; it is a stub until the Assemble phase wires the
modules together.
"""

from __future__ import annotations

from .guru_signals import GuruSignals
from .models import CardHealth, CardSignals
from .score import score_all
from .signals import gather_signals
from .store import upsert_health

__all__ = [
    "CardSignals",
    "CardHealth",
    "GuruSignals",
    "compute_health",
]


def _configured(guru_client) -> bool:
    """True when a usable Guru client is present (None / unconfigured → False)."""
    if guru_client is None:
        return False
    is_configured = getattr(guru_client, "is_configured", None)
    return bool(is_configured()) if callable(is_configured) else True


def compute_health(conn, guru_client, *, card_ids=None) -> list[CardHealth]:
    """Compute, persist, and return health for the given cards.

    A small linear pipeline: wrap the client in ``GuruSignals`` → gather
    Guru-native + catalog-local signals → score into ``CardHealth`` → upsert
    each into the health table. When ``card_ids`` is None the verification
    queue defines the card set. Reads only Guru live data (via ``GuruSignals``)
    and enablement-local tables — never a ticket / RCM / warehouse table.
    """
    if not _configured(guru_client):
        return []
    signals = gather_signals(conn, GuruSignals(guru_client), card_ids=card_ids)
    health = score_all(signals)
    for card in health:
        upsert_health(conn, card)
    return health
