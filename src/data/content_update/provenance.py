"""Audit trail for card updates — the 'why / who / when' of every change.

Recorded at stage time (source + change plan + the changes the grounding gate
rejected) and finalized at publish (approver + timestamp). The published row is
also the anchor the effectiveness measure reads from.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from src.data.connection_factory import atomic

_DDL = """
CREATE TABLE IF NOT EXISTS card_update_provenance (
    draft_id      INTEGER PRIMARY KEY,
    card_id       TEXT,
    source_ref    TEXT DEFAULT '',
    source_title  TEXT DEFAULT '',
    summary       TEXT DEFAULT '',
    changes_json  TEXT DEFAULT '[]',
    dropped_json  TEXT DEFAULT '[]',
    issues_json   TEXT DEFAULT '[]',
    status        TEXT DEFAULT 'staged',
    approved_by   TEXT DEFAULT '',
    created_at    TEXT DEFAULT '',
    pushed_at     TEXT DEFAULT ''
)
"""


def ensure_table(conn) -> None:
    conn.execute(_DDL)
    conn.commit()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def record_proposal(conn, draft_id, card_id, *, source_ref="", source_title="",
                    summary="", changes=None, dropped=None, issues=None) -> None:
    """Stage-time provenance: what this update intends to change and from where."""
    ensure_table(conn)
    with atomic(conn):
        conn.execute(
            """INSERT INTO card_update_provenance
               (draft_id,card_id,source_ref,source_title,summary,
                changes_json,dropped_json,issues_json,status,created_at)
               VALUES (?,?,?,?,?,?,?,?,'staged',?)
               ON CONFLICT(draft_id) DO UPDATE SET
                 card_id=excluded.card_id, source_ref=excluded.source_ref,
                 source_title=excluded.source_title, summary=excluded.summary,
                 changes_json=excluded.changes_json, dropped_json=excluded.dropped_json,
                 issues_json=excluded.issues_json""",
            (int(draft_id), card_id or "", source_ref or "", source_title or "", summary or "",
             json.dumps(changes or []), json.dumps(dropped or []),
             json.dumps(issues or []), _now()))


def record_from_result(conn, draft_id, card_id, src, plan, issues) -> None:
    """Convenience: extract provenance from the pipeline objects (never raises)."""
    try:
        record_proposal(
            conn, draft_id, card_id,
            source_ref=(src.primary.ref if src and src.primary else ""),
            source_title=(src.primary.title if src and src.primary else ""),
            summary=getattr(plan, "summary", ""),
            changes=[{"type": c.type, "section": c.section, "reason": c.reason}
                     for c in getattr(plan, "changes", [])],
            dropped=getattr(plan, "dropped", []), issues=issues or [])
    except Exception:  # noqa: BLE001 — provenance must never break the update
        pass


def finalize_publish(conn, draft_id, *, approved_by="user", card_id=None) -> None:
    """Push-time: stamp approver + timestamp; this row anchors effectiveness."""
    ensure_table(conn)
    with atomic(conn):
        if card_id:
            conn.execute(
                "UPDATE card_update_provenance SET status='published', approved_by=?, "
                "pushed_at=?, card_id=COALESCE(NULLIF(card_id,''),?) WHERE draft_id=?",
                (approved_by, _now(), card_id, int(draft_id)))
        else:
            conn.execute(
                "UPDATE card_update_provenance SET status='published', approved_by=?, "
                "pushed_at=? WHERE draft_id=?",
                (approved_by, _now(), int(draft_id)))


def get(conn, draft_id):
    ensure_table(conn)
    r = conn.execute("SELECT * FROM card_update_provenance WHERE draft_id=?",
                     (int(draft_id),)).fetchone()
    return _row(r) if r else None


def history(conn, card_id, *, limit=20) -> list[dict]:
    ensure_table(conn)
    rows = conn.execute(
        "SELECT * FROM card_update_provenance WHERE card_id=? "
        "ORDER BY COALESCE(NULLIF(pushed_at,''), created_at) DESC LIMIT ?",
        (card_id, limit)).fetchall()
    return [_row(r) for r in rows]


def _row(r) -> dict:
    return {"draft_id": r["draft_id"], "card_id": r["card_id"],
            "source_ref": r["source_ref"], "source_title": r["source_title"],
            "summary": r["summary"], "changes": json.loads(r["changes_json"] or "[]"),
            "dropped": json.loads(r["dropped_json"] or "[]"),
            "issues": json.loads(r["issues_json"] or "[]"),
            "status": r["status"], "approved_by": r["approved_by"],
            "created_at": r["created_at"], "pushed_at": r["pushed_at"]}
