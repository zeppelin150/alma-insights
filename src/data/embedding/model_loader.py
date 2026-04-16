"""Singleton model loader for EmbeddingGemma-300M.

Loads the model once on first access with air-gap verification.
All subsequent calls return the cached instance.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

MODEL_HF_ID = "Qwen/Qwen3-Embedding-0.6B"     # HuggingFace download ID
MODEL_NAME = "Qwen3-Embedding-0.6B"           # Local directory name
MODEL_DIR = Path(__file__).resolve().parent.parent.parent.parent / "data" / "models"
EMBEDDING_DIM = 1024
MAX_SEQ_LENGTH = 32768

_model = None


def is_available() -> bool:
    """Check if the embedding model is usable.

    Returns True if sentence_transformers is installed AND
    model files exist locally.
    """
    try:
        import sentence_transformers  # noqa: F401
    except ImportError:
        return False

    return _model_files_exist()


def get_model():
    """Get the singleton SentenceTransformer model.

    Enforces air-gap before loading. Returns None if unavailable.
    """
    global _model
    if _model is not None:
        return _model

    if not is_available():
        logger.warning("Embedding model not available (missing files or library)")
        return None

    from src.data.embedding.air_gap import enforce_air_gap
    enforce_air_gap()

    try:
        from sentence_transformers import SentenceTransformer
        model_path = _find_model_path()
        if model_path is None:
            logger.error("Model path not found in %s", MODEL_DIR)
            return None
        _model = SentenceTransformer(str(model_path))
        logger.info("Loaded embedding model from %s", model_path)
        return _model
    except Exception as e:
        logger.error("Failed to load embedding model: %s", e)
        return None


def _model_files_exist() -> bool:
    """Check if model files are present locally."""
    if not MODEL_DIR.is_dir():
        return False
    # Look for model directories matching the model name
    matches = list(MODEL_DIR.glob(f"*{MODEL_NAME}*"))
    if matches:
        return True
    # Also check for any model with a config.json (generic check)
    return any(MODEL_DIR.glob("*/config.json"))


def _find_model_path() -> Path | None:
    """Find the actual model directory path.

    Handles both clean directories (from snapshot_download with local_dir)
    and HF cache structure (models--Org--Name/snapshots/<hash>/).
    """
    # 1. Exact name match (clean install via snapshot_download)
    exact = MODEL_DIR / MODEL_NAME
    if exact.is_dir() and (exact / "config.json").exists():
        return exact

    # 2. HF cache structure: models--Org--Name/snapshots/<hash>/
    for d in sorted(MODEL_DIR.iterdir()):
        if not d.is_dir():
            continue
        snapshots = d / "snapshots"
        if snapshots.is_dir():
            for snap in sorted(snapshots.iterdir()):
                if snap.is_dir() and (snap / "config.json").exists():
                    return snap

    # 3. Glob match on model name
    matches = sorted(MODEL_DIR.glob(f"*{MODEL_NAME}*"))
    for m in matches:
        if m.is_dir() and (m / "config.json").exists():
            return m

    # 4. Fallback: any directory with config.json
    for d in sorted(MODEL_DIR.iterdir()):
        if d.is_dir() and (d / "config.json").exists():
            return d
    return None
