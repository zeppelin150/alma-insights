"""Renn content-update pipeline: Asana doc -> find Guru card -> identify -> draft.

A deterministic, stage-by-stage pipeline (one module per stage) that lets Renn
review a source document (e.g. a doc attached to an Asana task), find the
matching Guru card, identify what needs updating, write the update, and stage a
draft for human approval. Publishing is a separate, human-gated call.

The two reasoning stages (identify, write) call an injected ``llm_client`` so
the pipeline is model-agnostic; production wires Haiku via
``build_client_for_task("enablement_card_update")``.
"""

from __future__ import annotations

from .models import (
    CardCandidate,
    CardMatch,
    Change,
    ContentUpdateRequest,
    Deps,
    PipelineResult,
    ProposedUpdate,
    PulledCard,
    SourceBundle,
    SourceDoc,
    UpdatePlan,
)
from .orchestrator import run_content_update
from .publish import approve_and_publish

__all__ = [
    "ContentUpdateRequest",
    "Deps",
    "PipelineResult",
    "SourceBundle",
    "SourceDoc",
    "CardMatch",
    "CardCandidate",
    "PulledCard",
    "UpdatePlan",
    "Change",
    "ProposedUpdate",
    "run_content_update",
    "approve_and_publish",
]
