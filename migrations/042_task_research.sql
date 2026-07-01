-- Migration 042 — per-task research manifest store (M6a).
--
-- The ask-first research engine writes a per-task research .md here that Renn
-- RAGs on later turns. A table (not disk+index) gives free session scoping and a
-- simple latest_for_task read. Enablement lane: soft ref to enablement_tasks,
-- no FK to the PHI warehouse. Idempotent (IF NOT EXISTS).
CREATE TABLE IF NOT EXISTS task_research (
    research_id   TEXT PRIMARY KEY,                 -- uuid4
    task_id       TEXT NOT NULL,                    -- enablement_tasks.task_id (soft ref)
    session_id    TEXT,                             -- chat session that ran it
    job_id        TEXT,                             -- agent_jobs.job_id that produced it
    title         TEXT,
    summary       TEXT,                             -- one-line for the sidebar / RAG hint
    markdown      TEXT NOT NULL DEFAULT '',         -- the research MD the operator talks to
    status        TEXT NOT NULL DEFAULT 'complete', -- running | complete | error | cancelled
    tokens_in     INTEGER NOT NULL DEFAULT 0,
    tokens_out    INTEGER NOT NULL DEFAULT 0,
    cost_usd      REAL NOT NULL DEFAULT 0,
    created_by    TEXT NOT NULL DEFAULT 'agent',
    created_at    TEXT,
    updated_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_task_research_task    ON task_research(task_id, updated_at);
CREATE INDEX IF NOT EXISTS idx_task_research_session ON task_research(session_id, updated_at);
