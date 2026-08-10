"""Content-studio artifact store (WS3-M1, renn-calendar-kb-studio plan).

One registry for every studio artifact: Mermaid diagrams, knowledge quizzes,
.pptx decks, one-pagers, battle-cards — and the reserved ``podcast`` kind,
which IS the deferred-TTS seam (adding it later = a generator tool + a preview
branch; the enum below already carries it, no migration needed).

Whitelists live here, not as SQL CHECK constraints (SQLite cannot ALTER a
CHECK — a table rebuild per new kind would defeat the seam). Mirrors the
``enablement_tasks._UPDATABLE`` pattern.

Writes run inside the MCP subprocess on a *raw* ``sqlite3`` connection too, so
this module uses explicit ``commit()``/``rollback()`` — never ``atomic()``
(the agent_jobs convention). Files live under ``data/artifacts/<artifact_id>/``,
anchored to the PROJECT ROOT like settings_manager (never the cwd), outside the
installer's APP_CONTENTS so artifacts survive updates and ship nothing.

Extension checklist for a NEW artifact kind (the M8 seam audit lives here):
  1. add the kind to ``KINDS`` (one line, no migration);
  2. add a generator tool that validates output deterministically and writes
     ``create_artifact(kind=..., spec_json=...)``;
  3. add a preview/render branch where artifacts are surfaced;
  4. render output goes in ``artifact_dir(artifact_id)``.
Nothing else may switch on ``kind`` — keep dispatch in those three places.
"""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

KINDS = frozenset({
    "diagram", "quiz", "deck", "one_pager", "battle_card",
    "podcast",   # reserved — the deferred-TTS seam (no other podcast code exists)
})
STATUSES = frozenset({"draft", "rendered", "attached", "published", "archived"})

# Columns update_artifact() may set (guards against SQL injection via **fields).
_UPDATABLE = frozenset({
    "title", "status", "task_id", "card_id", "research_id", "doc_id",
    "draft_id", "deck_id", "spec_json", "file_path", "provenance_json",
})

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_ARTIFACTS_ROOT = _PROJECT_ROOT / "data" / "artifacts"

# Strict, Windows-safe slug: the shared filename sanitizer (WS2 folder/card
# names and WS3 deck filenames all route through this — LLM-authored titles
# can carry ``:?/\"`` and friends).
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(text: str, *, max_len: int = 60) -> str:
    """Lowercase [a-z0-9-] slug, length-capped, never empty or dot-leading."""
    slug = _SLUG_RE.sub("-", (text or "").lower()).strip("-")[:max_len].strip("-")
    return slug or "untitled"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row) -> dict:
    if isinstance(row, sqlite3.Row):
        return {k: row[k] for k in row.keys()}
    return dict(row) if hasattr(row, "keys") else {}


def _safe_rollback(conn) -> None:
    try:
        conn.rollback()
    except Exception:  # noqa: BLE001
        pass


def create_artifact(conn, *, kind: str, title: str = "", spec: dict | None = None,
                    provenance: dict | None = None, session_id: str | None = None,
                    created_by: str = "agent", **links) -> str:
    """Create an artifact row; returns the artifact_id.

    ``links`` accepts the soft-ref columns (task_id/card_id/research_id/doc_id/
    draft_id/deck_id) — anything else raises (typo guard).
    """
    if kind not in KINDS:
        raise ValueError(f"unknown artifact kind {kind!r} (allowed: {sorted(KINDS)})")
    allowed_links = {"task_id", "card_id", "research_id", "doc_id", "draft_id", "deck_id"}
    bad = set(links) - allowed_links
    if bad:
        raise ValueError(f"unknown artifact link fields: {sorted(bad)}")
    artifact_id = uuid.uuid4().hex
    now = _now()
    try:
        conn.execute(
            """INSERT INTO enablement_artifacts
               (artifact_id, kind, title, status, task_id, card_id, research_id,
                doc_id, draft_id, deck_id, spec_json, provenance_json,
                created_by, session_id, created_at, updated_at)
               VALUES (?, ?, ?, 'draft', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (artifact_id, kind, title, links.get("task_id"), links.get("card_id"),
             links.get("research_id"), links.get("doc_id"), links.get("draft_id"),
             links.get("deck_id"), json.dumps(spec or {}),
             json.dumps(provenance or {}), created_by, session_id, now, now),
        )
        conn.commit()
    except Exception:
        _safe_rollback(conn)
        raise
    return artifact_id


def get_artifact(conn, artifact_id: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM enablement_artifacts WHERE artifact_id = ?",
        (artifact_id,)).fetchone()
    return _row_to_dict(row) if row is not None else None


def update_artifact(conn, artifact_id: str, **fields) -> bool:
    """Update whitelisted fields; unknown fields are REJECTED loudly (unlike
    enablement_tasks' silent drop — the pre-mortem's remote_modified_at lesson)."""
    bad = set(fields) - _UPDATABLE
    if bad:
        raise ValueError(f"non-updatable artifact fields: {sorted(bad)}")
    if not fields:
        return False
    if "status" in fields and fields["status"] not in STATUSES:
        raise ValueError(f"unknown artifact status {fields['status']!r}")
    for key in ("spec_json", "provenance_json"):
        if key in fields and not isinstance(fields[key], str):
            fields[key] = json.dumps(fields[key] or {})
    cols = ", ".join(f"{k} = ?" for k in fields)
    try:
        cur = conn.execute(
            f"UPDATE enablement_artifacts SET {cols}, updated_at = ? WHERE artifact_id = ?",
            (*fields.values(), _now(), artifact_id),
        )
        conn.commit()
        return cur.rowcount > 0
    except Exception:
        _safe_rollback(conn)
        raise


def list_artifacts(conn, *, kind: str | None = None, status: str | None = None,
                   task_id: str | None = None, session_id: str | None = None,
                   limit: int = 50) -> list[dict]:
    """Complete enumeration (newest first) with optional filters — the LIST
    tool contract: no ranking, no sampling."""
    where, params = [], []
    if kind:
        where.append("kind = ?")
        params.append(kind)
    if status:
        where.append("status = ?")
        params.append(status)
    if task_id:
        where.append("task_id = ?")
        params.append(task_id)
    if session_id:
        where.append("session_id = ?")
        params.append(session_id)
    sql = "SELECT * FROM enablement_artifacts"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY updated_at DESC LIMIT ?"
    params.append(max(1, min(int(limit), 500)))
    return [_row_to_dict(r) for r in conn.execute(sql, params).fetchall()]


def artifacts_root() -> Path:
    """Public accessor for the artifacts root (read-only use, e.g. the
    documents-tree backfill sweep). Does not create the directory."""
    return _ARTIFACTS_ROOT


def artifact_dir(artifact_id: str) -> Path:
    """The artifact's managed on-disk directory (created on first use).
    Anchored to the project root — NEVER the cwd."""
    if not re.fullmatch(r"[a-f0-9]{8,64}", (artifact_id or "").lower()):
        raise ValueError("artifact_dir requires a hex artifact_id")
    path = _ARTIFACTS_ROOT / artifact_id.lower()
    path.mkdir(parents=True, exist_ok=True)
    return path
