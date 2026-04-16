"""Embedding engine — air-gapped EmbeddingGemma-300M for semantic search."""

from src.data.embedding.air_gap import enforce_air_gap, verify_air_gap
from src.data.embedding.model_loader import is_available, get_model
from src.data.embedding.encoder import embed_documents, embed_query
from src.data.embedding.search import semantic_search, load_embedding_cache
from src.data.embedding.builder import build_embeddings

__all__ = [
    "enforce_air_gap", "verify_air_gap",
    "is_available", "get_model",
    "embed_documents", "embed_query",
    "semantic_search", "load_embedding_cache",
    "build_embeddings",
]
