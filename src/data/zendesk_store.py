"""Zendesk Help Center articles + macros store.

Two layers share this module:

* The mig-030 draft/push flow used by the native Zendesk tab — sync the
  live articles/macros into a local cache, stage AI-drafted new/updated
  content, and human-gated-push to Zendesk. A draft linked to a live id
  (article_id/macro_id) UPDATES on push; otherwise it CREATES. Demo mode
  marks pushed without an API call.
* The mig-051 full-fidelity mirror (web Garden-clone tab + AI revisions):
  ON CONFLICT upserts with content_hash dedup and body_text/actions_text
  plain-text projections, contentless-FTS search, a unified revisions
  view, and the pending→ready→copied draft lifecycle.

Write-form rule (load-bearing): the mirror FTS tables are contentless and
kept in sync by AI/AD/AU triggers, and ``INSERT OR REPLACE`` fires only the
AI trigger (the implicit delete needs recursive_triggers, which the
connection factory does not enable) — a REPLACE would append a second FTS
posting and leave stale terms searchable forever. Every writer to
zendesk_articles / zendesk_macros / zendesk_sections / zendesk_categories
MUST use ``INSERT ... ON CONFLICT(pk) DO UPDATE`` (locked by a grep guard
in tests/test_zendesk_mirror_schema.py).

Transaction rule: mutators go through ``_txn(conn)`` — ``atomic(conn)``
when no transaction is open, a pass-through otherwise — so they are safe
on the MCP-subprocess dispatch path where the dispatcher may already hold
an open implicit transaction (atomic() cannot nest).
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

from src.data.connection_factory import atomic

_PROMPTS = Path(__file__).resolve().parent.parent.parent / "config" / "prompts"
_ARTICLE_PROMPT = _PROMPTS / "enablement_article_from_doc.txt"
_MACRO_PROMPT = _PROMPTS / "enablement_macro_from_doc.txt"

_FTS_TOKEN_RE = re.compile(r"[a-z0-9]+")
_TAG_RE = re.compile(r"<[^>]+>")

_DRAFT_TABLES = {"article": "zendesk_article_drafts",
                 "macro": "zendesk_macro_drafts"}

# pending→ready, ready→pending, pending→copied, ready→copied — nothing
# else. 'copied' and legacy 'pushed' rows are immutable here (delete only).
_ALLOWED_TRANSITIONS = {("pending", "ready"), ("ready", "pending"),
                        ("pending", "copied"), ("ready", "copied")}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def _txn(conn: sqlite3.Connection):
    """atomic() when no transaction is open; pass-through when the caller
    (e.g. the MCP tool dispatcher) already holds one — atomic() cannot nest,
    and on the pass-through path the commit belongs to the caller."""
    if conn.in_transaction:
        yield conn
    else:
        with atomic(conn):
            yield conn


# ── projections + hashing ────────────────────────────────────────────

_BLOCK_TAGS = {"p", "div", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5",
               "h6", "tr", "table", "section", "article", "blockquote",
               "pre", "hr"}


class _TextExtractor(HTMLParser):
    """Tag-stripping text extractor: skips script/style, newlines blocks."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        elif tag == "br":
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    """Plain-text projection of an HTML string (body_text / diff input)."""
    if not html:
        return ""
    parser = _TextExtractor()
    try:
        parser.feed(str(html))
        parser.close()
        raw = "".join(parser.parts)
    except Exception:  # noqa: BLE001 — a hostile fragment must not break sync
        raw = _TAG_RE.sub(" ", str(html))
    lines = [" ".join(line.split()) for line in raw.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def _actions_to_text(actions) -> str:
    """Plain-text projection of a macro's actions list (actions_text)."""
    parts = []
    for a in actions or []:
        if isinstance(a, dict):
            piece = f"{a.get('field', '')} {a.get('value', '')}".strip()
            if piece:
                parts.append(piece)
    return "\n".join(parts)


def _canonical_hash(payload: dict) -> str:
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _article_hash(*, title, body_html, section_id, draft, outdated,
                  labels_json, author_name, position) -> str:
    # FULL projected row so metadata-only Guide changes never count "unchanged"
    return _canonical_hash({
        "title": title, "body_html": body_html, "section_id": section_id,
        "draft": draft, "outdated": outdated, "labels_json": labels_json,
        "author_name": author_name, "position": position,
    })


def _macro_hash(*, name, description, active, actions_json) -> str:
    return _canonical_hash({"name": name, "description": description,
                            "active": active, "actions_json": actions_json})


def _norm_json(value, default="[]") -> str:
    if value is None:
        return default
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value)
    except (TypeError, ValueError):
        return default


