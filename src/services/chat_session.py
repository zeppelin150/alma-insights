"""
Alma Insights — Chat Session Manager (Build 12.0 — Chat Data Layer)

CRUD operations for chat_sessions + chat_messages tables.
Messages are stored as individual rows in chat_messages (migration 009).
"""

import json
import uuid
import logging
from datetime import datetime

logger = logging.getLogger("alma.chat_session")


def create_session(
    source_page: str,
    source_context: dict | None = None,
    filters: dict | None = None,
    conn=None,
) -> str:
    """Create a new chat session.  Returns the session_id."""
    session_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()
    f = filters or {}

    conn.execute(
        """
        INSERT INTO chat_sessions
        (session_id, created_at, updated_at, source_page, source_context,
         trc_filter, date_start, date_end, messages, title)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            session_id, now, now,
            source_page,
            json.dumps(source_context) if source_context else None,
            f.get("trc_filter"),
            f.get("date_start"),
            f.get("date_end"),
            json.dumps([]),  # keep legacy column populated for backward compat
            None,
        ),
    )
    conn.commit()
    logger.info("Created chat session %s (page=%s)", session_id, source_page)
    return session_id


def _session_exists(session_id: str, conn) -> bool:
    try:
        row = conn.execute(
            "SELECT 1 FROM chat_sessions WHERE session_id = ?",
            (session_id,)).fetchone()
        return row is not None
    except Exception:  # noqa: BLE001 — table missing / bad conn
        return False


def resolve_or_create_session(source_page: str, conn, *, pointer_dir=None) -> str:
    """Return the CURRENT enablement session, creating one only if none exists.

    The Agent page and the Workbench assistant panel are the same assistant and
    must share one transcript. They each used to mint their own session via
    ``create_session``, giving two divergent histories and a last-writer-wins
    fight over the ``.current_chat_session`` pointer (finding 5). This resolves
    a single shared session: the first surface to ask creates it and writes the
    pointer; the other reads the pointer and REUSES it.

    ``pointer_dir`` is the directory holding ``.current_chat_session`` (next to
    the DB the engine/tools read). When omitted, no pointer is consulted and a
    fresh session is created (legacy callers keep their behaviour).
    """
    from pathlib import Path
    pointer = None
    if pointer_dir is not None:
        pointer = Path(pointer_dir) / ".current_chat_session"
        try:
            if pointer.exists():
                existing = pointer.read_text(encoding="utf-8").strip()
                if existing and _session_exists(existing, conn):
                    return existing
        except Exception:  # noqa: BLE001 — a bad pointer just means "create one"
            pass
    session_id = create_session(source_page, conn=conn)
    if pointer is not None:
        try:
            pointer.write_text(session_id, encoding="utf-8")
        except Exception:  # noqa: BLE001 — pointer is best-effort
            pass
    return session_id


def append_message(
    session_id: str,
    role: str,
    content: str,
    conn=None,
    tool_calls: list | None = None,
    *,
    model_used: str | None = None,
    tokens_in: int | None = None,
    tokens_out: int | None = None,
    cost_usd: float | None = None,
    latency_ms: int | None = None,
    tool_round: int = 0,
    error_code: str | None = None,
    error_message: str | None = None,
    metadata: dict | None = None,
) -> str | None:
    """Append a message to a chat session. Returns the message_id."""
    now = datetime.utcnow().isoformat()

    # Verify session exists
    row = conn.execute(
        "SELECT session_id FROM chat_sessions WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    if row is None:
        logger.warning("Chat session not found: %s", session_id)
        return None

    message_id = None

    # Try inserting into chat_messages (migration 009+)
    try:
        ord_row = conn.execute(
            "SELECT COALESCE(MAX(ordinal), -1) FROM chat_messages WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        ordinal = (ord_row[0] if isinstance(ord_row, tuple) else ord_row[0]) + 1

        message_id = str(uuid.uuid4())

        conn.execute(
            """INSERT INTO chat_messages
               (message_id, session_id, ordinal, role, content, created_at,
                model_used, tokens_in, tokens_out, cost_usd, latency_ms,
                tool_calls, tool_round, error_code, error_message, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                message_id, session_id, ordinal, role, content, now,
                model_used, tokens_in, tokens_out, cost_usd, latency_ms,
                json.dumps(tool_calls) if tool_calls else None,
                tool_round, error_code, error_message,
                json.dumps(metadata) if metadata else None,
            ),
        )
    except Exception:
        # Pre-migration-009 database — chat_messages table doesn't exist
        message_id = None

    # Also update legacy JSON blob (always, for backward compat)
    _update_legacy_blob(session_id, role, content, now, tool_calls, conn)

    # Touch updated_at on parent session
    conn.execute(
        "UPDATE chat_sessions SET updated_at = ? WHERE session_id = ?",
        (now, session_id),
    )
    conn.commit()
    return message_id


