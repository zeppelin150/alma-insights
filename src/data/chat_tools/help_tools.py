"""Chat tool: search the in-app Help Center.

Renn reaches the same bundled help corpus the Help tab browses, so it can
answer "how does X work" and, crucially, tell the operator when a feature is
NOT actually available (each result carries its status). Search is the lexical
FTS5 ranker in ``src.data.help.search`` — no embeddings, no LLM in the ranking
path.

The MCP subprocess opens the warehouse DB directly (``ALMA_DB_PATH``) and has
no Help tab to lazily load the corpus, so this handler ensures it is loaded
before searching — idempotent, keyed on content hash, so a warm DB is a no-op.
A zero-hit search returns the section list rather than a dead end (the same
steering contract the KB tools follow).
"""

from __future__ import annotations

import logging

logger = logging.getLogger("alma.help.tool")


def _ensure_corpus(conn) -> bool:
    """Sync the bundled help corpus, then report whether it is queryable.

    Uses ``sync_bundled_help`` (loads only when the DB is BEHIND the bundled
    files), not a presence gate — a DB populated against an older, smaller
    corpus must pick up newly-shipped articles, not stay frozen at whatever it
    first loaded (the 2026-07-22 "8 of 62 articles" bug). Returns False only
    when the corpus could not be made available (e.g. the help_articles table
    does not exist because migration 048 has not run)."""
    try:
        from src.data.help import store
        from src.data.help.loader import sync_bundled_help
        sync_bundled_help(conn)
        return store.count_articles(conn) > 0
    except Exception as exc:  # noqa: BLE001 — table missing / not migrated
        logger.debug("help corpus sync failed: %s", exc)
        return False


def _sections(conn) -> list[str]:
    try:
        from src.data.help import store
        return [s["section_title"] for s in store.list_sections(conn)]
    except Exception:  # noqa: BLE001
        return []


def handle_help_search(conn, args: dict, filters: dict) -> dict:
    """Ranked lexical search over the in-app Help Center.

    Each result carries ``status`` (available | partial | flag-gated |
    not-available) so Renn can state a limitation before describing a feature
    rather than confidently explaining something that does not run.
    """
    query = str(args.get("query") or "").strip()
    if not query:
        return {"ok": False, "error": "query_required",
                "message": "help_search needs a question or keywords."}

    if not _ensure_corpus(conn):
        return {"ok": True, "results": [], "count": 0,
                "note": "The Help Center content is not available in this "
                        "database. Open the Help tab in the app to browse it."}

    section = str(args.get("section") or "").strip() or None
    try:
        limit = int(args.get("limit", 5))
    except (TypeError, ValueError):
        limit = 5
    limit = max(1, min(limit, 20))

    from src.data.help.search import search_help
    results = search_help(conn, query, section=section, limit=limit)

    if not results:
        # Never a dead end: hand back the table of contents so Renn can steer
        # the operator to a section instead of "nothing found".
        return {"ok": True, "results": [], "count": 0,
                "sections": _sections(conn),
                "note": "Nothing in the Help Center matched. Offer the section "
                        "list, or suggest fewer, more distinctive words."}

    trimmed = [
        {"article_id": r["article_id"], "title": r["title"],
         "section": r.get("section_title") or r.get("section"),
         "status": r.get("status"), "summary": r.get("summary"),
         "excerpt": r.get("excerpt")}
        for r in results
    ]
    # Surface a status warning inline so the model does not have to infer it.
    limited = [r for r in trimmed if r.get("status") not in (None, "available")]
    out = {"ok": True, "results": trimmed, "count": len(trimmed)}
    if limited:
        out["status_note"] = (
            "Some results describe features that are not fully available "
            "(status is partial / flag-gated / not-available). State the "
            "limitation before describing the feature.")
    return out
