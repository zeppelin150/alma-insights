"""Document and query embedding functions.

Qwen3-Embedding supports native prompt_name="query" via
sentence-transformers — no manual prefix wrangling needed.
Documents are encoded without a prompt prefix.
"""

from __future__ import annotations

import logging

import numpy as np

logger = logging.getLogger(__name__)


def embed_documents(
    texts: list[str], batch_size: int = 32
) -> np.ndarray:
    """Embed a batch of document texts.

    Args:
        texts: List of document strings.
        batch_size: Encoding batch size.

    Returns:
        numpy array of shape (len(texts), EMBEDDING_DIM), L2-normalized.
    """
    from src.data.embedding.model_loader import get_model
    model = get_model()
    if model is None:
        raise RuntimeError("Embedding model not available")

    embeddings = model.encode(
        texts,
        batch_size=batch_size,
        show_progress_bar=False,
        normalize_embeddings=True,
    )
    return np.asarray(embeddings, dtype=np.float32)


def embed_query(query: str) -> np.ndarray:
    """Embed a single search query.

    Uses prompt_name="query" for retrieval-optimized encoding.

    Args:
        query: The search query string.

    Returns:
        numpy array of shape (EMBEDDING_DIM,), L2-normalized.
    """
    from src.data.embedding.model_loader import get_model
    model = get_model()
    if model is None:
        raise RuntimeError("Embedding model not available")

    embedding = model.encode(
        [query],
        prompt_name="query",
        show_progress_bar=False,
        normalize_embeddings=True,
    )
    return np.asarray(embedding[0], dtype=np.float32)
