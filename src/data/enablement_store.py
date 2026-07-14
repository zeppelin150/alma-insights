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
    content_html: str | None = None,
    source_ref: str | None = None,
    draft_type: str = "new_article",
    status: str = "pending",
) -> int:
    """Persist a generated card draft into guru_content_drafts. Returns its id.

    ``content`` is the canonical markdown body. ``content_html`` is the
    optional rich-HTML representation used on the Guru publish path
    (carries color/highlight); NULL → publish derives it from markdown.
    """
    now = _now()
    with atomic(conn):
        cur = conn.execute(
            """INSERT INTO guru_content_drafts
               (card_id, friction_type, draft_type, title, content, content_html,
                source_tickets, status, created_at, source_ref)
               VALUES ('', '', ?, ?, ?, ?, '', ?, ?, ?)""",
            (draft_type, title, content, content_html, status, now, source_ref),
        )
    return cur.lastrowid


def get_draft(conn: sqlite3.Connection, draft_id: int) -> dict | None:
    row = conn.execute(
        "SELECT * FROM guru_content_drafts WHERE id = ?", (draft_id,)
    ).fetchone()
    return dict(row) if row else None


def mark_push_requested(
    conn: sqlite3.Connection, draft_id: int,
    collection_id: str | None = None, folder_id: str | None = None,
) -> None:
    """Record that a push was attempted but is awaiting human sign-off (M5).

    Stores the intended publish target so an in-UI approval can complete the
    push. A non-NULL ``pending_push_json`` is the "awaiting approval" flag.
    """
    target = json.dumps({"collection_id": collection_id, "folder_id": folder_id})
    with atomic(conn):
        conn.execute(
            "UPDATE guru_content_drafts SET pending_push_json = ? WHERE id = ?",
            (target, int(draft_id)),
        )


def approve_draft(conn: sqlite3.Connection, draft_id: int, *, approved_by: str = "user") -> dict | None:
    """Record human sign-off on a draft, clearing the approval gate (M5)."""
    with atomic(conn):
        conn.execute(
            "UPDATE guru_content_drafts SET approved_at = ?, approved_by = ? WHERE id = ?",
            (_now(), approved_by, int(draft_id)),
        )
    return get_draft(conn, int(draft_id))


def clear_push_request(conn: sqlite3.Connection, draft_id: int) -> bool:
    """Drop a pending push (used after a completed publish, or to reject one)."""
    with atomic(conn):
        cur = conn.execute(
            "UPDATE guru_content_drafts SET pending_push_json = NULL WHERE id = ?",
            (int(draft_id),),
        )
    return cur.rowcount > 0


def list_pending_approvals(conn: sqlite3.Connection) -> list[dict]:
    """Drafts a tool tried to push that await human sign-off (M5)."""
    rows = conn.execute(
        """SELECT * FROM guru_content_drafts
           WHERE require_approval = 1 AND approved_at IS NULL
                 AND pending_push_json IS NOT NULL
           ORDER BY created_at DESC""",
    ).fetchall()
    return [dict(r) for r in rows]


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
    content_html: str | None = None,
) -> dict:
    """Replace a draft's body (and optionally title) in one atomic UPDATE.

    Lets the chat ``revise_draft`` tool rewrite a draft directly on the dispatch
    path — one BEGIN/COMMIT, no nested transaction.

    ``content_html`` is ALWAYS rewritten to keep it consistent with the new
    markdown: the rich editor passes its cleaned HTML (preserving color /
    highlight); every markdown-only writer (source editor, ``revise_draft``,
    LLM generation) passes None, which clears any stale rich HTML so
    ``publish_draft`` re-derives a fresh HTML body from the new markdown.
    """
    with atomic(conn):
        if title is not None:
            conn.execute(
                "UPDATE guru_content_drafts SET title=?, content=?, content_html=? WHERE id=?",
                (title, content, content_html, draft_id),
            )
        else:
            conn.execute(
                "UPDATE guru_content_drafts SET content=?, content_html=? WHERE id=?",
                (content, content_html, draft_id),
            )
    return {"id": draft_id, "title": title, "content": content}


def set_draft_card_id(conn: sqlite3.Connection, draft_id: int, card_id: str) -> None:
    """Point a draft at an existing Guru card so publish_draft updates it."""
    with atomic(conn):
        conn.execute(
            "UPDATE guru_content_drafts SET card_id=? WHERE id=?", (card_id, draft_id)
        )


