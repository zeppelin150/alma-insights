"""Stage runner — sequences the content-update pipeline.

Flat guard-clause style: each stage runs, its result is checked, and a failure
short-circuits to a typed PipelineResult naming the stage. The pipeline STOPS
after staging the draft — publishing is the separate, human-gated
``approve_and_publish``.
"""

from __future__ import annotations

from .diff import unified
from .find_card import find_target_card
from .identify_updates import identify_updates
from .load_source import load_source
from .models import ContentUpdateRequest, Deps, PipelineResult
from .pull_card import pull_target_card
from .validate import validate_update
from .write_targeted import write_targeted_updates


def run_content_update(conn, request: ContentUpdateRequest, deps: Deps) -> PipelineResult:
    """Run load → find → pull → identify → write → validate → stage draft."""
    src = load_source(conn, request, asana_client=deps.asana_client,
                      guru_client=deps.guru_client, http_get=deps.http_get,
                      drive_reader=deps.drive_reader)
    if not src.ok:
        return PipelineResult(ok=False, status="failed", stage="load_source", error=src.error)

    match = find_target_card(deps.guru_client, src, request)
    if match.status == "needs_human_pick":
        return PipelineResult(ok=False, status="ambiguous_card", stage="find_card",
                              candidates=match.candidates)
    if not match.ok:
        return PipelineResult(ok=False, status="failed", stage="find_card",
                              error=match.error, candidates=match.candidates)

    pulled = pull_target_card(conn, deps.guru_client, match)
    if not pulled.ok:
        return PipelineResult(ok=False, status="failed", stage="pull_card", error=pulled.error)

    plan = identify_updates(deps.llm_client, src, pulled)
    if not plan.ok:
        return PipelineResult(ok=False, status="failed", stage="identify", error=plan.error,
                              draft_id=pulled.draft_id, card_id=pulled.card_id)

    proposed = write_targeted_updates(deps.llm_client, pulled, plan, src, _style(conn))
    if not proposed.ok:
        return PipelineResult(ok=False, status="failed", stage="write", error=proposed.error,
                              draft_id=pulled.draft_id, card_id=pulled.card_id, plan=plan)

    issues = validate_update(pulled, proposed, plan)
    _stage_draft(conn, pulled.draft_id, proposed)
    from . import provenance
    provenance.record_from_result(conn, pulled.draft_id, pulled.card_id, src, plan, issues)
    diff = unified(pulled.current_md, proposed.content_md, title=proposed.title)
    return PipelineResult(ok=True, status="staged", stage="staged",
                          draft_id=pulled.draft_id, card_id=pulled.card_id,
                          plan=plan, proposed=proposed, diff=diff, issues=issues)


def _style(conn) -> str:
    from src.data.enablement_store import style_guide_block
    try:
        return style_guide_block(conn)
    except Exception:  # noqa: BLE001 — style guide is optional
        return ""


def _stage_draft(conn, draft_id: int, proposed) -> None:
    from src.data.enablement_store import update_draft_content
    update_draft_content(conn, draft_id, title=proposed.title, content=proposed.content_md)
