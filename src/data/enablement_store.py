"""Enablement document + card-draft store (the ETL Transform/Load core).

Persists every pulled document (FULL text) to the local warehouse so sources
and drafts survive sessions and the Claude/Gemini chat can "look up an old
document". Card drafts reuse the existing ``guru_content_drafts`` table
(pending → pushed lifecycle) linked to a document via ``source_ref``.

All functions take a ``sqlite3.Connection`` from
``connection_factory.get_connection()``.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from src.data.connection_factory import atomic

_PROMPTS = Path(__file__).resolve().parent.parent.parent / "config" / "prompts"
_CARD_PROMPT = _PROMPTS / "enablement_card_from_doc.txt"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _excerpt(text: str, n: int = 280) -> str:
    flat = re.sub(r"\s+", " ", text or "").strip()
    return flat[:n]


def _hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


# ── documents ────────────────────────────────────────────────────────

def save_document(
    conn: sqlite3.Connection,
    *,
    source: str,
    name: str,
    doc_id: str | None = None,
    source_ref: str | None = None,
    mime_type: str = "",
    web_url: str = "",
    modified_time: str | None = None,
    full_text: str = "",
    due_dates: list[dict] | None = None,
) -> str:
    """Insert or update a locally-stored document. Returns the doc_id.

    doc_id defaults to source_ref (e.g. the Drive fileId) or a fresh uuid.
    Re-indexing the same doc_id updates the row in place (no duplicate).
    """
    doc_id = doc_id or source_ref or uuid.uuid4().hex
    now = _now()
    with atomic(conn):
        conn.execute(
            """INSERT INTO enablement_documents
               (doc_id, source, source_ref, name, mime_type, web_url, modified_time,
                content_hash, full_text, text_excerpt, due_dates_json, indexed_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(doc_id) DO UPDATE SET
                 name=excluded.name, mime_type=excluded.mime_type, web_url=excluded.web_url,
                 modified_time=excluded.modified_time, content_hash=excluded.content_hash,
                 full_text=excluded.full_text, text_excerpt=excluded.text_excerpt,
                 due_dates_json=excluded.due_dates_json, updated_at=excluded.updated_at""",
            (doc_id, source, source_ref or doc_id, name, mime_type, web_url, modified_time,
             _hash(full_text), full_text, _excerpt(full_text),
             json.dumps(due_dates or []), now, now),
        )
    return doc_id


def get_document(conn: sqlite3.Connection, doc_id: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM enablement_documents WHERE doc_id = ?", (doc_id,)
    ).fetchone()
    return dict(row) if row else None


def search_documents(conn: sqlite3.Connection, query: str, *, limit: int = 20) -> list[dict]:
    """Find locally-stored documents by name or body. Powers the chat lookup.

    Returns lightweight rows (no full_text) with a matching snippet, newest
    document first.
    """
    like = f"%{query.strip()}%"
    rows = conn.execute(
        """SELECT doc_id, source, name, mime_type, web_url, modified_time,
                  text_excerpt, due_dates_json, card_draft_id, indexed_at
           FROM enablement_documents
           WHERE name LIKE ? OR full_text LIKE ?
           ORDER BY COALESCE(modified_time, indexed_at) DESC
           LIMIT ?""",
        (like, like, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def list_documents(conn: sqlite3.Connection, *, limit: int = 100) -> list[dict]:
    rows = conn.execute(
        """SELECT doc_id, source, name, mime_type, web_url, modified_time,
                  text_excerpt, card_draft_id, indexed_at
           FROM enablement_documents
           ORDER BY COALESCE(modified_time, indexed_at) DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


# ── card drafts (reuse guru_content_drafts) ──────────────────────────

def save_card_draft(
    conn: sqlite3.Connection,
    *,
    title: str,
    content: str,
    source_ref: str | None = None,
    draft_type: str = "new_article",
    status: str = "pending",
) -> int:
    """Persist a generated card draft into guru_content_drafts. Returns its id."""
    now = _now()
    with atomic(conn):
        cur = conn.execute(
            """INSERT INTO guru_content_drafts
               (card_id, friction_type, draft_type, title, content,
                source_tickets, status, created_at, source_ref)
               VALUES ('', '', ?, ?, ?, '', ?, ?, ?)""",
            (draft_type, title, content, status, now, source_ref),
        )
    return cur.lastrowid


def get_draft(conn: sqlite3.Connection, draft_id: int) -> dict | None:
    row = conn.execute(
        "SELECT * FROM guru_content_drafts WHERE id = ?", (draft_id,)
    ).fetchone()
    return dict(row) if row else None


def list_drafts(conn: sqlite3.Connection, *, status: str | None = None, limit: int = 100) -> list[dict]:
    if status:
        rows = conn.execute(
            "SELECT * FROM guru_content_drafts WHERE status = ? ORDER BY created_at DESC LIMIT ?",
            (status, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM guru_content_drafts ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def search_drafts(conn: sqlite3.Connection, query: str, *, limit: int = 20) -> list[dict]:
    like = f"%{query.strip()}%"
    rows = conn.execute(
        """SELECT id, title, draft_type, status, source_ref, created_at,
                  substr(content, 1, 280) AS snippet
           FROM guru_content_drafts
           WHERE title LIKE ? OR content LIKE ?
           ORDER BY created_at DESC LIMIT ?""",
        (like, like, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def update_draft_content(
    conn: sqlite3.Connection,
    draft_id: int,
    *,
    title: str | None = None,
    content: str,
) -> dict:
    """Replace a draft's body (and optionally title) in one atomic UPDATE.

    Lets the chat ``revise_draft`` tool rewrite a draft directly on the dispatch
    path — one BEGIN/COMMIT, no nested transaction.
    """
    with atomic(conn):
        if title is not None:
            conn.execute(
                "UPDATE guru_content_drafts SET title=?, content=? WHERE id=?",
                (title, content, draft_id),
            )
        else:
            conn.execute(
                "UPDATE guru_content_drafts SET content=? WHERE id=?",
                (content, draft_id),
            )
    return {"id": draft_id, "title": title, "content": content}


def set_draft_card_id(conn: sqlite3.Connection, draft_id: int, card_id: str) -> None:
    """Point a draft at an existing Guru card so publish_draft updates it."""
    with atomic(conn):
        conn.execute(
            "UPDATE guru_content_drafts SET card_id=? WHERE id=?", (card_id, draft_id)
        )


# ── generation + publish ─────────────────────────────────────────────

def _parse_card(response: str, fallback_title: str) -> tuple[str, str]:
    """Parse a 'TITLE: ...\\n---\\n<body>' LLM response into (title, body)."""
    lines = (response or "").strip().split("\n")
    title, start = fallback_title, 0
    for i, line in enumerate(lines):
        s = line.strip()
        if s.upper().startswith("TITLE:"):
            title = s.split(":", 1)[1].strip()
            start = i + 1
        elif s == "---":
            start = i + 1
            break
    body = "\n".join(lines[start:]).strip()
    return title, (body or (response or "").strip())


def draft_card_from_document(
    conn: sqlite3.Connection,
    doc_id: str,
    llm_client,
    *,
    collection: str = "Enablement",
) -> dict:
    """Generate a Guru card draft from a stored document via the LLM client.

    Writes the draft to guru_content_drafts (status='pending') and links it to
    the document. Returns the draft dict. The llm_client only needs a
    ``.generate(prompt)`` method (real or mock).
    """
    doc = get_document(conn, doc_id)
    if not doc:
        raise ValueError(f"Document {doc_id} not found")

    template = _CARD_PROMPT.read_text(encoding="utf-8") if _CARD_PROMPT.exists() else (
        "Turn this document into a Guru card.\nSOURCE: {doc_name}\n{doc_text}\n"
        "Return:\nTITLE: <title>\n---\n<body>"
    )
    prompt = template.format(
        doc_name=doc.get("name", "Untitled"),
        doc_text=doc.get("full_text", ""),
        collection=collection,
    )
    response = llm_client.generate(prompt)
    title, content = _parse_card(response, fallback_title=doc.get("name", "Untitled"))

    # Idempotent: if this doc already has a draft, refresh it in place rather
    # than spawning a duplicate. A draft already pushed to Guru is left intact.
    existing_id = doc.get("card_draft_id")
    if existing_id:
        ex = get_draft(conn, int(existing_id))
        if ex:
            if ex.get("status") == "pushed":
                return {"id": int(existing_id), "title": ex["title"], "content": ex["content"],
                        "status": "pushed", "source_ref": doc_id}
            with atomic(conn):
                conn.execute(
                    "UPDATE guru_content_drafts SET title = ?, content = ? WHERE id = ?",
                    (title, content, int(existing_id)),
                )
            return {"id": int(existing_id), "title": title, "content": content,
                    "status": "pending", "source_ref": doc_id}

    draft_id = save_card_draft(conn, title=title, content=content, source_ref=doc_id)
    with atomic(conn):
        conn.execute(
            "UPDATE enablement_documents SET card_draft_id = ? WHERE doc_id = ?",
            (draft_id, doc_id),
        )
    return {"id": draft_id, "title": title, "content": content,
            "status": "pending", "source_ref": doc_id}


def publish_draft(
    conn: sqlite3.Connection,
    draft_id: int,
    *,
    guru_client=None,
    collection_id: str | None = None,
    approved_by: str = "user",
) -> dict:
    """Publish an approved draft to Guru and mark it pushed.

    Review-then-publish: callers gate this behind operator approval. If a
    guru_client is supplied it creates/updates the card; otherwise the draft is
    marked pushed for local/offline flows (and the simulation).
    """
    draft = get_draft(conn, draft_id)
    if not draft:
        return {"ok": False, "error": "draft_not_found", "draft_id": draft_id}
    if draft["status"] == "pushed":
        return {"ok": True, "draft_id": draft_id, "status": "pushed", "already": True}

    card_id = draft.get("card_id") or ""
    guru_result = None
    if guru_client is not None:
        try:
            if card_id:
                guru_result = guru_client.update_card(card_id, draft["content"], draft["title"])
            else:
                guru_result = guru_client.create_card(
                    collection_id or "", draft["title"], draft["content"]
                )
                if isinstance(guru_result, dict):
                    card_id = guru_result.get("id", card_id)
        except Exception as exc:  # noqa: BLE001 — surface as a failed publish
            return {"ok": False, "error": f"guru_push_failed: {exc}", "draft_id": draft_id}

    now = _now()
    with atomic(conn):
        conn.execute(
            "UPDATE guru_content_drafts SET status='pushed', approved_by=?, pushed_at=?, "
            "card_id=COALESCE(NULLIF(card_id,''), ?) WHERE id=?",
            (approved_by, now, card_id, draft_id),
        )
    return {"ok": True, "draft_id": draft_id, "status": "pushed",
            "card_id": card_id, "guru_result": guru_result}
