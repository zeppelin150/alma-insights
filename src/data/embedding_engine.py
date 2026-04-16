"""
Alma Insights — Embedding Engine (DEPRECATED)

This module now redirects to src/data/embedding/.
All functions preserved for backward compatibility.

HIPAA COMPLIANCE:
  Air-gap enforcement moved to src/data/embedding/air_gap.py.
  Environment variables are set on import of the new package.
"""

import logging
import warnings

logger = logging.getLogger(__name__)
warnings.warn(
    "embedding_engine.py is deprecated. Use src.data.embedding instead.",
    DeprecationWarning,
    stacklevel=2,
)

# ── HIPAA: Enforce air-gap on import (preserves old behavior) ──
from src.data.embedding.air_gap import enforce_air_gap
enforce_air_gap()

# ── Re-export old API surface via compat shims ──
from src.data.embedding.compat import (  # noqa: F401
    embed_texts,
    semantic_search_compat as semantic_search,
    compute_embedding_clusters,
    verify_model_bundled,
    is_available,
)
