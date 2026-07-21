"""KB local mirror store (WS2-M2, renn-calendar-kb-studio plan).

The SQLite twin of the EC Drive folder: kb_cards + the FTS5 index (kept in
sync by migration 046's triggers). Drive is source of truth for card CONTENT;
this mirror is a rebuildable cache that gives fast deterministic search and
sync bookkeeping. The store NEVER deletes anything in Drive.

Plain execute+commit — never atomic() (KBWorker threads + MCP subprocess).
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from datetime import datetime, timezone

logger = logging.getLogger("alma.kb.store")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def content_hash(meta: dict, body: str, *, extra: dict | None = None) -> str:
    basis_meta = {k: meta.get(k) for k in ("title", "type", "topics", "source_id",
                                           "source_modified", "summary", "key_facts")}
    # Include a human's custom fields so editing one is detected as a change
    # (otherwise a hand-edited extra field would be skipped as "unchanged").
    if extra:
        basis_meta["_extra"] = extra
    basis = json.dumps(basis_meta, sort_keys=True, default=str) + "\n" + (body or "")
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


def _row_to_dict(row) -> dict:
    out = {k: row[k] for k in row.keys()} if isinstance(row, sqlite3.Row) else dict(row)
    for key, col in (("topics", "topics_json"), ("key_facts", "key_facts_json")):
        try:
            out[key] = json.loads(out.get(col) or "[]")
        except (ValueError, TypeError):
            out[key] = []
    # Hand-added frontmatter fields preserved across the DB round-trip.
    try:
        out["extra"] = json.loads(out.get("extra_json") or "{}") or {}
    except (ValueError, TypeError):
        out["extra"] = {}
    return out


def upsert_card(conn, meta: dict, body: str, *, drive_file_id: str | None = None,
                topic_folder_id: str | None = None, drive_modified: str = "",
                status: str = "ok", extra: dict | None = None) -> dict:
    """Insert-or-update the mirror row for a card. Skips the write when the
    content hash is unchanged (the cheap idempotence backstop). Returns
    {"card_id", "changed": bool}.

    ``extra`` carries a human's unrecognised frontmatter keys (finding 17).
    ``None`` PRESERVES whatever is already stored — so a regeneration path that
    rebuilds meta from the canonical columns does not wipe the human's fields.
    Pass a dict (the PULL path does, from ``card_format.extract_extra``) to set
    them."""
    card_id = str(meta.get("card_id") or "").strip()
    if not card_id:
        raise ValueError("upsert_card requires meta['card_id']")
    new_hash = content_hash(meta, body, extra=extra)
    extra_json = json.dumps(extra) if extra else None
    row = conn.execute("SELECT content_hash FROM kb_cards WHERE card_id=?",
                       (card_id,)).fetchone()
    if row is not None and row[0] == new_hash and status == "ok":
        # Content unchanged — still refresh the sync bookkeeping fields.
        conn.execute(
            "UPDATE kb_cards SET drive_modified=COALESCE(NULLIF(?,''), drive_modified), "
            "synced_at=?, status='ok' WHERE card_id=?",
            (drive_modified, _now(), card_id))
        conn.commit()
        return {"card_id": card_id, "changed": False}
    fields = (
        card_id, drive_file_id,
        topic_folder_id,
        str(meta.get("title") or ""), str(meta.get("type") or "source_summary"),
        json.dumps(meta.get("topics") or []),
        str(meta.get("source_id") or ""), str(meta.get("source_url") or ""),
        str(meta.get("source_mime") or ""), str(meta.get("source_modified") or ""),
        str(meta.get("summary") or ""), json.dumps(meta.get("key_facts") or []),
        body or "", new_hash, drive_modified, _now(), status, extra_json,
    )
    try:
        conn.execute(
            """INSERT INTO kb_cards
               (card_id, drive_file_id, topic_folder_id, title, type, topics_json,
                source_id, source_url, source_mime, source_modified, summary,
                key_facts_json, body_md, content_hash, drive_modified, synced_at,
                status, extra_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(card_id) DO UPDATE SET
                 drive_file_id=COALESCE(excluded.drive_file_id, kb_cards.drive_file_id),
                 topic_folder_id=COALESCE(excluded.topic_folder_id, kb_cards.topic_folder_id),
                 title=excluded.title, type=excluded.type,
                 topics_json=excluded.topics_json, source_id=excluded.source_id,
                 source_url=excluded.source_url, source_mime=excluded.source_mime,
                 source_modified=excluded.source_modified, summary=excluded.summary,
                 key_facts_json=excluded.key_facts_json, body_md=excluded.body_md,
                 content_hash=excluded.content_hash,
                 drive_modified=CASE WHEN excluded.drive_modified != ''
                                     THEN excluded.drive_modified
                                     ELSE kb_cards.drive_modified END,
                 synced_at=excluded.synced_at, status=excluded.status,
                 extra_json=COALESCE(excluded.extra_json, kb_cards.extra_json)""",
            fields)
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001
            pass
        raise
    return {"card_id": card_id, "changed": True}


def get_card(conn, card_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM kb_cards WHERE card_id=?",
                       (card_id,)).fetchone()
    return _row_to_dict(row) if row is not None else None


def get_card_by_file(conn, drive_file_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM kb_cards WHERE drive_file_id=?",
                       (drive_file_id,)).fetchone()
    return _row_to_dict(row) if row is not None else None


def list_cards(conn, *, topic_folder_id: str | None = None,
               type: str | None = None, source_id: str | None = None,
               status: str | None = None, limit: int = 500) -> list[dict]:
    """COMPLETE enumeration (LIST-tool contract) with optional filters."""
    where, params = [], []
    if topic_folder_id:
        where.append("topic_folder_id = ?"); params.append(topic_folder_id)
    if type:
        where.append("type = ?"); params.append(type)
    if source_id:
        where.append("source_id = ?"); params.append(source_id)
    if status:
        where.append("status = ?"); params.append(status)
    sql = "SELECT * FROM kb_cards"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY synced_at DESC LIMIT ?"
    params.append(max(1, min(int(limit), 2000)))
    return [_row_to_dict(r) for r in conn.execute(sql, params).fetchall()]


def cards_count(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM kb_cards WHERE status != 'source_missing'"
                        ).fetchone()[0]


def fts_search(conn, match_expr: str, *, limit: int = 20) -> list[dict]:
    """Raw weighted-bm25 layer (title×3, topics×2, key_facts×2, summary×1,
    body×1). Callers sanitize the MATCH expression (kb.search owns that)."""
    rows = conn.execute(
        """SELECT c.*, bm25(kb_cards_fts, 3.0, 1.0, 2.0, 1.0, 2.0) AS rank
           FROM kb_cards_fts f JOIN kb_cards c ON c.rowid = f.rowid
           WHERE kb_cards_fts MATCH ? AND c.status != 'source_missing'
           ORDER BY rank LIMIT ?""",
        (match_expr, max(1, min(int(limit), 100)))).fetchall()
    return [_row_to_dict(r) for r in rows]


def mark_status(conn, card_id: str, status: str) -> bool:
    cur = conn.execute("UPDATE kb_cards SET status=?, synced_at=? WHERE card_id=?",
                       (status, _now(), card_id))
    conn.commit()
    return cur.rowcount > 0


def delete_card(conn, card_id: str) -> bool:
    """Mirror-only delete (a card removed from Drive by a human). The app
    NEVER deletes in Drive."""
    cur = conn.execute("DELETE FROM kb_cards WHERE card_id=?", (card_id,))
    conn.commit()
    return cur.rowcount > 0
