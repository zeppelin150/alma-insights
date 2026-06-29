"""Guru-native effectiveness proxy — impact without a single ticket read.

Replaces the moot ticket-volume ``record_baseline`` weld. At publish we snapshot
a card's Guru-native demand signals (per-card view_count + open_comment_count,
both via ``GuruSignals``); after a window we re-pull a followup and report the
deltas. FULLY DECOUPLED: nothing here touches a ticket / RCM / warehouse table —
only the Guru live API (through the facade) and the enablement-local
``card_signal_snapshot`` table.
"""

from __future__ import annotations

from datetime import datetime, timezone

from src.data.connection_factory import atomic

_DDL = """
CREATE TABLE IF NOT EXISTS card_signal_snapshot (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id            TEXT NOT NULL,
    draft_id           INTEGER,
    view_count         INTEGER DEFAULT 0,
    open_comment_count INTEGER DEFAULT 0,
    captured_at        TEXT DEFAULT '',
    phase              TEXT DEFAULT 'baseline'
)
"""


def ensure_table(conn) -> None:
    """Defensive no-op for code paths that run before migrate (mirrors 035)."""
    conn.execute(_DDL)
    conn.commit()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _signals(guru_signals, card_id: str) -> tuple[int, int]:
    """(view_count, open_comment_count) for one card from the Guru facade."""
    views = guru_signals.card_view_counts().get(card_id, 0)
    comments = guru_signals.open_comment_count(card_id)
    return int(views or 0), int(comments or 0)


def snapshot_baseline(conn, guru_signals, card_id: str, draft_id: int | None) -> dict:
    """Capture the pre-change view + open-comment counts at publish time."""
    ensure_table(conn)
    views, comments = _signals(guru_signals, card_id)
    now = _now()
    with atomic(conn):
        conn.execute(
            "INSERT INTO card_signal_snapshot "
            "(card_id, draft_id, view_count, open_comment_count, captured_at, phase) "
            "VALUES (?,?,?,?,?, 'baseline')",
            (card_id, draft_id, views, comments, now))
    return {"card_id": card_id, "view_count": views,
            "open_comment_count": comments, "captured_at": now}


def _baseline(conn, card_id: str) -> dict | None:
    row = conn.execute(
        "SELECT view_count, open_comment_count, captured_at FROM card_signal_snapshot "
        "WHERE card_id=? AND phase='baseline' ORDER BY captured_at DESC LIMIT 1",
        (card_id,)).fetchone()
    return dict(row) if row else None


def measure_proxy(conn, guru_signals, card_id: str) -> dict:
    """Re-pull after a window; report views_delta + comments_resolved vs baseline.

    Returns ``{"ok": False, ...}`` when no baseline exists yet (nothing to
    measure against). Persists the followup snapshot for the audit trail.
    """
    ensure_table(conn)
    base = _baseline(conn, card_id)
    if base is None:
        return {"ok": False, "card_id": card_id, "error": "no_baseline"}
    views, comments = _signals(guru_signals, card_id)
    now = _now()
    with atomic(conn):
        conn.execute(
            "INSERT INTO card_signal_snapshot "
            "(card_id, draft_id, view_count, open_comment_count, captured_at, phase) "
            "VALUES (?,?,?,?,?, 'followup')",
            (card_id, None, views, comments, now))
    return {"ok": True, "card_id": card_id,
            "views_delta": views - int(base["view_count"] or 0),
            "comments_resolved": int(base["open_comment_count"] or 0) - comments,
            "baseline_at": base["captured_at"], "followup_at": now}
