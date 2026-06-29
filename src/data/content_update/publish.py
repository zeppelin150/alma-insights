"""Stage 7 — human-gated publish back to Guru.

Deliberately separate from ``run_content_update``: the pipeline only STAGES a
draft; a human reviews the diff and then calls this to push. The draft is already
card-linked, so ``publish_draft`` takes the UPDATE branch.
"""

from __future__ import annotations

from typing import Any


def approve_and_publish(conn, guru_client: Any, draft_id: int, *,
                        approved_by: str = "user",
                        collection_id: str | None = None,
                        folder_id: str | None = None) -> dict:
    """Publish a staged draft to Guru and mark it pushed."""
    from src.data import enablement_store as store
    return store.publish_draft(
        conn, int(draft_id),
        guru_client=guru_client,
        collection_id=collection_id,
        folder_id=folder_id,
        approved_by=approved_by,
    )
