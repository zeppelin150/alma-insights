"""Backward-compatibility shims for the old embedding_engine.py API.

Maps old function signatures to the new embedding package.
"""

from __future__ import annotations

import logging

import numpy as np

logger = logging.getLogger(__name__)


def embed_texts(texts: list, batch_size: int = 64) -> np.ndarray:
    """Shim: old embed_texts → new embed_documents."""
    from src.data.embedding.encoder import embed_documents
    return embed_documents(texts, batch_size=batch_size)


def semantic_search_compat(
    query: str, corpus_embeddings: np.ndarray,
    corpus_ids: list, top_k: int = 20,
) -> list:
    """Shim: old semantic_search → new embed_query + semantic_search."""
    from src.data.embedding.encoder import embed_query
    from src.data.embedding.search import semantic_search
    q_emb = embed_query(query)
    results = semantic_search(q_emb, corpus_embeddings, corpus_ids, top_k=top_k)
    # Old API returned list of (id, score) tuples
    return [(r["ticket_id"], r["similarity"]) for r in results]


def compute_embedding_clusters(
    embeddings: np.ndarray, n_clusters: int = 0,
) -> dict:
    """Preserved clustering function (unchanged from old engine)."""
    try:
        from sklearn.cluster import KMeans
    except ImportError:
        return {"clusters": [], "error": "sklearn not installed"}

    n = len(embeddings)
    if n < 20:
        return {"clusters": [], "error": "Need at least 20 documents"}
    if n_clusters == 0:
        n_clusters = min(12, max(3, n // 15))
    km = KMeans(n_clusters=n_clusters, random_state=42, n_init=10)
    labels = km.fit_predict(embeddings)
    return {
        "labels": labels.tolist(),
        "centroids": km.cluster_centers_,
        "n_clusters": n_clusters,
    }


def verify_model_bundled() -> bool:
    """Shim: old verify_model_bundled → new is_available."""
    from src.data.embedding.model_loader import is_available
    return is_available()


def is_available() -> bool:
    """Shim: old is_available → new is_available."""
    from src.data.embedding.model_loader import is_available as _is_available
    return _is_available()
