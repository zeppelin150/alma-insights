"""CRUD + cursor helpers over the monitor_sources table.

One place for the Settings page and the connectors to read/write a source's
config and advance its incremental cursor. Plain execute+commit on purpose:
monitors run on a background thread and the chat dispatch path forbids a nested
atomic() (see registry.dispatch_tool / connection_factory.atomic).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

_COLS = ("source_id, source_type, display_name, config_json, enabled, cursor, "
         "last_run_at, last_status, last_error")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row(r) -> dict:
    return {
        "source_id": r[0], "source_type": r[1], "display_name": r[2],
        "config": json.loads(r[3] or "{}"), "enabled": bool(r[4]), "cursor": r[5],
        "last_run_at": r[6], "last_status": r[7], "last_error": r[8],
    }


def list_sources(conn, source_type: str | None = None) -> list[dict]:
    if source_type:
        rows = conn.execute(
            f"SELECT {_COLS} FROM monitor_sources WHERE source_type=? ORDER BY display_name",
            (source_type,)).fetchall()
    else:
        rows = conn.execute(
            f"SELECT {_COLS} FROM monitor_sources ORDER BY source_type, display_name").fetchall()
    return [_row(r) for r in rows]


def add_source(conn, *, source_type: str, source_id: str, display_name: str,
               config: dict, enabled: bool = True) -> str:
    now = _now()
    conn.execute(
        "INSERT INTO monitor_sources "
        "(source_id, source_type, display_name, config_json, enabled, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?) ON CONFLICT(source_id) DO UPDATE SET "
        "display_name=excluded.display_name, config_json=excluded.config_json, "
        "enabled=excluded.enabled, updated_at=excluded.updated_at",
        (source_id, source_type, display_name, json.dumps(config), 1 if enabled else 0, now, now),
    )
    conn.commit()
    return source_id


def set_enabled(conn, source_id: str, enabled: bool) -> None:
    conn.execute("UPDATE monitor_sources SET enabled=?, updated_at=? WHERE source_id=?",
                 (1 if enabled else 0, _now(), source_id))
    conn.commit()


def remove_source(conn, source_id: str) -> None:
    conn.execute("DELETE FROM monitor_sources WHERE source_id=?", (source_id,))
    conn.commit()


def read_cursor(conn, source_id: str) -> str | None:
    row = conn.execute("SELECT cursor FROM monitor_sources WHERE source_id=?",
                       (source_id,)).fetchone()
    return (row[0] if row else None) or None


def mark_source(conn, source_id: str, *, status: str = "ok",
                cursor: str | None = None, error: str | None = None) -> None:
    now = _now()
    if cursor is not None:
        conn.execute(
            "UPDATE monitor_sources SET cursor=?, last_run_at=?, last_status=?, last_error=? "
            "WHERE source_id=?", (cursor, now, status, error, source_id))
    else:
        conn.execute(
            "UPDATE monitor_sources SET last_run_at=?, last_status=?, last_error=? WHERE source_id=?",
            (now, status, error, source_id))
    conn.commit()