# ── imports (Workbench: bring outside content in) ────────────────────

_GURU_CARD_URL = re.compile(r"app\.getguru\.com/card/([A-Za-z0-9_-]+)")
_DRIVE_FILE_URL = re.compile(r"(?:/d/|[?&]id=)([A-Za-z0-9_-]{20,})")


def parse_guru_card_ref(ref: str) -> str:
    """Accept a raw Guru card id or an app.getguru.com card URL."""
    ref = (ref or "").strip()
    m = _GURU_CARD_URL.search(ref)
    if m:
        return m.group(1)
    return ref.rstrip("/").split("/")[-1] if "/" in ref else ref


def parse_drive_file_ref(ref: str) -> str:
    """Accept a raw Drive file id or a docs.google.com / drive URL."""
    ref = (ref or "").strip()
    m = _DRIVE_FILE_URL.search(ref)
    return m.group(1) if m else ref


def import_guru_card_to_draft(conn: sqlite3.Connection, guru_client, card_ref: str) -> dict:
    """Import an existing Guru card as an editable draft.

    The card's HTML is converted to markdown for editing and the draft is
    linked via set_draft_card_id, so publish_draft takes the UPDATE branch
    (never silently creating a duplicate card). A lossy conversion is
    recoverable: publish stays human-gated and source_ref records the
    origin card.
    """
    from src.data.html_markdown import html_to_markdown

    card_id = parse_guru_card_ref(card_ref)
    if not card_id:
        return {"ok": False, "error": "card_ref_required"}
    try:
        card = guru_client.get_card(card_id)
    except Exception as exc:  # noqa: BLE001 — surface as a failed import
        return {"ok": False, "error": f"guru_fetch_failed: {exc}"}
    if not card or not card.get("id"):
        return {"ok": False, "error": f"card_not_found: {card_id}"}

    source_html = card.get("content", "")
    markdown = html_to_markdown(source_html)
    draft_id = save_card_draft(
        conn,
        title=card.get("title") or "Untitled card",
        content=markdown,
        # Keep the ORIGINAL Guru HTML so a round-trip publish preserves the
        # source fidelity instead of re-deriving from down-converted markdown.
        # (An editor edit replaces it: rich → its cleaned HTML, markdown → None.)
        content_html=source_html or None,
        source_ref=f"guru:{card['id']}",
        draft_type="card_update",
    )
    set_draft_card_id(conn, draft_id, card["id"])
    return {"ok": True, "draft_id": draft_id, "card_id": card["id"],
            "title": card.get("title", ""), "content": markdown}


def import_drive_doc(conn: sqlite3.Connection, drive_reader, drive_ref: str) -> dict:
    """Import a Google Doc / Drive file by URL or file id into the store."""
    file_id = parse_drive_file_ref(drive_ref)
    if not file_id:
        return {"ok": False, "error": "drive_ref_required"}
    try:
        meta = drive_reader.get_file(file_id)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"drive_fetch_failed: {exc}"}
    if not meta or not meta.get("id"):
        return {"ok": False, "error": f"file_not_found: {file_id}"}
    try:
        text = drive_reader.export_text(meta["id"], meta.get("mime_type", ""))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"drive_export_failed: {exc}"}
    doc_id = save_document(
        conn,
        source="drive",
        name=meta.get("name") or "Untitled",
        source_ref=meta["id"],
        mime_type=meta.get("mime_type", ""),
        web_url=meta.get("url", ""),
        modified_time=meta.get("modified_time"),
        full_text=text or "",
    )
    return {"ok": True, "doc_id": doc_id, "name": meta.get("name", ""),
            "chars": len(text or "")}


# ── style guide (first-class input to card generation/revision) ──────

_STYLE_GUIDE_KEY = "style_guide_doc_id"
# Searchable identifier prepended to every style-guide document name so Renn can
# isolate style guides via search_local_documents / list_style_guides.
_STYLE_GUIDE_TAG = "[STYLE-GUIDE]"
# The card/article template is the SAME tagged-document pattern under its own
# settings pointer: the structural skeleton (exact headings) every generated
# card follows, alongside the style guide's tone/formatting rules.
_CARD_TEMPLATE_KEY = "card_template_doc_id"
_CARD_TEMPLATE_TAG = "[CARD-TEMPLATE]"


