"""Read/write layer for the help-article corpus.

Plain execute+commit, never ``atomic()`` — the loader runs at startup and the
search path runs inside the MCP subprocess, neither of which owns a
transaction.

``status`` is the honesty mechanism and is validated on write: several
documented features are stubs, flag-gated off, or unreachable, and the UI
renders the status as a badge plus a banner. A typo'd status would silently
present a broken feature as working, so an unknown value is rejected rather
than stored.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime

# Ordered least→most limited. The UI renders anything other than "available"
# with a banner stating plainly what does not work.
STATUSES = ("available", "partial", "flag-gated", "not-available")

_STATUS_BANNER = {
    "partial": "Parts of this feature are not finished yet — the gaps are "
               "called out below.",
    "flag-gated": "This feature is behind a setting that is currently off, so "
                  "it will not appear in the app until it is enabled.",
    "not-available": "This feature is not available yet. It is described here "
                     "so you know what is planned and what to expect.",
}


def status_banner(status: str) -> str:
    """The banner text for a status, or '' when the feature simply works."""
    return _STATUS_BANNER.get(str(status or ""), "")


def upsert_article(conn: sqlite3.Connection, article: dict) -> bool:
    """Insert or update one article. Returns True when a write happened.

    Skips the write when ``content_hash`` is unchanged, so re-running the
    loader on every launch is close to free and does not churn the FTS
    triggers.
    """
    status = str(article.get("status") or "available")
    if status not in STATUSES:
        raise ValueError(
            f"unknown help status {status!r} for article "
            f"{article.get('article_id')!r} — expected one of {STATUSES}")
    article_id = str(article.get("article_id") or "").strip()
    if not article_id:
        raise ValueError("help article is missing an article_id")

    content_hash = str(article.get("content_hash") or "")
    row = conn.execute(
        "SELECT content_hash FROM help_articles WHERE article_id = ?",
        (article_id,),
    ).fetchone()
    if row is not None and content_hash and row[0] == content_hash:
        return False

    features = article.get("features") or []
    if not isinstance(features, list):
        features = [features]
    payload = (
        article_id,
        str(article.get("title") or ""),
        str(article.get("section") or ""),
        str(article.get("section_title") or ""),
        int(article.get("section_order") or 0),
        int(article.get("article_order") or 0),
        status,
        str(article.get("applies_to") or "enablement"),
        str(article.get("summary") or ""),
        str(article.get("body") or ""),
        json.dumps([str(f) for f in features]),
        str(article.get("last_verified") or ""),
        content_hash,
        datetime.now().isoformat(timespec="seconds"),
    )
    conn.execute(
        """INSERT INTO help_articles
               (article_id, title, section, section_title, section_order,
                article_order, status, applies_to, summary, body,
                features_json, last_verified, content_hash, loaded_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(article_id) DO UPDATE SET
               title=excluded.title, section=excluded.section,
               section_title=excluded.section_title,
               section_order=excluded.section_order,
               article_order=excluded.article_order,
               status=excluded.status, applies_to=excluded.applies_to,
               summary=excluded.summary, body=excluded.body,
               features_json=excluded.features_json,
               last_verified=excluded.last_verified,
               content_hash=excluded.content_hash,
               loaded_at=excluded.loaded_at""",
        payload,
    )
    conn.commit()
    return True


def _row_to_dict(row) -> dict:
    d = dict(row)
    try:
        d["features"] = json.loads(d.get("features_json") or "[]")
    except Exception:  # noqa: BLE001 — a malformed row must not break the tab
        d["features"] = []
    d["banner"] = status_banner(d.get("status", ""))
    return d


def list_articles(conn: sqlite3.Connection, *, section: str | None = None,
                  applies_to: str = "enablement") -> list[dict]:
    """Every article in reading order, optionally one section.

    ``applies_to`` matches the article's own value or ``both``, so a future
    product-mode corpus can share the table without a content migration.
    """
    sql = ("SELECT * FROM help_articles "
           "WHERE (applies_to = ? OR applies_to = 'both')")
    params: list = [applies_to]
    if section:
        sql += " AND section = ?"
        params.append(section)
    sql += " ORDER BY section_order, article_order, title"
    return [_row_to_dict(r) for r in conn.execute(sql, params).fetchall()]


def get_article(conn: sqlite3.Connection, article_id: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM help_articles WHERE article_id = ?",
        (str(article_id or ""),),
    ).fetchone()
    return _row_to_dict(row) if row is not None else None


def list_sections(conn: sqlite3.Connection,
                  applies_to: str = "enablement") -> list[dict]:
    """Section headings in ToC order, with per-section article counts."""
    rows = conn.execute(
        """SELECT section, section_title, section_order,
                  COUNT(*) AS n_articles
           FROM help_articles
           WHERE (applies_to = ? OR applies_to = 'both')
           GROUP BY section, section_title, section_order
           ORDER BY section_order, section_title""",
        (applies_to,),
    ).fetchall()
    return [dict(r) for r in rows]


def count_articles(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT COUNT(*) FROM help_articles").fetchone()[0])
