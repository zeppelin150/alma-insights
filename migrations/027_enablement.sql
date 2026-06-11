-- ─────────────────────────────────────────────────────────────────────
-- Migration 027 — Enablement Workbench
--
-- Stands up the data spine for the Guru → Enablement Workbench redesign
-- (plan: ~/.claude/plans/guru-enablement-redesign.md). The Enablement tab
-- is an ETL: Extract (Drive/Guru/Asana, operator-configured in
-- monitor_sources) → Transform (the AI loop) → Load (enablement_tasks).
--
--   enablement_tasks      — the task spine; every monitor + manual entry
--                           writes here. dedup_key prevents duplicate tasks
--                           on re-poll.
--   enablement_subtasks   — per-task checklist (operator-added or AI-drafted).
--   enablement_documents  — local store of every pulled document (FULL text)
--                           so drafts/sources survive sessions and the chat
--                           can "look up an old document".
--   monitor_sources       — operator-configured Extract sources (typed
--                           config_json: Asana boards, Drive folders, Guru
--                           card ids).
--   asana_users           — gid → name cache for people custom-fields.
--
-- Reuses guru_content_drafts (migration 003) for card drafts — adds a
-- source_ref column linking a draft back to its originating document.
--
-- Idempotent: CREATE ... IF NOT EXISTS and a guarded ALTER (the schema
-- migrator's ADD-COLUMN helper skips duplicates — see
-- src/updater/schema_migrator.py).
-- ─────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS enablement_tasks (
    task_id       TEXT PRIMARY KEY,
    source        TEXT NOT NULL,                 -- 'drive'|'guru'|'asana'|'manual'
    source_ref    TEXT,                          -- drive fileId | guru cardId | asana gid | doc_id
    source_url    TEXT,
    kind          TEXT,                          -- 'card_review'|'doc_due_date'|'product_update'|'request'
    title         TEXT NOT NULL,
    summary       TEXT,
    due_date      TEXT,                          -- ISO date → Calendar; nullable
    priority      TEXT NOT NULL DEFAULT 'normal',-- 'low'|'normal'|'high'
    status        TEXT NOT NULL DEFAULT 'open',  -- 'open'|'in_progress'|'done'|'dismissed'
    assignee      TEXT,
    draft_id      INTEGER,                        -- FK → guru_content_drafts.id (card_review)
    scratchpad    TEXT,                           -- freeform operator notes
    llm_rationale TEXT,
    dedup_key     TEXT UNIQUE,
    created_by    TEXT NOT NULL DEFAULT 'agent',  -- 'agent'|'user'
    created_at    TEXT,
    updated_at    TEXT,
    completed_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_ent_status   ON enablement_tasks(status);
CREATE INDEX IF NOT EXISTS idx_ent_source   ON enablement_tasks(source);
CREATE INDEX IF NOT EXISTS idx_ent_due      ON enablement_tasks(due_date);

CREATE TABLE IF NOT EXISTS enablement_subtasks (
    subtask_id  TEXT PRIMARY KEY,
    task_id     TEXT NOT NULL REFERENCES enablement_tasks(task_id) ON DELETE CASCADE,
    text        TEXT NOT NULL,
    done        INTEGER NOT NULL DEFAULT 0,
    ordinal     INTEGER NOT NULL DEFAULT 0,
    created_by  TEXT NOT NULL DEFAULT 'user',    -- 'user'|'agent'
    created_at  TEXT,
    updated_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_est_task ON enablement_subtasks(task_id);

CREATE TABLE IF NOT EXISTS enablement_documents (
    doc_id        TEXT PRIMARY KEY,              -- drive fileId, or uuid for non-drive
    source        TEXT NOT NULL,                 -- 'drive'|'upload'|'manual'
    source_ref    TEXT,                          -- external id (= doc_id for drive)
    name          TEXT NOT NULL,
    mime_type     TEXT,
    web_url       TEXT,
    modified_time TEXT,                           -- source modified ts (watermark)
    content_hash  TEXT,                           -- sha256(full_text) — change detection
    full_text     TEXT,                           -- the indexed document body (stored locally)
    text_excerpt  TEXT,                           -- short preview
    due_dates_json TEXT,                          -- [{date, context}]
    card_draft_id INTEGER,                        -- FK → guru_content_drafts.id once drafted
    indexed_at    TEXT,
    updated_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_edoc_source   ON enablement_documents(source);
CREATE INDEX IF NOT EXISTS idx_edoc_name     ON enablement_documents(name);
CREATE INDEX IF NOT EXISTS idx_edoc_modified ON enablement_documents(modified_time);

CREATE TABLE IF NOT EXISTS monitor_sources (
    source_id    TEXT PRIMARY KEY,               -- 'drive:<folderId>'|'guru:<cardId>'|'asana:<projectGid>'
    source_type  TEXT NOT NULL,                  -- 'drive'|'guru'|'asana'
    display_name TEXT,
    config_json  TEXT,                            -- typed per-source config
    enabled      INTEGER NOT NULL DEFAULT 1,
    cursor       TEXT,                            -- Drive startPageToken | last modified ts
    last_run_at  TEXT,
    last_status  TEXT,
    last_error   TEXT,
    created_at   TEXT,
    updated_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_msrc_type ON monitor_sources(source_type);

CREATE TABLE IF NOT EXISTS asana_users (
    gid           TEXT PRIMARY KEY,
    name          TEXT,
    email         TEXT,
    workspace_gid TEXT,
    cached_at     TEXT
);

-- Link a card draft back to the document it was generated from.
ALTER TABLE guru_content_drafts ADD COLUMN source_ref TEXT;