# ── tagged guide documents (style guide / card template) ─────────────
# One mechanism, two instances: a "guide" is an enablement document whose
# name carries a searchable tag, with the active doc_id stored in the
# enablement settings section under `key`.

def _get_guide(conn: sqlite3.Connection, key: str) -> str:
    try:
        from src.data.settings_manager import get_section
        doc_id = (get_section("enablement", {}) or {}).get(key, "")
        if not doc_id:
            return ""
        doc = get_document(conn, str(doc_id))
        return (doc or {}).get("full_text", "") or ""
    except Exception:
        return ""


def _set_guide(conn: sqlite3.Connection, text: str, *, key: str, tag: str,
               default_doc_id: str, name: str, doc_id: str | None) -> str:
    from src.data.settings_manager import get_section, set_section
    # Guides are prompt inputs, not rendered cards: inline color spans that
    # doc_reader preserves from Word headings are noise here (and Qt's
    # markdown preview would show them literally) — strip them.
    text = re.sub(r"</?span[^>]*>", "", text or "")
    if tag not in name:
        name = f"{tag} {name}"
    cfg = dict(get_section("enablement", {}) or {})
    did = str(doc_id or cfg.get(key) or default_doc_id)
    did = save_document(
        conn, source="manual", name=name, doc_id=did, full_text=text or ""
    )
    cfg[key] = did   # active pointer
    set_section("enablement", cfg)
    return did


def _list_guides(conn: sqlite3.Connection, key: str, tag: str) -> list[dict]:
    from src.data.settings_manager import get_section
    active = (get_section("enablement", {}) or {}).get(key, "")
    rows = conn.execute(
        "SELECT doc_id, name, LENGTH(full_text) AS chars, "
        "       COALESCE(modified_time, indexed_at) AS ts "
        "FROM enablement_documents WHERE name LIKE ? "
        "ORDER BY ts DESC",
        (tag + "%",),
    ).fetchall()
    return [{"doc_id": r["doc_id"], "name": r["name"], "chars": r["chars"],
             "active": r["doc_id"] == active} for r in rows]


def _set_active_guide(conn: sqlite3.Connection, doc_id: str, key: str) -> bool:
    if not get_document(conn, str(doc_id)):
        return False
    from src.data.settings_manager import get_section, set_section
    cfg = dict(get_section("enablement", {}) or {})
    cfg[key] = str(doc_id)
    set_section("enablement", cfg)
    return True


def _delete_guide(conn: sqlite3.Connection, doc_id: str, key: str, tag: str) -> bool:
    from src.data.settings_manager import get_section, set_section
    with atomic(conn):
        cur = conn.execute(
            "DELETE FROM enablement_documents WHERE doc_id = ?", (str(doc_id),))
    cfg = dict(get_section("enablement", {}) or {})
    if cfg.get(key) == str(doc_id):
        cfg.pop(key, None)
        remaining = _list_guides(conn, key, tag)
        if remaining:
            cfg[key] = remaining[0]["doc_id"]
        set_section("enablement", cfg)
    return cur.rowcount > 0


def _clear_guide(key: str) -> None:
    from src.data.settings_manager import get_section, set_section
    cfg = dict(get_section("enablement", {}) or {})
    if cfg.pop(key, None) is not None:
        set_section("enablement", cfg)


# ── style guide (public API — tone/formatting rules) ─────────────────

def get_style_guide(conn: sqlite3.Connection) -> str:
    """The operator's card style guide text ('' when unset)."""
    return _get_guide(conn, _STYLE_GUIDE_KEY)


def set_style_guide(conn: sqlite3.Connection, text: str, *,
                    name: str = "Card style guide",
                    doc_id: str | None = None) -> str:
    """Store/replace a style guide as an enablement document + set it active.

    The stored name is prefixed with the ``[STYLE-GUIDE]`` identifier so Renn can
    find style guides by searching the local DB. Pass an explicit ``doc_id`` (e.g.
    a slug of an uploaded filename) to keep distinct guides as separate, searchable
    documents; omit it to update the single default guide (paste/demo flows).
    """
    return _set_guide(conn, text, key=_STYLE_GUIDE_KEY, tag=_STYLE_GUIDE_TAG,
                      default_doc_id="style-guide",
                      name=name or "Card style guide", doc_id=doc_id)


