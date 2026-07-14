-- Migration 047 — Content-studio artifact spine (WS3-M1, renn-calendar-kb-studio).
--
-- One registry for every studio artifact (diagram / quiz / deck / one_pager /
-- battle_card / podcast-reserved) with source linkage, provenance, lifecycle
-- status, and a managed on-disk home (data/artifacts/<id>/ — outside the
-- installer's APP_CONTENTS, so nothing ships and files survive updates).
--
-- NO CHECK constraints on kind/status: SQLite cannot ALTER a CHECK, which
-- would freeze the enum (the 'podcast' seam is a frozenset edit in
-- artifact_store.KINDS, not a table rebuild). Whitelists live in
-- src/data/artifact_store.py, mirroring enablement_tasks._UPDATABLE.
-- Idempotent (IF NOT EXISTS). Numbering: 043-045=WS1, 046=WS2, 047=WS3
-- (pre-allocated across workstreams by the plan).
CREATE TABLE IF NOT EXISTS enablement_artifacts (
    artifact_id     TEXT PRIMARY KEY,                 -- uuid4 hex
    kind            TEXT NOT NULL,                    -- diagram|quiz|deck|one_pager|battle_card|podcast
    title           TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'draft',    -- draft|rendered|attached|published|archived
    task_id         TEXT,                             -- enablement_tasks soft ref
    card_id         TEXT,                             -- guru card soft ref
    research_id     TEXT,                             -- task_research soft ref
    doc_id          TEXT,                             -- enablement_documents soft ref
    draft_id        INTEGER,                          -- guru_content_drafts soft ref
    deck_id         INTEGER,                          -- pptx_decks soft ref
    spec_json       TEXT NOT NULL DEFAULT '{}',       -- mermaid source / quiz json / outline ref
    file_path       TEXT,                             -- rendered/exported file inside artifact_dir
    provenance_json TEXT NOT NULL DEFAULT '{}',       -- model, prompt template, retries, source refs
    created_by      TEXT NOT NULL DEFAULT 'agent',
    session_id      TEXT,                             -- chat session that generated it
    created_at      TEXT,
    updated_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_artifacts_kind_status ON enablement_artifacts(kind, status);
CREATE INDEX IF NOT EXISTS idx_artifacts_task        ON enablement_artifacts(task_id, updated_at);
CREATE INDEX IF NOT EXISTS idx_artifacts_session     ON enablement_artifacts(session_id, updated_at);
