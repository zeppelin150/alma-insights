-- ─────────────────────────────────────────────────────────────────────
-- Migration 036 — Agent Jobs (M4: Job-builder + sidebar tracker)
--
-- A small, fully-decoupled jobs spine for the standalone Agent chat: when a
-- task spans multiple phases, the Agent turns it into a first-class "job"
-- (header + ordered steps) that the sidebar tracks live. Local-only — no
-- ticket/warehouse coupling. Jobs are scoped to the chat session that spawned
-- them (mirrors how chat_tool_executions is session-scoped), so the Agent
-- sidebar only ever surfaces its own work.
-- ─────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS agent_jobs (
    job_id        TEXT PRIMARY KEY,
    session_id    TEXT,                            -- chat session that spawned it (nullable)
    title         TEXT NOT NULL,
    kind          TEXT,                            -- freeform label, e.g. 'card_update_batch'
    status        TEXT NOT NULL DEFAULT 'running', -- running | done | error | cancelled
    progress_pct  INTEGER NOT NULL DEFAULT 0,      -- 0..100 (auto-derived from steps)
    summary       TEXT,                            -- short current-activity line
    tokens_in     INTEGER NOT NULL DEFAULT 0,
    tokens_out    INTEGER NOT NULL DEFAULT 0,
    cost_usd      REAL NOT NULL DEFAULT 0,
    error         TEXT,
    created_by    TEXT NOT NULL DEFAULT 'agent',   -- 'agent' | 'user'
    created_at    TEXT,
    updated_at    TEXT,
    completed_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_agent_jobs_session ON agent_jobs(session_id, updated_at);
CREATE INDEX IF NOT EXISTS idx_agent_jobs_status  ON agent_jobs(status);

CREATE TABLE IF NOT EXISTS agent_job_steps (
    step_id     TEXT PRIMARY KEY,
    job_id      TEXT NOT NULL REFERENCES agent_jobs(job_id) ON DELETE CASCADE,
    ordinal     INTEGER NOT NULL,
    name        TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',   -- pending | running | done | error | skipped
    detail      TEXT,
    created_at  TEXT,
    updated_at  TEXT,
    UNIQUE(job_id, ordinal)
);
CREATE INDEX IF NOT EXISTS idx_agent_job_steps_job ON agent_job_steps(job_id, ordinal);
