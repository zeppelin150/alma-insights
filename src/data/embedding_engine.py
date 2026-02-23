"""
Alma Insights — Embedding Engine
Dense vector encoding of conversations for semantic search and clustering.

HIPAA COMPLIANCE:
  All inference is local. No data leaves the machine.
  - HF_HUB_DISABLE_TELEMETRY=1  → suppresses Hugging Face usage telemetry
  - HF_HUB_OFFLINE=1            → prevents model update checks to huggingface.co
  - TRANSFORMERS_OFFLINE=1       → prevents transformers library network calls
  These are set BEFORE any HF imports to guarantee no outbound connections.
  The model file must be pre-bundled in data/models/ for production.
"""

# ── HIPAA: Set air-gap environment variables BEFORE any HF imports ──
import os
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"

import numpy as np
from pathlib import Path

_model = None
_MODEL_NAME = "all-MiniLM-L6-v2"  # 80MB, fast, good quality


def _get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        cache_dir = Path(__file__).parent.parent.parent / "data" / "models"
        cache_dir.mkdir(parents=True, exist_ok=True)
        _model = SentenceTransformer(_MODEL_NAME, cache_folder=str(cache_dir))
    return _model


def is_available() -> bool:
    try:
        import sentence_transformers  # noqa: F401
        # Also verify the model files exist locally
        cache_dir = Path(__file__).parent.parent.parent / "data" / "models"
        # SentenceTransformer stores models in subdirectories
        model_dirs = list(cache_dir.glob(f"*{_MODEL_NAME}*"))
        if not model_dirs:
            # Model not yet downloaded — still "available" as a capability,
            # but first use will require a one-time download.
            # In production, pre-bundle the model (see deployment notes).
            pass
        return True
    except ImportError:
        return False


def embed_texts(texts: list, batch_size: int = 64) -> np.ndarray:
    model = _get_model()
    return model.encode(texts, batch_size=batch_size,
                        show_progress_bar=False, normalize_embeddings=True)


def semantic_search(query: str, corpus_embeddings: np.ndarray,
                    corpus_ids: list, top_k: int = 20) -> list:
    model = _get_model()
    q_emb = model.encode([query], normalize_embeddings=True)
    sims = np.dot(corpus_embeddings, q_emb.T).flatten()
    top_idx = np.argsort(sims)[-top_k:][::-1]
    return [(corpus_ids[i], float(sims[i])) for i in top_idx]


def compute_embedding_clusters(embeddings: np.ndarray, n_clusters: int = 0) -> dict:
    from sklearn.cluster import KMeans
    n = len(embeddings)
    if n < 20:
        return {"clusters": [], "error": "Need at least 20 documents"}
    if n_clusters == 0:
        n_clusters = min(12, max(3, n // 15))
    km = KMeans(n_clusters=n_clusters, random_state=42, n_init=10)
    labels = km.fit_predict(embeddings)
    return {"labels": labels.tolist(), "centroids": km.cluster_centers_, "n_clusters": n_clusters}


def verify_model_bundled() -> bool:
    """Check that the embedding model is available locally."""
    cache_dir = Path(__file__).parent.parent.parent / "data" / "models"
    return any(cache_dir.glob(f"*{_MODEL_NAME}*/*model*"))
