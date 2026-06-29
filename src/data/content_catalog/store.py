"""SQLite-backed content catalog + human-readable .md export.

Lazy `CREATE TABLE IF NOT EXISTS` so it works in any connection (tests, live)
without a migration step. The .md export is the readable "file system" index.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from src.data.connection_factory import atomic

from .models import CatalogEntry

_DDL = """
CREATE TABLE IF NOT EXISTS content_catalog (
    item_id      TEXT PRIMARY KEY,
    item_type    TEXT NOT NULL,
    title        TEXT NOT NULL,
    source       TEXT DEFAULT '',
    url          TEXT DEFAULT '',
    summary      TEXT DEFAULT '',
    topics_json  TEXT DEFAULT '[]',
    content_hash TEXT DEFAULT '',
    updated_at   TEXT DEFAULT ''
)
"""


def ensure_table(conn) -> None:
    conn.execute(_DDL)
    conn.commit()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_entry(r) -> CatalogEntry:
    return CatalogEntry(
        item_id=r["item_id"], item_type=r["item_type"], title=r["title"],
        source=r["source"] or "", url=r["url"] or "", summary=r["summary"] or "",
        topics=json.loads(r["topics_json"] or "[]"),
        content_hash=r["content_hash"] or "", updated_at=r["updated_at"] or "")


def upsert_entry(conn, e: CatalogEntry) -> None:
    ensure_table(conn)
    with atomic(conn):
        conn.execute(
            """INSERT INTO content_catalog
               (item_id,item_type,title,source,url,summary,topics_json,content_hash,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?)
               ON CONFLICT(item_id) DO UPDATE SET
                 item_type=excluded.item_type, title=excluded.title, source=excluded.source,
                 url=excluded.url, summary=excluded.summary, topics_json=excluded.topics_json,
                 content_hash=excluded.content_hash, updated_at=excluded.updated_at""",
            (e.item_id, e.item_type, e.title, e.source, e.url, e.summary,
             json.dumps(e.topics), e.content_hash, e.updated_at or _now()))


def get_entry(conn, item_id: str) -> CatalogEntry | None:
    ensure_table(conn)
    r = conn.execute("SELECT * FROM content_catalog WHERE item_id=?", (item_id,)).fetchone()
    return _to_entry(r) if r else None


def all_entries(conn) -> list[CatalogEntry]:
    ensure_table(conn)
    return [_to_entry(r) for r in
            conn.execute("SELECT * FROM content_catalog ORDER BY updated_at DESC")]


def write_catalog_md(conn, path: str) -> str:
    """Render the catalog as a readable markdown index and write it to ``path``."""
    entries = all_entries(conn)
    lines = [f"# Content Catalog ({len(entries)} items)", ""]
    for e in entries:
        lines.append(f"## {e.title}")
        meta = f"- id: `{e.item_id}` · type: {e.item_type} · source: {e.source}"
        lines.append(meta)
        if e.url:
            lines.append(f"- url: {e.url}")
        if e.topics:
            lines.append(f"- topics: {', '.join(e.topics)}")
        lines.append(f"- summary: {e.summary}")
        lines.append("")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text("\n".join(lines), encoding="utf-8")
    return path
