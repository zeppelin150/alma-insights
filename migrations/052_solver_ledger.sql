-- ─────────────────────────────────────────────────────────────────────────────
-- Migration 052 — Solver ledger (task-solver M0)
--
-- Per-call / per-tool audit spine for background solver jobs. A solver job is
-- an agent_jobs row (kind 'task_solve'); these tables record what each job
-- actually did: every Claude CLI invocation (solver_calls, row inserted
-- BEFORE the subprocess spawns so killed/hung calls still leave a trace),
-- every MCP tool executed inside a call (solver_tool_execs — the watermark
-- target for claimed-work verification), stage outputs with provenance back
-- to the calls that produced them, drafts in a human-gated state machine
-- with mandatory rationale + citations, and one row per self-correction
-- check with the action its consumer took (a check nothing consumes cannot
-- be recorded).
--
-- Enablement lane — no PHI/warehouse data. Cross-lane references
-- (agent_jobs.job_id, mirror/Drive/Guru ids) are soft refs by convention;
-- real FKs only within this migration's own parent/child pairs.
-- Status vocabularies live in src/data/solver_ledger.py (no CHECK
-- constraints — SQLite cannot ALTER a CHECK, which would freeze the enums).
-- Idempotent (IF NOT EXISTS).
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS solver_calls (
    call_id            TEXT PRIMARY KEY,              -- uuid4 hex
    job_id             TEXT NOT NULL,                 -- agent_jobs.job_id (soft ref)
    stage              TEXT NOT NULL,                 -- frame | research | review | sufficiency | draft | execute
    attempt            INTEGER NOT NULL DEFAULT 1,    -- authoritative counter is COUNT/MAX over this table
    purpose            TEXT NOT NULL DEFAULT 'worker', -- worker | verifier | repair
    bridge_instance_id TEXT,                          -- which ClaudeCliBridge instance ran it
    request_id         TEXT,                          -- bridge-level request id
    model              TEXT,                          -- model alias/id passed to the CLI
    bedrock_active     INTEGER NOT NULL DEFAULT 0,    -- 1 = Bedrock env injection confirmed for this spawn
    prompt_sha         TEXT,                          -- sha256 of prompt_text
    prompt_text        TEXT,
    response_sha       TEXT,                          -- sha256 of response_text
    response_text      TEXT,
    tokens_in          INTEGER,
    tokens_out         INTEGER,
    cost_usd           REAL,
    duration_ms        INTEGER,
    stop_reason        TEXT,
    error_class        TEXT,                          -- transport | contract | semantic | environmental (NULL = clean)
    salvage_applied    INTEGER NOT NULL DEFAULT 0,    -- 1 = deterministic output repair ran
    status             TEXT NOT NULL DEFAULT 'spawned', -- spawned | done | error | killed | interrupted
    started_at         TEXT,                          -- ISO, set at insert (pre-spawn)
    finished_at        TEXT                           -- ISO, set by finish_call
);
CREATE INDEX IF NOT EXISTS idx_solver_calls_job    ON solver_calls(job_id, stage, attempt);
CREATE INDEX IF NOT EXISTS idx_solver_calls_status ON solver_calls(status);

