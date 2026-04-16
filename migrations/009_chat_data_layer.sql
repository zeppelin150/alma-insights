-- ═══════════════════════════════════════════════════════════════
--  Migration 009 — Chat Data Layer Redesign
--  Creates per-message storage, tool execution tracking,
--  project organization, FTS5 search, and summary views.
--  Data migration (JSON blob → rows) handled by Python post-hook.
-- ═══════════════════════════════════════════════════════════════

-- ═══ CHAT PROJECTS — organize sessions into folders ═══
CREATE TABLE IF NOT EXISTS chat_projects (
    project_id  TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT,
    sort_order  INTEGER DEFAULT 0,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- ═══ CHAT MESSAGES — per-message storage (replaces JSON blob) ═══
CREATE TABLE IF NOT EXISTS chat_messages (
    message_id   TEXT PRIMARY KEY,
    session_id   TEXT NOT NULL REFERENCES chat_sessions(session_id),
    ordinal      INTEGER NOT NULL,
    role         TEXT NOT NULL CHECK(role IN ('user','assistant','system','tool_result')),
    content      TEXT NOT NULL,
    created_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
    model_used   TEXT,
    tokens_in    INTEGER,
    tokens_out   INTEGER,
    cost_usd     REAL,
    latency_ms   INTEGER,
    tool_calls   TEXT,          -- JSON summary array
    tool_round   INTEGER DEFAULT 0,
    error_code   TEXT,
    error_message TEXT,
    metadata     TEXT,          -- JSON extensible
    UNIQUE(session_id, ordinal)
);
CREATE INDEX IF NOT EXISTS idx_chat_msg_session ON chat_messages(session_id, ordinal);
CREATE INDEX IF NOT EXISTS idx_chat_msg_created ON chat_messages(created_at);
CREATE INDEX IF NOT EXISTS idx_chat_msg_model   ON chat_messages(model_used);

-- ═══ CHAT TOOL EXECUTIONS — per-tool-call detail (powers Monitor) ═══
CREATE TABLE IF NOT EXISTS chat_tool_executions (
    execution_id  TEXT PRIMARY KEY,
    message_id    TEXT NOT NULL REFERENCES chat_messages(message_id),
    session_id    TEXT NOT NULL,   -- denormalized for fast session queries
    tool_name     TEXT NOT NULL,
    args_json     TEXT,
    result_json   TEXT,            -- truncated to 4KB max (PHI-safe)
    result_rows   INTEGER,
    tables_touched TEXT,           -- JSON array
    elapsed_ms    INTEGER,
    error         TEXT,
    created_at    DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_tool_exec_msg     ON chat_tool_executions(message_id);
CREATE INDEX IF NOT EXISTS idx_tool_exec_session ON chat_tool_executions(session_id);

-- ═══ FTS5 — full-text search on chat messages ═══
CREATE VIRTUAL TABLE IF NOT EXISTS chat_messages_fts USING fts5(
    content,
    session_id UNINDEXED,
    message_id UNINDEXED,
    content=chat_messages,
    content_rowid=rowid
);

-- FTS5 triggers to keep index in sync
CREATE TRIGGER IF NOT EXISTS chat_msg_fts_insert AFTER INSERT ON chat_messages BEGIN
    INSERT INTO chat_messages_fts(rowid, content, session_id, message_id)
    VALUES (new.rowid, new.content, new.session_id, new.message_id);
END;

CREATE TRIGGER IF NOT EXISTS chat_msg_fts_delete AFTER DELETE ON chat_messages BEGIN
    INSERT INTO chat_messages_fts(chat_messages_fts, rowid, content, session_id, message_id)
    VALUES ('delete', old.rowid, old.content, old.session_id, old.message_id);
END;

CREATE TRIGGER IF NOT EXISTS chat_msg_fts_update AFTER UPDATE ON chat_messages BEGIN
    INSERT INTO chat_messages_fts(chat_messages_fts, rowid, content, session_id, message_id)
    VALUES ('delete', old.rowid, old.content, old.session_id, old.message_id);
    INSERT INTO chat_messages_fts(rowid, content, session_id, message_id)
    VALUES (new.rowid, new.content, new.session_id, new.message_id);
END;

-- ═══ ADD project_id to chat_sessions ═══
ALTER TABLE chat_sessions ADD COLUMN project_id TEXT REFERENCES chat_projects(project_id);

-- ═══ VIRTUAL VIEWS ═══

-- Session summary for sidebar, chips, and drilldown cards
CREATE VIEW IF NOT EXISTS v_session_summary AS
SELECT
    s.session_id, s.title, s.source_page, s.project_id,
    s.created_at, s.updated_at, s.filter_json, s.ticket_count,
    COUNT(m.message_id) AS message_count,
    SUM(CASE WHEN m.role='user' THEN 1 ELSE 0 END) AS user_messages,
    SUM(CASE WHEN m.role='assistant' THEN 1 ELSE 0 END) AS assistant_messages,
    SUM(COALESCE(m.tokens_in, 0)) AS total_tokens_in,
    SUM(COALESCE(m.tokens_out, 0)) AS total_tokens_out,
    SUM(COALESCE(m.cost_usd, 0)) AS total_cost,
    MAX(m.created_at) AS last_message_at,
    (SELECT content FROM chat_messages
     WHERE session_id = s.session_id AND role = 'user'
     ORDER BY ordinal LIMIT 1) AS first_question
FROM chat_sessions s
LEFT JOIN chat_messages m ON m.session_id = s.session_id
GROUP BY s.session_id;

-- Tool usage per session (for Monitor totals)
CREATE VIEW IF NOT EXISTS v_session_tools AS
SELECT
    e.session_id,
    e.tool_name,
    COUNT(*) AS call_count,
    SUM(e.elapsed_ms) AS total_ms,
    AVG(e.elapsed_ms) AS avg_ms,
    SUM(e.result_rows) AS total_rows
FROM chat_tool_executions e
GROUP BY e.session_id, e.tool_name;

-- Daily cost tracking
CREATE VIEW IF NOT EXISTS v_chat_cost_daily AS
SELECT
    SUBSTR(m.created_at, 1, 10) AS date,
    m.model_used,
    COUNT(DISTINCT m.session_id) AS sessions,
    SUM(m.tokens_in) AS tokens_in,
    SUM(m.tokens_out) AS tokens_out,
    SUM(m.cost_usd) AS total_cost
FROM chat_messages m
WHERE m.role = 'assistant'
GROUP BY SUBSTR(m.created_at, 1, 10), m.model_used;
