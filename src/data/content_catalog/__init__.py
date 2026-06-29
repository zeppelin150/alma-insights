"""Content catalog: a torch-free summary index for enablement content.

Instead of a vector DB over full documents, each PHI-free content item (Guru
card, uploaded/Drive doc) is reduced to a short LLM-written summary + metadata.
Retrieval is a deterministic hybrid (TF-IDF cosine + lexical) over those
summaries — no embedding model, no torch. The summary is the unit of retrieval,
which both shrinks the search surface ~10-20x and disambiguates look-alike
filenames by *content*.

Production can swap the local TF-IDF vectoriser for a hosted embedding (e.g.
Bedrock Titan) behind the same cosine search without touching callers.
"""

from __future__ import annotations

from .models import CatalogEntry, ScoredEntry
from .search import rank_entries, search_catalog
from .store import (
    all_entries, ensure_table, get_entry, upsert_entry, write_catalog_md,
)
from .indexer import index_items
from .summarize import summarize_item

__all__ = [
    "CatalogEntry",
    "ScoredEntry",
    "rank_entries",
    "search_catalog",
    "all_entries",
    "get_entry",
    "upsert_entry",
    "ensure_table",
    "write_catalog_md",
    "index_items",
    "summarize_item",
]