-- AUTOINCREMENT is deliberate (cf. migration 038): the watermark verifier
-- needs a monotonic cursor whose ids are never reused after deletes.
CREATE TABLE IF NOT EXISTS solver_tool_execs (
    exec_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id        TEXT NOT NULL,                      -- agent_jobs.job_id (soft ref)
    call_id       TEXT,                               -- solver_calls.call_id (soft ref; subprocess-supplied)
    tool_name     TEXT NOT NULL,
    args_digest   TEXT,                               -- sha256 of canonical args JSON (content-free row)
    result_status TEXT NOT NULL DEFAULT 'ok',         -- ok | error
    created_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_solver_tool_execs_job ON solver_tool_execs(job_id);

CREATE TABLE IF NOT EXISTS solver_stage_results (
    result_id  TEXT PRIMARY KEY,                      -- uuid4 hex
    job_id     TEXT NOT NULL,                         -- agent_jobs.job_id (soft ref)
    stage      TEXT NOT NULL,                         -- frame | research | review | sufficiency | draft | execute
    attempt    INTEGER NOT NULL DEFAULT 1,
    status     TEXT NOT NULL DEFAULT 'pass',          -- pass | fail | parked
    payload_json TEXT,                                -- stage output (manifest / verdict / draft pointer)
    created_at TEXT,
    UNIQUE(job_id, stage, attempt)
);
CREATE INDEX IF NOT EXISTS idx_solver_results_job ON solver_stage_results(job_id, stage);

-- Provenance join: which calls produced a stage result (real FKs — same migration).
CREATE TABLE IF NOT EXISTS solver_result_calls (
    result_id TEXT NOT NULL REFERENCES solver_stage_results(result_id) ON DELETE CASCADE,
    call_id   TEXT NOT NULL REFERENCES solver_calls(call_id) ON DELETE CASCADE,
    UNIQUE(result_id, call_id)
);

CREATE TABLE IF NOT EXISTS solver_drafts (
    draft_id    TEXT PRIMARY KEY,                     -- uuid4 hex
    job_id      TEXT NOT NULL,                        -- agent_jobs.job_id (soft ref)
    kind        TEXT NOT NULL,                        -- edit | create | removal
    target_kind TEXT,                                 -- guru_card | zendesk_article | zendesk_macro | asana_task | drive_doc
    target_ref  TEXT,                                 -- external / mirror id (soft ref)
    title       TEXT NOT NULL,
    content_md  TEXT NOT NULL,
    rationale   TEXT NOT NULL,                        -- mandatory — store API rejects blank
    summary     TEXT,                                 -- job summary shown in Q/A
    status      TEXT NOT NULL DEFAULT 'pending',      -- pending | ready | executed | rejected
    created_at  TEXT,
    updated_at  TEXT,
    executed_at TEXT                                  -- ISO, set once on ready -> executed
);
CREATE INDEX IF NOT EXISTS idx_solver_drafts_job ON solver_drafts(job_id, status);

CREATE TABLE IF NOT EXISTS solver_citations (
    citation_id   TEXT PRIMARY KEY,                   -- uuid4 hex
    draft_id      TEXT NOT NULL REFERENCES solver_drafts(draft_id) ON DELETE CASCADE,
    source_kind   TEXT NOT NULL,                      -- drive | corpus | zendesk_mirror | guru | asana
    source_ref    TEXT NOT NULL,                      -- doc / card / article / task id (soft ref)
    quote         TEXT,                               -- evidence quote backing the claim
    overlap_score REAL                                -- stem-lite vocabulary overlap vs source
);
CREATE INDEX IF NOT EXISTS idx_solver_citations_draft ON solver_citations(draft_id);

CREATE TABLE IF NOT EXISTS solver_checks (
    check_id        TEXT PRIMARY KEY,                 -- uuid4 hex
    job_id          TEXT NOT NULL,                    -- agent_jobs.job_id (soft ref)
    stage           TEXT NOT NULL,
    attempt         INTEGER NOT NULL DEFAULT 1,
    call_id         TEXT,                             -- solver_calls.call_id (soft ref, nullable)
    name            TEXT NOT NULL,                    -- e.g. citation_joins, watermark, corpus_containment
    kind            TEXT NOT NULL DEFAULT 'deterministic', -- deterministic | llm_verifier
    verdict         TEXT NOT NULL,                    -- pass | fail | unknown
    reason          TEXT,                             -- "<tier>:<subject>:<detail>" closed vocab, content-free
    consumer_action TEXT NOT NULL,                    -- advanced | repaired | parked
    created_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_solver_checks_job ON solver_checks(job_id, stage, attempt);
