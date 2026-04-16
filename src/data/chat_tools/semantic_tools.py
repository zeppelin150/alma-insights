"""Semantic search chat tool handler.

Uses the EmbeddingGemma engine for similarity-based ticket retrieval.
Graceful fallback when model is not installed.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_DEFAULT_TOP_K = 10
_MAX_TOP_K = 50


def handle_semantic_search(conn, args: dict, session_filters: dict) -> dict:
    """Find tickets semantically similar to a query.

    Args:
        conn: SQLite connection.
        args: {query: str, top_k: int}.
        session_filters: Session-scoped filters.

    Returns:
        Dict with matches or graceful error.
    """
    query = args.get("query", "")
    if not query:
        return {"error": "query is required"}

    top_k = min(args.get("top_k", _DEFAULT_TOP_K), _MAX_TOP_K)

    # Check availability
    from src.data.embedding.model_loader import is_available
    if not is_available():
        return _fallback_response(query)

    try:
        return _run_search(conn, query, top_k, session_filters)
    except Exception as e:
        logger.warning("Semantic search failed: %s", e)
        return _fallback_response(query, error=str(e))


def _run_search(
    conn, query: str, top_k: int, session_filters: dict,
) -> dict:
    """Execute semantic search against cached embeddings."""
    from src.data.embedding.encoder import embed_query
    from src.data.embedding.search import semantic_search, load_embedding_cache

    # Load embeddings (scoped by session filters if present)
    embeddings, ticket_ids = load_embedding_cache(conn, session_filters)

    if len(ticket_ids) == 0:
        return {
            "matches": [],
            "count": 0,
            "note": "No embeddings available. Run embedding build first.",
        }

    # Encode query and search
    query_vec = embed_query(query)
    results = semantic_search(query_vec, embeddings, ticket_ids, top_k=top_k)

    # Enrich results with ticket metadata
    enriched = _enrich_results(conn, results)

    return {
        "matches": enriched,
        "count": len(enriched),
        "query": query,
    }


def _enrich_results(conn, results: list[dict]) -> list[dict]:
    """Add ticket metadata to search results."""
    if not results:
        return []

    ticket_ids = [r["ticket_id"] for r in results]
    placeholders = ", ".join("?" for _ in ticket_ids)
    rows = conn.execute(
        f"SELECT ticket_id, trc_code, friction_type, issue_snippet "
        f"FROM ticket_index WHERE ticket_id IN ({placeholders})",
        ticket_ids,
    ).fetchall()

    meta = {}
    for r in rows:
        meta[r[0]] = {
            "trc_code": r[1],
            "friction_type": r[2],
            "issue_snippet": r[3],
        }

    enriched = []
    for r in results:
        tid = r["ticket_id"]
        entry = {**r, **meta.get(tid, {})}
        enriched.append(entry)

    return enriched


def _fallback_response(query: str, error: str | None = None) -> dict:
    """Graceful fallback when embedding model is unavailable."""
    msg = "Semantic search unavailable — embedding model not installed."
    if error:
        msg += f" Error: {error}"
    return {
        "matches": [],
        "count": 0,
        "query": query,
        "note": msg,
    }