def _update_legacy_blob(session_id, role, content, timestamp, tool_calls, conn):
    """Keep the JSON blob in chat_sessions.messages in sync (backward compat)."""
    try:
        row = conn.execute(
            "SELECT messages FROM chat_sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        raw = row[0] if isinstance(row, tuple) else row["messages"]
        messages = json.loads(raw) if raw else []
        msg = {"role": role, "content": content, "timestamp": timestamp}
        if tool_calls:
            msg["tool_calls"] = tool_calls
        messages.append(msg)
        conn.execute(
            "UPDATE chat_sessions SET messages = ? WHERE session_id = ?",
            (json.dumps(messages), session_id),
        )
    except Exception:
        pass  # Non-critical — the authoritative data is in chat_messages


def list_sessions(limit: int = 20, conn=None, source_page: str | None = None) -> list[dict]:
    """Return recent chat sessions with summary stats.

    Tries the v_session_summary view first (migration 009+).
    Falls back to raw chat_sessions for pre-009 databases.
    ``source_page`` optionally restricts to one origin (e.g. ``'enablement'``)
    so the LIMIT applies *after* scoping — no over-fetch needed.
    """
    where = "WHERE source_page = ?" if source_page else ""
    params = (source_page, limit) if source_page else (limit,)
    try:
        rows = conn.execute(
            f"""SELECT session_id, created_at, updated_at, source_page, title,
                      project_id, message_count, user_messages,
                      assistant_messages, total_tokens_in, total_tokens_out,
                      total_cost, last_message_at, first_question,
                      filter_json, ticket_count
               FROM v_session_summary
               {where}
               ORDER BY COALESCE(last_message_at, updated_at) DESC
               LIMIT ?""",
            params,
        ).fetchall()
        return [
            {
                "session_id": r[0] if isinstance(r, tuple) else r["session_id"],
                "created_at": r[1] if isinstance(r, tuple) else r["created_at"],
                "updated_at": r[2] if isinstance(r, tuple) else r["updated_at"],
                "source_page": r[3] if isinstance(r, tuple) else r["source_page"],
                "title": r[4] if isinstance(r, tuple) else r["title"],
                "project_id": r[5] if isinstance(r, tuple) else r["project_id"],
                "message_count": r[6] if isinstance(r, tuple) else r["message_count"],
                "user_messages": r[7] if isinstance(r, tuple) else r["user_messages"],
                "assistant_messages": r[8] if isinstance(r, tuple) else r["assistant_messages"],
                "total_tokens_in": r[9] if isinstance(r, tuple) else r["total_tokens_in"],
                "total_tokens_out": r[10] if isinstance(r, tuple) else r["total_tokens_out"],
                "total_cost": r[11] if isinstance(r, tuple) else r["total_cost"],
                "last_message_at": r[12] if isinstance(r, tuple) else r["last_message_at"],
                "first_question": r[13] if isinstance(r, tuple) else r["first_question"],
                "filter_json": r[14] if isinstance(r, tuple) else r["filter_json"],
                "ticket_count": r[15] if isinstance(r, tuple) else r["ticket_count"],
            }
            for r in rows
        ]
    except Exception:
        # Fallback for pre-009 databases
        return _list_sessions_legacy(limit, conn, source_page)


def _list_sessions_legacy(limit: int, conn, source_page: str | None = None) -> list[dict]:
    """Pre-migration-009 fallback."""
    where = "WHERE source_page = ?" if source_page else ""
    params = (source_page, limit) if source_page else (limit,)
    rows = conn.execute(
        f"""SELECT session_id, created_at, updated_at, source_page, title,
                  trc_filter, date_start, date_end
           FROM chat_sessions
           {where}
           ORDER BY updated_at DESC LIMIT ?""",
        params,
    ).fetchall()
    return [
        {
            "session_id": r[0] if isinstance(r, tuple) else r["session_id"],
            "created_at": r[1] if isinstance(r, tuple) else r["created_at"],
            "updated_at": r[2] if isinstance(r, tuple) else r["updated_at"],
            "source_page": r[3] if isinstance(r, tuple) else r["source_page"],
            "title": r[4] if isinstance(r, tuple) else r["title"],
            "trc_filter": r[5] if isinstance(r, tuple) else r["trc_filter"],
            "date_start": r[6] if isinstance(r, tuple) else r["date_start"],
            "date_end": r[7] if isinstance(r, tuple) else r["date_end"],
        }
        for r in rows
    ]


def load_session(session_id: str, conn=None) -> dict | None:
    """Load a full chat session including messages from chat_messages table."""
    row = conn.execute(
        "SELECT * FROM chat_sessions WHERE session_id = ?",
        (session_id,),
    ).fetchone()

    if row is None:
        return None

    result = {
        "session_id": row[1] if isinstance(row, tuple) else row["session_id"],
        "created_at": row[2] if isinstance(row, tuple) else row["created_at"],
        "updated_at": row[3] if isinstance(row, tuple) else row["updated_at"],
        "source_page": row[4] if isinstance(row, tuple) else row["source_page"],
        "source_context": json.loads(row[5] if isinstance(row, tuple) else row["source_context"])
            if (row[5] if isinstance(row, tuple) else row["source_context"]) else None,
        "trc_filter": row[6] if isinstance(row, tuple) else row["trc_filter"],
        "date_start": row[7] if isinstance(row, tuple) else row["date_start"],
        "date_end": row[8] if isinstance(row, tuple) else row["date_end"],
        "title": row[10] if isinstance(row, tuple) else row["title"],
    }

    # Load messages from chat_messages table (migration 009)
    messages = _load_messages_from_table(session_id, conn)
    if not messages:
        # Fallback: read from legacy JSON blob
        raw = row[9] if isinstance(row, tuple) else row["messages"]
        messages = json.loads(raw) if raw else []
    result["messages"] = messages

    # Migration 006 columns
    try:
        result["filter_json"] = json.loads(row[11] if isinstance(row, tuple) else row["filter_json"]) \
            if (row[11] if isinstance(row, tuple) else row["filter_json"]) else {}
        result["ticket_count"] = row[12] if isinstance(row, tuple) else row["ticket_count"]
        result["active_report_ids"] = json.loads(row[13] if isinstance(row, tuple) else row["active_report_ids"]) \
            if (row[13] if isinstance(row, tuple) else row["active_report_ids"]) else []
    except (IndexError, TypeError, KeyError):
        result["filter_json"] = {}
        result["ticket_count"] = None
        result["active_report_ids"] = []

    return result


def delete_session(session_id: str, conn=None) -> bool:
    """Delete a chat session and everything under it (cascade).

    FK enforcement is ON and these tables carry no ON DELETE CASCADE, so children
    are removed before the parent: tool executions → messages (the FTS5 delete
    trigger keeps ``chat_messages_fts`` in sync) → the session row. Child deletes
    are guarded so a pre-009 database (no ``chat_messages``) still drops the
    session row. Returns True if a session row existed and was removed.
    """
    from src.data.connection_factory import atomic

    row = conn.execute(
        "SELECT session_id FROM chat_sessions WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    if row is None:
        return False

    with atomic(conn):
        for table in ("chat_tool_executions", "chat_messages"):
            try:
                conn.execute(
                    f"DELETE FROM {table} WHERE session_id = ?", (session_id,)
                )
            except Exception:  # pre-009 db without this table — nothing to orphan
                pass
        conn.execute(
            "DELETE FROM chat_sessions WHERE session_id = ?", (session_id,)
        )
    logger.info("Deleted chat session %s (cascade)", session_id)
    return True


def _load_messages_from_table(session_id: str, conn) -> list[dict]:
    """Load messages from chat_messages table, returning legacy-compatible dicts."""
    try:
        rows = conn.execute(
            """SELECT message_id, ordinal, role, content, created_at,
                      model_used, tokens_in, tokens_out, cost_usd, latency_ms,
                      tool_calls, tool_round
               FROM chat_messages
               WHERE session_id = ?
               ORDER BY ordinal""",
            (session_id,),
        ).fetchall()
        if not rows:
            return []
        messages = []
        for r in rows:
            msg = {
                "role": r[2] if isinstance(r, tuple) else r["role"],
                "content": r[3] if isinstance(r, tuple) else r["content"],
                "timestamp": r[4] if isinstance(r, tuple) else r["created_at"],
                "message_id": r[0] if isinstance(r, tuple) else r["message_id"],
            }
            tc = r[10] if isinstance(r, tuple) else r["tool_calls"]
            if tc:
                try:
                    msg["tool_calls"] = json.loads(tc)
                except (json.JSONDecodeError, TypeError):
                    pass
            # Include telemetry fields if present
            model = r[5] if isinstance(r, tuple) else r["model_used"]
            if model:
                msg["model_used"] = model
            tokens_in = r[6] if isinstance(r, tuple) else r["tokens_in"]
            if tokens_in is not None:
                msg["tokens_in"] = tokens_in
            tokens_out = r[7] if isinstance(r, tuple) else r["tokens_out"]
            if tokens_out is not None:
                msg["tokens_out"] = tokens_out
            cost = r[8] if isinstance(r, tuple) else r["cost_usd"]
            if cost is not None:
                msg["cost_usd"] = cost
            latency = r[9] if isinstance(r, tuple) else r["latency_ms"]
            if latency is not None:
                msg["latency_ms"] = latency
            messages.append(msg)
        return messages
    except Exception:
        return []


# ═══════════════════════════════════════
#  Session filter management (Migration 006)
# ═══════════════════════════════════════

def get_session_filters(session_id: str, conn=None) -> dict:
    """Load session-scoped filters as a dict."""
    row = conn.execute(
        "SELECT filter_json FROM chat_sessions WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    if row is None or (row[0] if isinstance(row, tuple) else row["filter_json"]) is None:
        return {}
    try:
        return json.loads(row[0] if isinstance(row, tuple) else row["filter_json"])
    except (json.JSONDecodeError, TypeError):
        return {}


def set_session_filters(session_id: str, filters: dict, conn=None) -> None:
    """Save session-scoped filters."""
    now = datetime.utcnow().isoformat()
    conn.execute(
        "UPDATE chat_sessions SET filter_json = ?, updated_at = ? "
        "WHERE session_id = ?",
        (json.dumps(filters), now, session_id),
    )
    conn.commit()


def set_session_ticket_count(session_id: str, count: int, conn=None) -> None:
    """Update the cached ticket count for a session."""
    now = datetime.utcnow().isoformat()
    conn.execute(
        "UPDATE chat_sessions SET ticket_count = ?, updated_at = ? "
        "WHERE session_id = ?",
        (count, now, session_id),
    )
    conn.commit()


def get_active_report_ids(session_id: str, conn=None) -> list[str]:
    """Get list of active report IDs for a session."""
    row = conn.execute(
        "SELECT active_report_ids FROM chat_sessions WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    if row is None or (row[0] if isinstance(row, tuple) else row["active_report_ids"]) is None:
        return []
    try:
        return json.loads(row[0] if isinstance(row, tuple) else row["active_report_ids"])
    except (json.JSONDecodeError, TypeError):
        return []


def add_active_report_id(session_id: str, report_id: str, conn=None) -> None:
    """Add a report ID to the session's active reports list."""
    current = get_active_report_ids(session_id, conn=conn)
    if report_id not in current:
        current.append(report_id)
    now = datetime.utcnow().isoformat()
    conn.execute(
        "UPDATE chat_sessions SET active_report_ids = ?, updated_at = ? "
        "WHERE session_id = ?",
        (json.dumps(current), now, session_id),
    )
    conn.commit()


# ═══════════════════════════════════════
#  Project management (Migration 009)
# ═══════════════════════════════════════

def create_project(name: str, description: str | None = None, conn=None) -> str:
    """Create a new chat project. Returns the project_id."""
    project_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()
    conn.execute(
        """INSERT INTO chat_projects
           (project_id, name, description, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?)""",
        (project_id, name, description, now, now),
    )
    conn.commit()
    logger.info("Created chat project %s (%s)", project_id, name)
    return project_id


def assign_session_to_project(
    session_id: str, project_id: str | None, conn=None
) -> None:
    """Assign a session to a project (or None to unassign)."""
    now = datetime.utcnow().isoformat()
    conn.execute(
        "UPDATE chat_sessions SET project_id = ?, updated_at = ? WHERE session_id = ?",
        (project_id, now, session_id),
    )
    conn.commit()


def list_projects(conn=None) -> list[dict]:
    """List all chat projects with session counts."""
    try:
        rows = conn.execute(
            """SELECT p.project_id, p.name, p.description, p.sort_order,
                      p.created_at, p.updated_at,
                      COUNT(s.session_id) AS session_count,
                      MAX(s.updated_at) AS last_active
               FROM chat_projects p
               LEFT JOIN chat_sessions s ON s.project_id = p.project_id
               GROUP BY p.project_id
               ORDER BY p.sort_order, p.name""",
        ).fetchall()
        return [
            {
                "project_id": r[0] if isinstance(r, tuple) else r["project_id"],
                "name": r[1] if isinstance(r, tuple) else r["name"],
                "description": r[2] if isinstance(r, tuple) else r["description"],
                "sort_order": r[3] if isinstance(r, tuple) else r["sort_order"],
                "created_at": r[4] if isinstance(r, tuple) else r["created_at"],
                "updated_at": r[5] if isinstance(r, tuple) else r["updated_at"],
                "session_count": r[6] if isinstance(r, tuple) else r["session_count"],
                "last_active": r[7] if isinstance(r, tuple) else r["last_active"],
            }
            for r in rows
        ]
    except Exception:
        return []


# ═══════════════════════════════════════
#  Message queries (Migration 009)
# ═══════════════════════════════════════

def get_message(message_id: str, conn=None) -> dict | None:
    """Load a single message by ID."""
    row = conn.execute(
        "SELECT * FROM chat_messages WHERE message_id = ?",
        (message_id,),
    ).fetchone()
    if row is None:
        return None
    return {
        "message_id": row[0] if isinstance(row, tuple) else row["message_id"],
        "session_id": row[1] if isinstance(row, tuple) else row["session_id"],
        "ordinal": row[2] if isinstance(row, tuple) else row["ordinal"],
        "role": row[3] if isinstance(row, tuple) else row["role"],
        "content": row[4] if isinstance(row, tuple) else row["content"],
        "created_at": row[5] if isinstance(row, tuple) else row["created_at"],
        "model_used": row[6] if isinstance(row, tuple) else row["model_used"],
        "tokens_in": row[7] if isinstance(row, tuple) else row["tokens_in"],
        "tokens_out": row[8] if isinstance(row, tuple) else row["tokens_out"],
        "cost_usd": row[9] if isinstance(row, tuple) else row["cost_usd"],
        "latency_ms": row[10] if isinstance(row, tuple) else row["latency_ms"],
    }


def search_messages(query: str, limit: int = 20, conn=None,
                    source_page: str | None = None) -> list[dict]:
    """Full-text search across chat messages. Returns matching messages.

    ``source_page`` optionally restricts hits to one origin (e.g.
    ``'enablement'``) so search never leaks content from other modes.
    """
    try:
        where = "WHERE chat_messages_fts MATCH ?"
        params: list = [query]
        if source_page:
            where += " AND s.source_page = ?"
            params.append(source_page)
        params.append(limit)
        rows = conn.execute(
            f"""SELECT m.message_id, m.session_id, m.role, m.content,
                      m.created_at, s.title
               FROM chat_messages_fts fts
               JOIN chat_messages m ON m.rowid = fts.rowid
               JOIN chat_sessions s ON s.session_id = m.session_id
               {where}
               ORDER BY fts.rank
               LIMIT ?""",
            tuple(params),
        ).fetchall()
        return [
            {
                "message_id": r[0] if isinstance(r, tuple) else r["message_id"],
                "session_id": r[1] if isinstance(r, tuple) else r["session_id"],
                "role": r[2] if isinstance(r, tuple) else r["role"],
                "content": r[3] if isinstance(r, tuple) else r["content"],
                "created_at": r[4] if isinstance(r, tuple) else r["created_at"],
                "session_title": r[5] if isinstance(r, tuple) else r["title"],
            }
            for r in rows
        ]
    except Exception:
        return []
