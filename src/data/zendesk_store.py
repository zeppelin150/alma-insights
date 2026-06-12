"""Zendesk Help Center articles + macros store.

Sync the live articles/macros into a local cache, stage AI-drafted
new/updated content, and human-gated-push to Zendesk — mirroring the Guru
card flow. A draft linked to a live id (article_id/macro_id) UPDATES on
push; otherwise it CREATES. Demo mode marks pushed without an API call.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from src.data.connection_factory import atomic

_PROMPTS = Path(__file__).resolve().parent.parent.parent / "config" / "prompts"
_ARTICLE_PROMPT = _PROMPTS / "enablement_article_from_doc.txt"
_MACRO_PROMPT = _PROMPTS / "enablement_macro_from_doc.txt"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── sync (read → cache) ──────────────────────────────────────────────

def sync_articles(conn: sqlite3.Connection, zendesk_client, *, locale="en-us") -> dict:
    try:
        articles = zendesk_client.get_articles(locale=locale)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
    now = _now()
    with atomic(conn):
        for a in articles:
            if not a.get("id"):
                continue
            conn.execute(
                "INSERT OR REPLACE INTO zendesk_articles (article_id, title, body, "
                "locale, section_id, html_url, updated_at, fetched_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (a["id"], a.get("title", ""), a.get("body", ""),
                 a.get("locale", locale), a.get("section_id"),
                 a.get("html_url", ""), a.get("updated_at", ""), now),
            )
    return {"ok": True, "count": len(articles)}


def sync_macros(conn: sqlite3.Connection, zendesk_client) -> dict:
    try:
        macros = zendesk_client.list_macros()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
    now = _now()
    with atomic(conn):
        for m in macros:
            if not m.get("id"):
                continue
            conn.execute(
                "INSERT OR REPLACE INTO zendesk_macros (macro_id, name, description, "
                "actions_json, active, updated_at, fetched_at) VALUES (?,?,?,?,?,?,?)",
                (m["id"], m.get("title", ""), m.get("description", ""),
                 json.dumps(m.get("actions", [])), 1 if m.get("active", True) else 0,
                 m.get("updated_at", ""), now),
            )
    return {"ok": True, "count": len(macros)}


def list_articles(conn, *, limit=200) -> list[dict]:
    rows = conn.execute(
        "SELECT article_id, title, section_id, html_url, updated_at "
        "FROM zendesk_articles ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def list_macros(conn, *, limit=200) -> list[dict]:
    rows = conn.execute(
        "SELECT macro_id, name, description, updated_at FROM zendesk_macros "
        "ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


# ── article drafts ───────────────────────────────────────────────────

def save_article_draft(conn, *, title, body, article_id=None, section_id=None,
                       source_ref=None) -> int:
    with atomic(conn):
        cur = conn.execute(
            "INSERT INTO zendesk_article_drafts (article_id, section_id, title, "
            "body, source_ref, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
            (article_id, section_id, title, body, source_ref, _now(), _now()),
        )
        return int(cur.lastrowid)


def get_article_draft(conn, draft_id) -> dict | None:
    row = conn.execute(
        "SELECT * FROM zendesk_article_drafts WHERE id=?", (draft_id,)).fetchone()
    return dict(row) if row else None


def list_article_drafts(conn, *, status="pending", limit=100) -> list[dict]:
    rows = conn.execute(
        "SELECT id, article_id, title, status, source_ref, updated_at "
        "FROM zendesk_article_drafts WHERE status=? ORDER BY updated_at DESC LIMIT ?",
        (status, limit)).fetchall()
    return [dict(r) for r in rows]


def update_article_draft(conn, draft_id, *, title=None, body=None) -> dict:
    d = get_article_draft(conn, draft_id)
    if not d:
        return {"ok": False, "error": "draft_not_found"}
    with atomic(conn):
        conn.execute(
            "UPDATE zendesk_article_drafts SET title=?, body=?, updated_at=? WHERE id=?",
            (title if title is not None else d["title"],
             body if body is not None else d["body"], _now(), draft_id))
    return {"ok": True, "draft_id": draft_id}


def link_article_draft(conn, draft_id, article_id) -> None:
    with atomic(conn):
        conn.execute("UPDATE zendesk_article_drafts SET article_id=? WHERE id=?",
                     (article_id, draft_id))


def draft_article_from_document(conn, doc_id, llm_client) -> dict:
    from src.data.enablement_store import _parse_card, get_document
    doc = get_document(conn, doc_id)
    if not doc:
        return {"ok": False, "error": "document_not_found"}
    template = _ARTICLE_PROMPT.read_text(encoding="utf-8") if _ARTICLE_PROMPT.exists() else (
        "Turn this into a support-center article.\nSOURCE: {doc_name}\n{doc_text}\n"
        "Return:\nTITLE: <title>\n---\n<body in markdown>")
    prompt = template.format(doc_name=doc.get("name", "Untitled"),
                             doc_text=doc.get("full_text", ""))
    title, body = _parse_card(llm_client.generate(prompt), doc.get("name", "Article"))
    draft_id = save_article_draft(conn, title=title, body=body, source_ref=doc_id)
    return {"ok": True, "draft_id": draft_id, "title": title}


def publish_article_draft(conn, draft_id, *, zendesk_client=None,
                          section_id=None, approved_by="user") -> dict:
    """Push an article draft to Zendesk (UPDATE if linked, else CREATE).
    Demo/no-client: marks pushed locally. Body markdown → HTML on push."""
    d = get_article_draft(conn, draft_id)
    if not d:
        return {"ok": False, "error": "draft_not_found"}
    if d["status"] == "pushed":
        return {"ok": True, "draft_id": draft_id, "already": True}

    article_id = d.get("article_id")
    result = None
    if zendesk_client is not None:
        from src.data.html_markdown import markdown_to_html
        html = markdown_to_html(d["body"])
        try:
            if article_id:
                result = zendesk_client.update_article(
                    article_id, title=d["title"], body=html, locale=d.get("locale", "en-us"))
            else:
                sect = section_id or d.get("section_id")
                if not sect:
                    return {"ok": False, "error": "section_id_required_to_create"}
                result = zendesk_client.create_article(
                    sect, d["title"], html, locale=d.get("locale", "en-us"))
                article_id = result.get("id", article_id)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"zendesk_push_failed: {exc}"}

    with atomic(conn):
        conn.execute(
            "UPDATE zendesk_article_drafts SET status='pushed', article_id=?, "
            "pushed_at=?, updated_at=? WHERE id=?",
            (article_id, _now(), _now(), draft_id))
    return {"ok": True, "draft_id": draft_id, "article_id": article_id, "result": result}


# ── macro drafts ─────────────────────────────────────────────────────

def save_macro_draft(conn, *, name, actions, description=None, macro_id=None,
                     source_ref=None) -> int:
    with atomic(conn):
        cur = conn.execute(
            "INSERT INTO zendesk_macro_drafts (macro_id, name, description, "
            "actions_json, source_ref, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
            (macro_id, name, description, json.dumps(actions or []), source_ref,
             _now(), _now()))
        return int(cur.lastrowid)


def get_macro_draft(conn, draft_id) -> dict | None:
    row = conn.execute(
        "SELECT * FROM zendesk_macro_drafts WHERE id=?", (draft_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    try:
        d["actions"] = json.loads(d.get("actions_json") or "[]")
    except (ValueError, TypeError):
        d["actions"] = []
    return d


def update_macro_draft(conn, draft_id, *, name=None, actions=None) -> dict:
    d = get_macro_draft(conn, draft_id)
    if not d:
        return {"ok": False, "error": "draft_not_found"}
    with atomic(conn):
        conn.execute(
            "UPDATE zendesk_macro_drafts SET name=?, actions_json=?, updated_at=? WHERE id=?",
            (name if name is not None else d["name"],
             json.dumps(actions if actions is not None else d["actions"]),
             _now(), draft_id))
    return {"ok": True, "draft_id": draft_id}


def list_macro_drafts(conn, *, status="pending", limit=100) -> list[dict]:
    rows = conn.execute(
        "SELECT id, macro_id, name, status, updated_at FROM zendesk_macro_drafts "
        "WHERE status=? ORDER BY updated_at DESC LIMIT ?", (status, limit)).fetchall()
    return [dict(r) for r in rows]


def draft_macro_from_document(conn, doc_id, llm_client) -> dict:
    from src.data.enablement_store import get_document
    doc = get_document(conn, doc_id)
    if not doc:
        return {"ok": False, "error": "document_not_found"}
    template = _MACRO_PROMPT.read_text(encoding="utf-8") if _MACRO_PROMPT.exists() else (
        "Create a Zendesk macro from this.\n{doc_text}\nReturn JSON: "
        '{{"name": str, "actions": [{{"field": str, "value": str}}]}}')
    prompt = template.format(doc_name=doc.get("name", "Untitled"),
                             doc_text=doc.get("full_text", ""))
    parsed = _parse_macro(llm_client.generate(prompt))
    draft_id = save_macro_draft(conn, name=parsed["name"], actions=parsed["actions"],
                                source_ref=doc_id)
    return {"ok": True, "draft_id": draft_id, "name": parsed["name"]}


def publish_macro_draft(conn, draft_id, *, zendesk_client=None, approved_by="user") -> dict:
    d = get_macro_draft(conn, draft_id)
    if not d:
        return {"ok": False, "error": "draft_not_found"}
    if d["status"] == "pushed":
        return {"ok": True, "draft_id": draft_id, "already": True}
    macro_id = d.get("macro_id")
    result = None
    if zendesk_client is not None:
        try:
            if macro_id:
                result = zendesk_client.update_macro(
                    macro_id, name=d["name"], actions=d["actions"])
            else:
                result = zendesk_client.create_macro(
                    d["name"], d["actions"], description=d.get("description"))
                macro_id = result.get("id", macro_id)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"zendesk_push_failed: {exc}"}
    with atomic(conn):
        conn.execute(
            "UPDATE zendesk_macro_drafts SET status='pushed', macro_id=?, "
            "pushed_at=?, updated_at=? WHERE id=?",
            (macro_id, _now(), _now(), draft_id))
    return {"ok": True, "draft_id": draft_id, "macro_id": macro_id, "result": result}


def _parse_macro(text: str) -> dict:
    import re
    raw = (text or "").strip()
    obj = None
    try:
        obj = json.loads(raw)
    except (ValueError, TypeError):
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            try:
                obj = json.loads(m.group(0))
            except (ValueError, TypeError):
                obj = None
    if not isinstance(obj, dict):
        obj = {}
    name = str(obj.get("name") or "Untitled macro")
    actions = []
    for a in obj.get("actions") or []:
        if isinstance(a, dict) and a.get("field"):
            actions.append({"field": str(a["field"]), "value": str(a.get("value", ""))})
    if not actions:
        actions = [{"field": "comment_value", "value": name}]
    return {"name": name, "actions": actions}