# ── mirror upserts (mig 051) ─────────────────────────────────────────

def upsert_articles(conn, articles: list[dict], *, origin="pull",
                    source_file=None) -> dict:
    """Hash-deduped mirror upsert. Import payloads may never replace a
    differing pull-origin baseline (the mirror is authoritative) — those
    rows are refused and counted under 'conflicts'."""
    now = _now()
    report = {"inserted": 0, "updated": 0, "unchanged": 0, "conflicts": 0,
              "conflict_ids": []}
    with _txn(conn):
        for a in articles or []:
            if not isinstance(a, dict) or a.get("id") in (None, ""):
                continue
            try:
                aid = int(a["id"])
            except (TypeError, ValueError):
                continue
            title = a.get("title", "") or ""
            body_html = a.get("body_html")
            if body_html is None:
                body_html = a.get("body", "") or ""
            labels = a.get("label_names")
            if labels is None:
                labels = a.get("labels")
            labels_json = json.dumps(labels or [])
            draft = 1 if a.get("draft") else 0
            outdated = 1 if a.get("outdated") else 0
            author_name = a.get("author_name")
            position = a.get("position")
            content_hash = _article_hash(
                title=title, body_html=body_html, section_id=a.get("section_id"),
                draft=draft, outdated=outdated, labels_json=labels_json,
                author_name=author_name, position=position)

            existing = conn.execute(
                "SELECT content_hash, origin FROM zendesk_articles "
                "WHERE article_id=?", (aid,)).fetchone()
            if existing is not None:
                if existing[0] == content_hash:
                    report["unchanged"] += 1
                    if origin == "pull":
                        conn.execute(
                            "UPDATE zendesk_articles SET fetched_at=? "
                            "WHERE article_id=?", (now, aid))
                    continue
                if origin == "import" and (existing[1] or "pull") == "pull":
                    report["conflicts"] += 1
                    report["conflict_ids"].append(aid)
                    continue

            conn.execute(
                "INSERT INTO zendesk_articles (article_id, title, body, "
                "body_html, body_text, locale, section_id, html_url, "
                "updated_at, fetched_at, draft, outdated, labels_json, "
                "author_name, position, created_at_remote, content_hash, "
                "origin, source_file, raw_json) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(article_id) DO UPDATE SET "
                "title=excluded.title, body=excluded.body, "
                "body_html=excluded.body_html, body_text=excluded.body_text, "
                "locale=excluded.locale, section_id=excluded.section_id, "
                "html_url=excluded.html_url, updated_at=excluded.updated_at, "
                "fetched_at=excluded.fetched_at, draft=excluded.draft, "
                "outdated=excluded.outdated, labels_json=excluded.labels_json, "
                "author_name=excluded.author_name, position=excluded.position, "
                "created_at_remote=excluded.created_at_remote, "
                "content_hash=excluded.content_hash, origin=excluded.origin, "
                "source_file=excluded.source_file, raw_json=excluded.raw_json",
                (aid, title, body_html, body_html, html_to_text(body_html),
                 a.get("locale", "en-us"), a.get("section_id"),
                 a.get("html_url", ""), a.get("updated_at", ""), now,
                 draft, outdated, labels_json, author_name, position,
                 a.get("created_at"), content_hash, origin, source_file,
                 json.dumps(a)))
            report["inserted" if existing is None else "updated"] += 1
    return report


