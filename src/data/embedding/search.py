"""Semantic search and embedding cache loading.

Provides cosine similarity search over pre-computed embeddings
and loads cached embeddings from the ticket_embeddings table.
"""

from __future__ import annotations

import logging

import numpy as np

logger = logging.getLogger(__name__)

_MIN_SIMILARITY = 0.1  # Filter out very low similarity results


def semantic_search(
    query_embedding: np.ndarray,
    corpus_embeddings: np.ndarray,
    corpus_ids: list[str],
    top_k: int = 10,
    min_similarity: float = _MIN_SIMILARITY,
) -> list[dict]:
    """Find the most similar documents to a query.

    Args:
        query_embedding: Query vector (EMBEDDING_DIM,).
        corpus_embeddings: Document matrix (N, EMBEDDING_DIM).
        corpus_ids: Ticket IDs corresponding to rows.
        top_k: Number of results to return.
        min_similarity: Minimum cosine similarity threshold.

    Returns:
        List of {ticket_id, similarity} dicts, sorted descending.
    """
    if len(corpus_embeddings) == 0:
        return []

    # Cosine similarity (embeddings are L2-normalized)
    similarities = np.dot(corpus_embeddings, query_embedding)

    # Apply threshold
    mask = similarities >= min_similarity
    valid_indices = np.where(mask)[0]

    if len(valid_indices) == 0:
        return []

    # Sort by similarity descending
    valid_sims = similarities[valid_indices]
    sorted_order = np.argsort(valid_sims)[::-1][:top_k]

    results = []
    for idx in sorted_order:
        orig_idx = valid_indices[idx]
        results.append({
            "ticket_id": corpus_ids[orig_idx],
            "similarity": round(float(valid_sims[idx]), 4),
        })

    return results


def load_embedding_cache(
    conn, session_filters: dict | None = None,
) -> tuple[np.ndarray, list[str]]:
    """Load cached embeddings from ticket_embeddings table.

    Args:
        conn: SQLite connection.
        session_filters: Optional filter dict to scope results.

    Returns:
        (embeddings_matrix, ticket_ids) tuple.
        Empty arrays if no embeddings cached.
    """
    if session_filters:
        return _load_filtered(conn, session_filters)
    return _load_all(conn)


def _load_all(conn) -> tuple[np.ndarray, list[str]]:
    """Load all cached embeddings."""
    rows = conn.execute(
        "SELECT ticket_id, embedding_blob, dim_size "
        "FROM ticket_embeddings ORDER BY ticket_id"
    ).fetchall()

    if not rows:
        return np.array([]), []

    ticket_ids = []
    embeddings = []
    for row in rows:
        ticket_ids.append(row[0])
        blob = row[1]
        dim = row[2]
        vec = np.frombuffer(blob, dtype=np.float32).copy()
        if len(vec) == dim:
            embeddings.append(vec)
        else:
            logger.warning("Dimension mismatch for %s: %d vs %d", row[0], len(vec), dim)

    if not embeddings:
        return np.array([]), []

    return np.vstack(embeddings), ticket_ids


def _load_filtered(
    conn, session_filters: dict,
) -> tuple[np.ndarray, list[str]]:
    """Load embeddings scoped by session filters."""
    from src.data.filter_engine import build_filter_query
    sql, params = build_filter_query(
        filters=session_filters,
        select_columns=["ti.ticket_id"],
        base_table="ticket_index",
    )

    # Get ticket IDs in scope
    scope_ids = {r[0] for r in conn.execute(sql, params).fetchall()}
    if not scope_ids:
        return np.array([]), []

    # Load only those embeddings
    placeholders = ", ".join("?" for _ in scope_ids)
    rows = conn.execute(
        f"SELECT ticket_id, embedding_blob, dim_size "
        f"FROM ticket_embeddings WHERE ticket_id IN ({placeholders}) "
        f"ORDER BY ticket_id",
        list(scope_ids),
    ).fetchall()

    if not rows:
        return np.array([]), []

    ticket_ids = []
    embeddings = []
    for row in rows:
        ticket_ids.append(row[0])
        vec = np.frombuffer(row[1], dtype=np.float32).copy()
        if len(vec) == row[2]:
            embeddings.append(vec)

    if not embeddings:
        return np.array([]), []

    return np.vstack(embeddings), ticket_ids
