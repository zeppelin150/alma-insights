"""Persistence for the enablement card-health score.

One small table, ``guru_card_health``: the latest 0..1 score, worst-standing
bucket, and per-signal component + raw-signal breakdown for each card. Mirrors
the lazy-DDL pattern of content_update/provenance.py — ``ensure_table`` keeps
the table present for code paths that run before a migration sweep.

PHI boundary: every value written here is derived from the Guru live API (via
GuruSignals) or an enablement-local table — never a ticket / RCM / warehouse
table. This module only reads/writes its own table.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone

from src.data.connection_factory import atomic
from .models import CardHealth, CardSignals

_DDL = """
CREATE TABLE IF NOT EXISTS guru_card_health (
    card_id         TEXT PRIMARY KEY,
    title           TEXT DEFAULT '',
    score           REAL DEFAULT 0,
    bucket          TEXT DEFAULT '',
    components_json TEXT DEFAULT '{}',
    signals_json    TEXT DEFAULT '{}',
    computed_at     TEXT DEFAULT ''
)
"""

_INDEX = (
    "CREATE INDEX IF NOT EXISTS idx_guru_card_health_bucket "
    "ON guru_card_health (bucket)"
)


def ensure_table(conn) -> None:
    """Create the health table + bucket index if absent (idempotent)."""
    conn.execute(_DDL)
    conn.execute(_INDEX)
    conn.commit()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _title(health: CardHealth) -> str:
    return health.signals.title if health.signals else ""


def _signals_json(health: CardHealth) -> str:
    if health.signals is None:
        return "{}"
    return json.dumps(asdict(health.signals))


def upsert_health(conn, health: CardHealth) -> None:
    """Insert or replace the latest health row for ``health.card_id``."""
    ensure_table(conn)
    with atomic(conn):
        conn.execute(
            """INSERT INTO guru_card_health
               (card_id, title, score, bucket, components_json, signals_json, computed_at)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(card_id) DO UPDATE SET
                 title=excluded.title, score=excluded.score, bucket=excluded.bucket,
                 components_json=excluded.components_json,
                 signals_json=excluded.signals_json, computed_at=excluded.computed_at""",
            (health.card_id, _title(health), float(health.score), health.bucket or "",
             json.dumps(health.components or {}), _signals_json(health), _now()))


def get_health(conn, card_id: str):
    """Return the stored health for one card as a dict, or None."""
    ensure_table(conn)
    row = conn.execute(
        "SELECT * FROM guru_card_health WHERE card_id=?", (card_id,)).fetchone()
    return _row(row) if row else None


def list_health(conn, bucket: str | None = None, limit: int = 100) -> list[dict]:
    """Return stored health rows ordered by score DESC, optionally bucket-filtered."""
    ensure_table(conn)
    if bucket:
        rows = conn.execute(
            "SELECT * FROM guru_card_health WHERE bucket=? "
            "ORDER BY score DESC LIMIT ?", (bucket, limit)).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM guru_card_health ORDER BY score DESC LIMIT ?",
            (limit,)).fetchall()
    return [_row(r) for r in rows]


def _row(r) -> dict:
    return {"card_id": r["card_id"], "title": r["title"], "score": r["score"],
            "bucket": r["bucket"],
            "components": json.loads(r["components_json"] or "{}"),
            "signals": json.loads(r["signals_json"] or "{}"),
            "computed_at": r["computed_at"]}