def upsert_macros(conn, macros: list[dict], *, origin="pull",
                  source_file=None) -> dict:
    now = _now()
    report = {"inserted": 0, "updated": 0, "unchanged": 0, "conflicts": 0,
              "conflict_ids": []}
    with _txn(conn):
        for m in macros or []:
            if not isinstance(m, dict) or m.get("id") in (None, ""):
                continue
            try:
                mid = int(m["id"])
            except (TypeError, ValueError):
                continue
            name = m.get("title") or m.get("name") or ""
            description = m.get("description", "") or ""
            actions = m.get("actions", []) or []
            actions_json = json.dumps(actions)
            active = 1 if m.get("active", True) else 0
            content_hash = _macro_hash(name=name, description=description,
                                       active=active, actions_json=actions_json)

            existing = conn.execute(
                "SELECT content_hash, origin FROM zendesk_macros "
                "WHERE macro_id=?", (mid,)).fetchone()
            if existing is not None:
                if existing[0] == content_hash:
                    report["unchanged"] += 1
                    if origin == "pull":
                        conn.execute(
                            "UPDATE zendesk_macros SET fetched_at=? "
                            "WHERE macro_id=?", (now, mid))
                    continue
                if origin == "import" and (existing[1] or "pull") == "pull":
                    report["conflicts"] += 1
                    report["conflict_ids"].append(mid)
                    continue

            conn.execute(
                "INSERT INTO zendesk_macros (macro_id, name, description, "
                "actions_json, actions_text, active, updated_at, fetched_at, "
                "content_hash, origin, source_file, raw_json) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(macro_id) DO UPDATE SET "
                "name=excluded.name, description=excluded.description, "
                "actions_json=excluded.actions_json, "
                "actions_text=excluded.actions_text, active=excluded.active, "
                "updated_at=excluded.updated_at, fetched_at=excluded.fetched_at, "
                "content_hash=excluded.content_hash, origin=excluded.origin, "
                "source_file=excluded.source_file, raw_json=excluded.raw_json",
                (mid, name, description, actions_json,
                 _actions_to_text(actions), active, m.get("updated_at", ""),
                 now, content_hash, origin, source_file, json.dumps(m)))
            report["inserted" if existing is None else "updated"] += 1
    return report


def upsert_sections(conn, sections: list[dict], *, origin="pull") -> dict:
    now = _now()
    report = {"inserted": 0, "updated": 0}
    with _txn(conn):
        for s in sections or []:
            if not isinstance(s, dict) or s.get("id") in (None, ""):
                continue
            try:
                sid = int(s["id"])
            except (TypeError, ValueError):
                continue
            existing = conn.execute(
                "SELECT 1 FROM zendesk_sections WHERE section_id=?",
                (sid,)).fetchone()
            conn.execute(
                "INSERT INTO zendesk_sections (section_id, category_id, name, "
                "description, position, origin, fetched_at) "
                "VALUES (?,?,?,?,?,?,?) "
                "ON CONFLICT(section_id) DO UPDATE SET "
                "category_id=excluded.category_id, name=excluded.name, "
                "description=excluded.description, position=excluded.position, "
                "origin=excluded.origin, fetched_at=excluded.fetched_at",
                (sid, s.get("category_id"), s.get("name", "") or "",
                 s.get("description"), s.get("position"), origin, now))
            report["inserted" if existing is None else "updated"] += 1
    return report


def upsert_categories(conn, categories: list[dict], *, origin="pull") -> dict:
    now = _now()
    report = {"inserted": 0, "updated": 0}
    with _txn(conn):
        for c in categories or []:
            if not isinstance(c, dict) or c.get("id") in (None, ""):
                continue
            try:
                cid = int(c["id"])
            except (TypeError, ValueError):
                continue
            existing = conn.execute(
                "SELECT 1 FROM zendesk_categories WHERE category_id=?",
                (cid,)).fetchone()
            conn.execute(
                "INSERT INTO zendesk_categories (category_id, name, "
                "description, position, origin, fetched_at) "
                "VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(category_id) DO UPDATE SET "
                "name=excluded.name, description=excluded.description, "
                "position=excluded.position, origin=excluded.origin, "
                "fetched_at=excluded.fetched_at",
                (cid, c.get("name", "") or "", c.get("description"),
                 c.get("position"), origin, now))
            report["inserted" if existing is None else "updated"] += 1
    return report