def list_style_guides(conn: sqlite3.Connection) -> list[dict]:
    """Every stored style guide (tagged docs), newest first, active one flagged."""
    return _list_guides(conn, _STYLE_GUIDE_KEY, _STYLE_GUIDE_TAG)


def set_active_style_guide(conn: sqlite3.Connection, doc_id: str) -> bool:
    """Make an existing style-guide document the active one (used for injection)."""
    return _set_active_guide(conn, doc_id, _STYLE_GUIDE_KEY)


def delete_style_guide(conn: sqlite3.Connection, doc_id: str) -> bool:
    """Delete a stored style guide; if it was active, promote the newest remaining."""
    return _delete_guide(conn, doc_id, _STYLE_GUIDE_KEY, _STYLE_GUIDE_TAG)


def clear_style_guide() -> None:
    _clear_guide(_STYLE_GUIDE_KEY)


def update_style_guide_text(conn: sqlite3.Connection, text: str) -> str:
    """Rewrite the ACTIVE style guide's text in place (its name is kept, so an
    inline edit doesn't rename an uploaded guide to the default)."""
    from src.data.settings_manager import get_section
    did = (get_section("enablement", {}) or {}).get(_STYLE_GUIDE_KEY)
    doc = get_document(conn, str(did)) if did else None
    return set_style_guide(conn, text, name=(doc or {}).get("name")
                           or "Card style guide")


def style_guide_block(conn: sqlite3.Connection) -> str:
    """The prompt block injected into card-gen/revise ('' when unset)."""
    text = get_style_guide(conn)
    if not text.strip():
        return ""
    return (
        "\nSTYLE GUIDE — follow it strictly for tone, structure and formatting:\n"
        "--- STYLE GUIDE START ---\n"
        f"{text.strip()}\n"
        "--- STYLE GUIDE END ---\n"
    )


# ── card/article template (public API — structural skeleton) ─────────

def get_card_template(conn: sqlite3.Connection) -> str:
    """The active card/article template text ('' when unset)."""
    return _get_guide(conn, _CARD_TEMPLATE_KEY)


def set_card_template(conn: sqlite3.Connection, text: str, *,
                      name: str = "Card/article template",
                      doc_id: str | None = None) -> str:
    """Store/replace the card/article template + set it active (same
    tagged-document pattern as the style guide)."""
    return _set_guide(conn, text, key=_CARD_TEMPLATE_KEY, tag=_CARD_TEMPLATE_TAG,
                      default_doc_id="card-template",
                      name=name or "Card/article template", doc_id=doc_id)


def list_card_templates(conn: sqlite3.Connection) -> list[dict]:
    """Every stored template, newest first, active one flagged."""
    return _list_guides(conn, _CARD_TEMPLATE_KEY, _CARD_TEMPLATE_TAG)


def set_active_card_template(conn: sqlite3.Connection, doc_id: str) -> bool:
    return _set_active_guide(conn, doc_id, _CARD_TEMPLATE_KEY)


def delete_card_template(conn: sqlite3.Connection, doc_id: str) -> bool:
    return _delete_guide(conn, doc_id, _CARD_TEMPLATE_KEY, _CARD_TEMPLATE_TAG)


def clear_card_template() -> None:
    _clear_guide(_CARD_TEMPLATE_KEY)


def update_card_template_text(conn: sqlite3.Connection, text: str) -> str:
    """Rewrite the ACTIVE card template's text in place (name kept)."""
    from src.data.settings_manager import get_section
    did = (get_section("enablement", {}) or {}).get(_CARD_TEMPLATE_KEY)
    doc = get_document(conn, str(did)) if did else None
    return set_card_template(conn, text, name=(doc or {}).get("name")
                             or "Card/article template")


