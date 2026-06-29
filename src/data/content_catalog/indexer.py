"""Incremental catalog builder — summarize only changed items.

Each item carries a content_hash; an item is re-summarized only when its hash
changes, so re-indexing a large library is cheap and the catalog stays fresh.
"""

from __future__ import annotations

import hashlib

from .models import CatalogEntry
from .store import ensure_table, get_entry, upsert_entry
from .summarize import summarize_item


def _hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def index_items(conn, items: list[dict], *, llm_client=None) -> dict:
    """Summarize + upsert each item whose content changed.

    ``items``: dicts with item_id, item_type, title, source, url, text.
    Returns ``{"indexed", "skipped", "total"}``.
    """
    ensure_table(conn)
    indexed = skipped = 0
    for it in items:
        iid = str(it.get("item_id") or "").strip()
        if not iid:
            continue
        h = _hash(it.get("text", ""))
        existing = get_entry(conn, iid)
        if existing and existing.content_hash == h:
            skipped += 1
            continue
        s = summarize_item(llm_client, it.get("title", ""), it.get("text", ""))
        upsert_entry(conn, CatalogEntry(
            item_id=iid, item_type=it.get("item_type", "doc"), title=it.get("title", ""),
            source=it.get("source", ""), url=it.get("url", ""),
            summary=s["summary"], topics=s["topics"], content_hash=h))
        indexed += 1
    return {"indexed": indexed, "skipped": skipped, "total": len(items)}
