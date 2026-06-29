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
    """Push-time: stamp approver + timestamp; this row anchors effectiveness.

    Upsert, not update-only: drafts pushed via paths that never staged a
    proposal row (Drive scans, workbench imports, direct UI/chat pushes of an
    imported card) have no prior row, so a plain UPDATE would match nothing and
    leave the published card with no audit trail / effectiveness anchor. When the
    UPDATE touches no row we INSERT a minimal published anchor instead.
    """
    ensure_table(conn)
    now = _now()
    with atomic(conn):
        if card_id:
            cur = conn.execute(
                "UPDATE card_update_provenance SET status='published', approved_by=?, "
                "pushed_at=?, card_id=COALESCE(NULLIF(card_id,''),?) WHERE draft_id=?",
                (approved_by, now, card_id, int(draft_id)))
        else:
            cur = conn.execute(
                "UPDATE card_update_provenance SET status='published', approved_by=?, "
                "pushed_at=? WHERE draft_id=?",
                (approved_by, now, int(draft_id)))
        if cur.rowcount == 0:
            conn.execute(
                """INSERT INTO card_update_provenance
                   (draft_id, card_id, status, approved_by, created_at, pushed_at)
                   VALUES (?,?,'published',?,?,?)
                   ON CONFLICT(draft_id) DO UPDATE SET
                     status='published', approved_by=excluded.approved_by,
                     pushed_at=excluded.pushed_at,
                     card_id=COALESCE(NULLIF(card_update_provenance.card_id,''), excluded.card_id)""",
                (int(draft_id), card_id or "", approved_by, now, now))


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


def _latest(conn, card_id):
    """The most recent provenance row for a card (published or staged)."""
    ensure_table(conn)
    return conn.execute(
        "SELECT * FROM card_update_provenance WHERE card_id=? "
        "ORDER BY COALESCE(NULLIF(pushed_at,''), created_at) DESC LIMIT 1",
        (card_id,)).fetchone()


def _change_evidence(c) -> dict:
    """Normalize one stored change into a reviewer row (evidence may be absent)."""
    if not isinstance(c, dict):
        return {"type": "", "section": "", "reason": "", "evidence": str(c)}
    return {"type": c.get("type", ""), "section": c.get("section", ""),
            "reason": c.get("reason", ""), "evidence": c.get("evidence", "")}


def _empty_pack(card_id=None) -> dict:
    """Reviewer pack for a card/draft with no recorded provenance."""
    return {"card_id": card_id, "source_ref": "", "source_title": "",
            "changes": [], "dropped": [], "issues": [],
            "pushed_at": "", "approved_by": ""}


def evidence_pack(conn, *, card_id=None, draft_id=None) -> dict:
    """Reviewer-facing pack: source + per-change evidence for one update.

    By ``card_id`` reshapes the latest published-or-staged row; by ``draft_id``
    reshapes that exact draft. Reads ONLY ``card_update_provenance`` (no tickets).
    A missing card/draft returns an empty-but-well-formed pack.
    """
    if draft_id is not None:
        rec = get(conn, draft_id)
        if not rec:
            return _empty_pack()
        return _pack(rec)
    row = _latest(conn, card_id)
    if not row:
        return _empty_pack(card_id)
    return _pack(_row(row))


def _pack(rec: dict) -> dict:
    """Reshape one provenance dict into the reviewer pack."""
    return {
        "card_id": rec["card_id"],
        "source_ref": rec["source_ref"],
        "source_title": rec["source_title"],
        "changes": [_change_evidence(c) for c in rec["changes"]],
        "dropped": rec["dropped"],
        "issues": rec["issues"],
        "pushed_at": rec["pushed_at"],
        "approved_by": rec["approved_by"],
    }


def _row(r) -> dict:
    return {"draft_id": r["draft_id"], "card_id": r["card_id"],
            "source_ref": r["source_ref"], "source_title": r["source_title"],
            "summary": r["summary"], "changes": json.loads(r["changes_json"] or "[]"),
            "dropped": json.loads(r["dropped_json"] or "[]"),
            "issues": json.loads(r["issues_json"] or "[]"),
            "status": r["status"], "approved_by": r["approved_by"],
            "created_at": r["created_at"], "pushed_at": r["pushed_at"]}
