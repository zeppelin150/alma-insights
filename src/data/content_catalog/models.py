"""Dataclasses for the content catalog."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CatalogEntry:
    item_id: str
    item_type: str            # "card" | "doc"
    title: str
    source: str = ""          # guru | drive | upload | manual
    url: str = ""
    summary: str = ""
    topics: list[str] = field(default_factory=list)
    content_hash: str = ""
    updated_at: str = ""


@dataclass
class ScoredEntry:
    entry: CatalogEntry
    score: float
    vector_score: float
    lexical_score: float
    matched_terms: list[str] = field(default_factory=list)