def backfill_mirror_projections(conn) -> dict:
    """Compute body_text/actions_text + content_hash for pre-051 rows (a SQL
    migration cannot run the Python projections), then rebuild both FTS
    mirrors. Called by the 051 post-hook; safe to re-run."""
    fixed_articles = 0
    rows = conn.execute(
        "SELECT article_id, title, body, body_html, section_id, draft, "
        "outdated, labels_json, author_name, position FROM zendesk_articles "
        "WHERE body_text IS NULL").fetchall()
    for r in rows:
        html = r[3] if r[3] is not None else (r[2] or "")
        content_hash = _article_hash(
            title=r[1] or "", body_html=html, section_id=r[4],
            draft=r[5] or 0, outdated=r[6] or 0, labels_json=r[7] or "[]",
            author_name=r[8], position=r[9])
        conn.execute(
            "UPDATE zendesk_articles SET body_text=?, "
            "content_hash=COALESCE(content_hash, ?) WHERE article_id=?",
            (html_to_text(html), content_hash, r[0]))
        fixed_articles += 1

    fixed_macros = 0
    rows = conn.execute(
        "SELECT macro_id, name, description, actions_json, active "
        "FROM zendesk_macros WHERE actions_text IS NULL").fetchall()
    for r in rows:
        try:
            actions = json.loads(r[3] or "[]")
        except (ValueError, TypeError):
            actions = []
        content_hash = _macro_hash(name=r[1] or "", description=r[2] or "",
                                   active=r[4] if r[4] is not None else 1,
                                   actions_json=r[3] or "[]")
        conn.execute(
            "UPDATE zendesk_macros SET actions_text=?, "
            "content_hash=COALESCE(content_hash, ?) WHERE macro_id=?",
            (_actions_to_text(actions), content_hash, r[0]))
        fixed_macros += 1

    # Rebuild the contentless FTS mirrors from the repaired projections
    conn.execute(
        "INSERT INTO zendesk_articles_fts(zendesk_articles_fts) "
        "VALUES ('delete-all')")
    conn.execute(
        "INSERT INTO zendesk_articles_fts(rowid, title, body_text, labels_json) "
        "SELECT rowid, title, COALESCE(body_text, ''), "
        "COALESCE(labels_json, '[]') FROM zendesk_articles")
    conn.execute(
        "INSERT INTO zendesk_macros_fts(zendesk_macros_fts) "
        "VALUES ('delete-all')")
    conn.execute(
        "INSERT INTO zendesk_macros_fts(rowid, name, description, actions_text) "
        "SELECT rowid, name, COALESCE(description, ''), "
        "COALESCE(actions_text, '') FROM zendesk_macros")
    conn.commit()
    return {"articles": fixed_articles, "macros": fixed_macros}


# ── sync (read → cache; delegates to the mirror upserts) ─────────────

def sync_articles(conn: sqlite3.Connection, zendesk_client, *, locale="en-us") -> dict:
    try:
        articles = zendesk_client.get_articles(locale=locale)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
    for a in articles:
        if isinstance(a, dict):
            a.setdefault("locale", locale)
    upsert_articles(conn, articles, origin="pull")
    return {"ok": True, "count": len(articles)}


def sync_macros(conn: sqlite3.Connection, zendesk_client) -> dict:
    try:
        macros = zendesk_client.list_macros()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
    upsert_macros(conn, macros, origin="pull")
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


# ── mirror reads (mig 051) ───────────────────────────────────────────

def list_articles_full(conn, *, section_id=None, limit=500) -> list[dict]:
    sql = "SELECT * FROM zendesk_articles"
    params: list = []
    if section_id is not None:
        sql += " WHERE section_id=?"
        params.append(section_id)
    sql += " ORDER BY updated_at DESC, article_id LIMIT ?"
    params.append(limit)
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def list_sections(conn) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM zendesk_sections "
        "ORDER BY COALESCE(position, 999999), name").fetchall()
    return [dict(r) for r in rows]


def list_categories(conn) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM zendesk_categories "
        "ORDER BY COALESCE(position, 999999), name").fetchall()
    return [dict(r) for r in rows]


