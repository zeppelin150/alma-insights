"""Effectiveness feedback for a card: its update history + measured impact.

Reads the authoritative `guru_effectiveness` measure (pre/post ticket volume,
delta_pct, significance) keyed off the card. We never fabricate a premature
delta — until post-publish data accrues, the push is simply *anchored* (via the
provenance pushed_at) and reported as pending. All de-identified: counts and
deltas only, no ticket text.
"""

from __future__ import annotations

from . import provenance


def card_effectiveness(conn, card_id: str, *, limit: int = 20) -> dict:
    updates = provenance.history(conn, card_id, limit=limit)
    measured = _measured(conn, card_id)
    return {"ok": True, "card_id": card_id, "updates": updates,
            "measured": measured, "note": _note(updates, measured)}


def _measured(conn, card_id: str) -> list[dict]:
    try:
        rows = conn.execute(
            "SELECT friction_type, measurement_date, pre_volume, post_volume, "
            "       pre_window_days, post_window_days, delta_pct, is_significant "
            "FROM guru_effectiveness WHERE card_id=? "
            "ORDER BY measurement_date DESC LIMIT 10", (card_id,)).fetchall()
        return [dict(r) for r in rows]
    except Exception:  # noqa: BLE001 — table absent / not yet computed
        return []


def _note(updates: list[dict], measured: list[dict]) -> str:
    if not updates:
        return "No recorded updates for this card."
    published = [u for u in updates if u.get("status") == "published"]
    if measured:
        m = measured[0]
        sig = " (significant)" if m.get("is_significant") else ""
        return (f"Measured impact: ticket-volume delta {m.get('delta_pct')}%{sig} "
                f"({m.get('pre_volume')}→{m.get('post_volume')} over "
                f"{m.get('post_window_days')}d). {len(published)} published update(s).")
    if published:
        return (f"{len(published)} published update(s); effectiveness is measured once "
                f"post-publish ticket data accrues (anchored {published[0].get('pushed_at')}).")
    return f"{len(updates)} staged update(s), not yet published."
