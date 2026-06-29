"""Dataclasses passed between content-update stages.

Each stage takes explicit inputs and returns one of these small result objects
carrying an ``ok``/``status`` flag, so the orchestrator stays a flat guard-clause
sequence (no nested branching) and every stage is unit-testable in isolation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class ContentUpdateRequest:
    """What to update and where to find the source of truth.

    Source (first non-empty wins): ``source_text`` > ``asana_task_gid`` >
    ``source_doc_ref``. Target card selection (first wins): ``target_card_ref``
    (id/URL) > ``target_card_name`` > live search by ``search_query`` (or the
    source title), scoped to ``collections`` when given.
    """

    # ── source ──
    asana_task_gid: str | None = None
    attachment_selector: str | None = None      # substring to pick the attachment by name
    source_doc_ref: str | None = None           # local path / stored doc_id / Drive ref
    source_text: str | None = None              # raw text (tests/demo)
    source_title: str | None = None
    # ── target card selection ──
    target_card_ref: str | None = None          # Guru card id or app.getguru.com URL
    target_card_name: str | None = None          # match by title
    search_query: str | None = None             # else search this (defaults to source title)
    collections: list[str] = field(default_factory=list)   # scope: collection ids/names
    # ── references we point at (authoritative) ──
    reference_refs: list[str] = field(default_factory=list)
    approved_by: str = "user"


@dataclass
class Deps:
    """Injected collaborators. Tests pass fakes; the demo passes real clients."""

    llm_client: Any
    guru_client: Any | None = None
    asana_client: Any | None = None
    http_get: Callable[[str], str] | None = None
    drive_reader: Any | None = None


@dataclass
class SourceDoc:
    ref: str
    title: str
    text: str


@dataclass
class SourceBundle:
    ok: bool
    primary: SourceDoc | None = None
    references: list[SourceDoc] = field(default_factory=list)
    error: str = ""


@dataclass
class CardCandidate:
    card_id: str
    title: str
    collection: str = ""
    score: float = 0.0


@dataclass
class CardMatch:
    ok: bool
    status: str = ""           # matched | needs_human_pick | not_found | error
    card_id: str = ""
    title: str = ""
    candidates: list[CardCandidate] = field(default_factory=list)
    error: str = ""


@dataclass
class PulledCard:
    ok: bool
    draft_id: int = 0
    card_id: str = ""
    title: str = ""
    current_md: str = ""
    error: str = ""


@dataclass
class Change:
    type: str                  # add | update | remove
    section: str
    reason: str
    evidence: str = ""


@dataclass
class UpdatePlan:
    ok: bool
    changes: list[Change] = field(default_factory=list)
    summary: str = ""
    error: str = ""


@dataclass
class ProposedUpdate:
    ok: bool
    title: str = ""
    content_md: str = ""
    error: str = ""


@dataclass
class PipelineResult:
    ok: bool
    status: str                # staged | ambiguous_card | failed
    stage: str = ""
    draft_id: int = 0
    card_id: str = ""
    plan: UpdatePlan | None = None
    proposed: ProposedUpdate | None = None
    diff: str = ""
    issues: list[str] = field(default_factory=list)
    candidates: list[CardCandidate] = field(default_factory=list)
    error: str = ""