def get_article(conn, article_id: int) -> dict | None:
    row = conn.execute(
        "SELECT * FROM zendesk_articles WHERE article_id=?",
        (article_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    try:
        d["labels"] = json.loads(d.get("labels_json") or "[]")
    except (ValueError, TypeError):
        d["labels"] = []
    return d


def get_macro(conn, macro_id: int) -> dict | None:
    row = conn.execute(
        "SELECT * FROM zendesk_macros WHERE macro_id=?", (macro_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    try:
        d["actions"] = json.loads(d.get("actions_json") or "[]")
    except (ValueError, TypeError):
        d["actions"] = []
    return d


# ── mirror search (contentless FTS5) ─────────────────────────────────

def _match_expr(query: str) -> str:
    """Sanitized FTS5 MATCH expression: quoted tokens OR'd together (raw
    user text — quotes, hyphens, parens — must never reach MATCH syntax)."""
    tokens = _FTS_TOKEN_RE.findall((query or "").lower())
    return " OR ".join(f'"{t}"' for t in tokens[:24])


def _snippet(text: str, terms: list[str], *, width=160) -> str:
    t = text or ""
    low = t.lower()
    pos = -1
    for term in terms:
        pos = low.find(term)
        if pos >= 0:
            break
    if pos < 0:
        return " ".join(t[:width].split())
    start = max(0, pos - width // 3)
    return " ".join(t[start:start + width].split())


def search_mirror(conn, query: str, *, kind="all", limit=25) -> dict:
    """FTS search over the mirror. Empty mirror / no hits / hostile query
    all degrade to empty lists — never raises."""
    out: dict = {"articles": [], "macros": []}
    expr = _match_expr(query)
    if not expr:
        return out
    if kind not in ("all", "articles", "macros"):
        kind = "all"
    try:
        limit = max(1, min(int(limit), 100))
    except (TypeError, ValueError):
        limit = 25
    terms = _FTS_TOKEN_RE.findall((query or "").lower())

    if kind in ("all", "articles"):
        try:
            hits = conn.execute(
                "SELECT rowid, rank FROM zendesk_articles_fts "
                "WHERE zendesk_articles_fts MATCH ? ORDER BY rank LIMIT ?",
                (expr, limit)).fetchall()
        except sqlite3.Error:
            hits = []
        for rowid, rank in hits:
            base = conn.execute(
                "SELECT article_id, title, section_id, body_text "
                "FROM zendesk_articles WHERE rowid=?", (rowid,)).fetchone()
            if not base:
                continue
            out["articles"].append({
                "id": base[0], "title": base[1], "section_id": base[2],
                "snippet": _snippet(base[3] or base[1] or "", terms),
                "score": round(-(rank or 0.0), 4)})

    if kind in ("all", "macros"):
        try:
            hits = conn.execute(
                "SELECT rowid, rank FROM zendesk_macros_fts "
                "WHERE zendesk_macros_fts MATCH ? ORDER BY rank LIMIT ?",
                (expr, limit)).fetchall()
        except sqlite3.Error:
            hits = []
        for rowid, rank in hits:
            base = conn.execute(
                "SELECT macro_id, name, description, actions_text "
                "FROM zendesk_macros WHERE rowid=?", (rowid,)).fetchone()
            if not base:
                continue
            out["macros"].append({
                "id": base[0], "name": base[1], "description": base[2] or "",
                "snippet": _snippet(base[3] or base[1] or "", terms),
                "score": round(-(rank or 0.0), 4)})
    return out


# ── revisions (unified draft view + lifecycle) ───────────────────────

def list_revisions(conn, *, status=None, kind=None, limit=500) -> list[dict]:
    """Unified view over both draft tables; each row carries kind
    'article'|'macro', target_id, target_title (joined from the mirror).
    ``limit`` is clamped to 1..500 — the web Revision Center reads the
    default, and the old 100 ceiling silently truncated the copied/pushed
    audit trail (which purge deliberately retains forever)."""
    try:
        limit = max(1, min(int(limit), 500))
    except (TypeError, ValueError):
        limit = 500
    out: list[dict] = []
    if kind in (None, "article"):
        sql = ("SELECT d.id, d.article_id, d.title, d.status, d.rationale, "
               "d.sources_json, d.created_at, d.updated_at, d.copied_at, "
               "a.title AS target_title "
               "FROM zendesk_article_drafts d "
               "LEFT JOIN zendesk_articles a ON a.article_id = d.article_id")
        params: list = []
        if status is not None:
            sql += " WHERE d.status=?"
            params.append(status)
        for r in conn.execute(sql, params).fetchall():
            out.append({"kind": "article", "draft_id": r[0], "target_id": r[1],
                        "target_title": r[9], "title": r[2], "status": r[3],
                        "rationale": r[4], "sources_json": r[5] or "[]",
                        "created_at": r[6], "updated_at": r[7],
                        "copied_at": r[8]})
    if kind in (None, "macro"):
        sql = ("SELECT d.id, d.macro_id, d.name, d.status, d.rationale, "
               "d.sources_json, d.created_at, d.updated_at, d.copied_at, "
               "m.name AS target_title "
               "FROM zendesk_macro_drafts d "
               "LEFT JOIN zendesk_macros m ON m.macro_id = d.macro_id")
        params = []
        if status is not None:
            sql += " WHERE d.status=?"
            params.append(status)
        for r in conn.execute(sql, params).fetchall():
            out.append({"kind": "macro", "draft_id": r[0], "target_id": r[1],
                        "target_title": r[9], "title": r[2], "status": r[3],
                        "rationale": r[4], "sources_json": r[5] or "[]",
                        "created_at": r[6], "updated_at": r[7],
                        "copied_at": r[8]})
    out.sort(key=lambda r: (r.get("created_at") or "", r["draft_id"]),
             reverse=True)
    return out[:limit]


def set_draft_status(conn, kind: str, draft_id: int, status: str, *,
                     expected=None) -> dict:
    """Validated lifecycle transition. Every illegal move is refused against
    the CURRENT DB status; ``expected`` catches stale-UI races. Legacy
    'pushed' rows (and 'copied') are immutable here."""
    table = _DRAFT_TABLES.get(kind)
    if table is None:
        return {"ok": False, "error": "invalid_kind"}
    row = conn.execute(f"SELECT status FROM {table} WHERE id=?",
                       (draft_id,)).fetchone()
    if row is None:
        return {"ok": False, "error": "draft_not_found"}
    current = row[0]
    if expected is not None and current != expected:
        return {"ok": False, "error": "status_changed"}
    if (current, status) not in _ALLOWED_TRANSITIONS:
        return {"ok": False, "error": "invalid_transition"}
    now = _now()
    with _txn(conn):
        if status == "copied":
            conn.execute(
                f"UPDATE {table} SET status=?, copied_at=?, updated_at=? "
                "WHERE id=?", (status, now, now, draft_id))
        else:
            conn.execute(
                f"UPDATE {table} SET status=?, updated_at=? WHERE id=?",
                (status, now, draft_id))
    return {"ok": True, "error": None, "draft_id": draft_id, "status": status}


def delete_draft(conn, kind: str, draft_id: int) -> dict:
    table = _DRAFT_TABLES.get(kind)
    if table is None:
        return {"ok": False, "error": "invalid_kind"}
    with _txn(conn):
        cur = conn.execute(f"DELETE FROM {table} WHERE id=?", (draft_id,))
        deleted = cur.rowcount
    if not deleted:
        return {"ok": False, "error": "draft_not_found"}
    return {"ok": True, "error": None, "deleted": deleted}


def purge_mirror(conn, *, scope="all") -> dict:
    """Delete mirror rows. scope='all' also deletes pending/ready drafts but
    ALWAYS retains 'copied'/'pushed' rows — the audit trail of what went into
    real Zendesk (removable only one-at-a-time via delete_draft)."""
    if scope not in ("all", "articles", "macros", "imported"):
        return {"ok": False, "error": "invalid_scope"}
    counts = {"articles": 0, "macros": 0, "sections": 0, "categories": 0,
              "article_drafts": 0, "macro_drafts": 0}
    with _txn(conn):
        if scope == "imported":
            counts["articles"] = conn.execute(
                "DELETE FROM zendesk_articles WHERE origin='import'").rowcount
            counts["macros"] = conn.execute(
                "DELETE FROM zendesk_macros WHERE origin='import'").rowcount
        else:
            if scope in ("all", "articles"):
                counts["articles"] = conn.execute(
                    "DELETE FROM zendesk_articles").rowcount
            if scope in ("all", "macros"):
                counts["macros"] = conn.execute(
                    "DELETE FROM zendesk_macros").rowcount
            if scope == "all":
                counts["sections"] = conn.execute(
                    "DELETE FROM zendesk_sections").rowcount
                counts["categories"] = conn.execute(
                    "DELETE FROM zendesk_categories").rowcount
                counts["article_drafts"] = conn.execute(
                    "DELETE FROM zendesk_article_drafts "
                    "WHERE status IN ('pending','ready')").rowcount
                counts["macro_drafts"] = conn.execute(
                    "DELETE FROM zendesk_macro_drafts "
                    "WHERE status IN ('pending','ready')").rowcount
    return {"ok": True, **counts}


# ── article drafts ───────────────────────────────────────────────────

def save_article_draft(conn, *, title, body, article_id=None, section_id=None,
                       source_ref=None, body_html=None, rationale=None,
                       sources_json=None) -> int:
    with _txn(conn):
        cur = conn.execute(
            "INSERT INTO zendesk_article_drafts (article_id, section_id, title, "
            "body, source_ref, body_html, rationale, sources_json, "
            "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (article_id, section_id, title, body, source_ref, body_html,
             rationale, _norm_json(sources_json), _now(), _now()),
        )
        return int(cur.lastrowid)


def get_article_draft(conn, draft_id) -> dict | None:
    row = conn.execute(
        "SELECT * FROM zendesk_article_drafts WHERE id=?", (draft_id,)).fetchone()
    return dict(row) if row else None


def list_article_drafts(conn, *, status="pending", limit=100,
                        include_ai=False) -> list[dict]:
    """Rows with rationale set (AI provenance — only propose_* writes it)
    are hidden unless include_ai=True, so the native tab's Push button can
    never see a Renn draft (web surfaces pass include_ai=True)."""
    sql = ("SELECT id, article_id, title, status, source_ref, updated_at "
           "FROM zendesk_article_drafts WHERE status=?")
    if not include_ai:
        sql += " AND rationale IS NULL"
    sql += " ORDER BY updated_at DESC LIMIT ?"
    rows = conn.execute(sql, (status, limit)).fetchall()
    return [dict(r) for r in rows]


def update_article_draft(conn, draft_id, *, title=None, body=None,
                         body_html=None) -> dict:
    d = get_article_draft(conn, draft_id)
    if not d:
        return {"ok": False, "error": "draft_not_found"}
    with _txn(conn):
        conn.execute(
            "UPDATE zendesk_article_drafts SET title=?, body=?, body_html=?, "
            "updated_at=? WHERE id=?",
            (title if title is not None else d["title"],
             body if body is not None else d["body"],
             body_html if body_html is not None else d.get("body_html"),
             _now(), draft_id))
    return {"ok": True, "draft_id": draft_id}


def link_article_draft(conn, draft_id, article_id) -> None:
    with _txn(conn):
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

    with _txn(conn):
        conn.execute(
            "UPDATE zendesk_article_drafts SET status='pushed', article_id=?, "
            "pushed_at=?, updated_at=? WHERE id=?",
            (article_id, _now(), _now(), draft_id))
    return {"ok": True, "draft_id": draft_id, "article_id": article_id, "result": result}


# ── macro drafts ─────────────────────────────────────────────────────

def save_macro_draft(conn, *, name, actions, description=None, macro_id=None,
                     source_ref=None, reply_html=None, rationale=None,
                     sources_json=None) -> int:
    with _txn(conn):
        cur = conn.execute(
            "INSERT INTO zendesk_macro_drafts (macro_id, name, description, "
            "actions_json, source_ref, reply_html, rationale, sources_json, "
            "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (macro_id, name, description, json.dumps(actions or []), source_ref,
             reply_html, rationale, _norm_json(sources_json), _now(), _now()))
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


def update_macro_draft(conn, draft_id, *, name=None, actions=None,
                       reply_html=None) -> dict:
    d = get_macro_draft(conn, draft_id)
    if not d:
        return {"ok": False, "error": "draft_not_found"}
    with _txn(conn):
        conn.execute(
            "UPDATE zendesk_macro_drafts SET name=?, actions_json=?, "
            "reply_html=?, updated_at=? WHERE id=?",
            (name if name is not None else d["name"],
             json.dumps(actions if actions is not None else d["actions"]),
             reply_html if reply_html is not None else d.get("reply_html"),
             _now(), draft_id))
    return {"ok": True, "draft_id": draft_id}


def list_macro_drafts(conn, *, status="pending", limit=100,
                      include_ai=False) -> list[dict]:
    sql = ("SELECT id, macro_id, name, status, updated_at "
           "FROM zendesk_macro_drafts WHERE status=?")
    if not include_ai:
        sql += " AND rationale IS NULL"
    sql += " ORDER BY updated_at DESC LIMIT ?"
    rows = conn.execute(sql, (status, limit)).fetchall()
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
    with _txn(conn):
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