def card_template_block(conn: sqlite3.Connection) -> str:
    """The prompt block injected into card-gen/revise ('' when unset).

    The template is a heading skeleton: generation must reproduce its exact
    heading structure and order, filling the bracketed slots and dropping the
    template's own instructional notes/examples from the finished card."""
    text = get_card_template(conn)
    if not text.strip():
        return ""
    return (
        "\nCARD/ARTICLE TEMPLATE — structure the card with EXACTLY this heading "
        "layout: same headings, same order. Fill each bracketed [slot] with real "
        "content; the template's instructional notes and examples are guidance "
        "for you, not text to copy into the card:\n"
        "--- TEMPLATE START ---\n"
        f"{text.strip()}\n"
        "--- TEMPLATE END ---\n"
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
    llm_client=None,
    *,
    collection: str = "Enablement",
) -> dict:
    """Generate a Guru card draft from a stored document, and link it.

    DEFAULT (``llm_client=None``) is a deterministic Python conversion
    (``doc_to_card.card_from_document``) — no provider, no network, no
    nondeterminism. Pass an ``llm_client`` (with ``.generate(prompt)``) only
    when the caller wants AI summarisation/rewriting ("polish with AI"). The
    draft is written to guru_content_drafts (status='pending').
    """
    doc = get_document(conn, doc_id)
    if not doc:
        raise ValueError(f"Document {doc_id} not found")

    if llm_client is None:
        from src.data.doc_to_card import card_from_document
        title, content = card_from_document(
            doc.get("name", "Untitled"), doc.get("full_text", ""))
    else:
        template = _CARD_PROMPT.read_text(encoding="utf-8") if _CARD_PROMPT.exists() else (
            "Turn this document into a Guru card.\nSOURCE: {doc_name}\n{doc_text}\n"
            "{style_guide}"
            "Return:\nTITLE: <title>\n---\n<body>"
        )
        # The template block rides the same {style_guide} slot so older prompt
        # files (without a dedicated placeholder) still receive it.
        prompt = template.format(
            doc_name=doc.get("name", "Untitled"),
            doc_text=doc.get("full_text", ""),
            collection=collection,
            style_guide=style_guide_block(conn) + card_template_block(conn),
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
    folder_id: str | None = None,
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
        # Guru's `content` field is HTML. Send the draft's rich HTML when the
        # rich editor captured it (preserves color/highlight); otherwise derive
        # it from the canonical markdown with the SAME converter the in-app
        # preview uses, so the published card matches the preview. (Previously
        # this sent raw markdown into Guru's HTML field — formatting was lost.)
        from src.data.guru_blocks import expand_blocks
        from src.data.html_markdown import markdown_to_html
        html_body = draft.get("content_html") or markdown_to_html(draft["content"])
        # Expand native-block directives (callout / collapsible / card-link)
        # into Guru's markup — works on both the captured rich HTML and the
        # markdown-derived HTML. Idempotent when there are no directives.
        html_body = expand_blocks(html_body)
        try:
            if card_id:
                guru_result = guru_client.update_card(card_id, html_body, draft["title"])
            elif folder_id:
                guru_result = guru_client.create_card(
                    collection_id or "", draft["title"], html_body,
                    folder_ids=[folder_id])
            else:
                # Unchanged 3-arg call — keeps backward-compat with callers/mocks
                # whose create_card predates the folder_ids parameter.
                guru_result = guru_client.create_card(
                    collection_id or "", draft["title"], html_body)
            # Capture the new card's id on EITHER create path (folder or root) so a
            # later edit UPDATES the same card instead of creating a duplicate.
            if not card_id and isinstance(guru_result, dict):
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
    # Finalize the audit trail for this update (anchors effectiveness). Never fatal.
    try:
        from src.data.content_update import provenance
        provenance.finalize_publish(conn, draft_id, approved_by=approved_by, card_id=card_id)
    except Exception:  # noqa: BLE001
        pass
    # Effectiveness baseline (Guru-native, FULLY DECOUPLED): snapshot the card's
    # per-card view + open-comment counts at publish so a post-window proxy delta
    # can later be measured (effectiveness_proxy.measure_proxy) with no ticket /
    # warehouse read. Replaces the moot ticket-volume record_baseline weld. Fires
    # only when we have both a live guru_client and a card_id; behaviour-preserving
    # when guru_client is None. Always non-fatal: a Guru hiccup must never fail a
    # successful publish.
    if guru_client is not None and card_id:
        try:
            from src.data.content_update import effectiveness_proxy
            from src.data.enablement_health import GuruSignals
            effectiveness_proxy.snapshot_baseline(
                conn, GuruSignals(guru_client), card_id, draft_id)
        except Exception:  # noqa: BLE001
            pass
    return {"ok": True, "draft_id": draft_id, "status": "pushed",
            "card_id": card_id, "guru_result": guru_result}
