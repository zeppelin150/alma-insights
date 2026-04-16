-- Migration 006: Hybrid chat architecture
-- Adds ticket-theme tagging, enriched trends, embedding persistence,
-- and expands chat_sessions for session-scoped filtering.

-- 1. Expand chat_sessions with filter/ticket/report tracking
ALTER TABLE chat_sessions ADD COLUMN filter_json TEXT;
ALTER TABLE chat_sessions ADD COLUMN ticket_count INTEGER;
ALTER TABLE chat_sessions ADD COLUMN active_report_ids TEXT;  -- JSON array

-- 2. Ticket-theme junction (links tickets to NLP findings)
CREATE TABLE IF NOT EXISTS ticket_theme_tags (
    ticket_id   TEXT NOT NULL,
    theme_id    TEXT NOT NULL,
    finding_id  TEXT,
    confidence  REAL DEFAULT 1.0,
    scan_id     TEXT NOT NULL,
    tagged_at   TEXT NOT NULL,
    PRIMARY KEY (ticket_id, theme_id)
);
CREATE INDEX IF NOT EXISTS idx_ttt_theme ON ticket_theme_tags(theme_id);
CREATE INDEX IF NOT EXISTS idx_ttt_scan ON ticket_theme_tags(scan_id);

-- 3. Enriched trends (post-NLP aggregated stats)
CREATE TABLE IF NOT EXISTS enriched_trends (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id         TEXT NOT NULL,
    dimension       TEXT NOT NULL,
    dimension_value TEXT NOT NULL,
    period          TEXT NOT NULL,
    ticket_count    INTEGER NOT NULL,
    pct_of_total    REAL,
    velocity        REAL,
    trc_breakdown   TEXT,
    UNIQUE(scan_id, dimension, dimension_value, period)
);
CREATE INDEX IF NOT EXISTS idx_et_dim ON enriched_trends(dimension, dimension_value);
CREATE INDEX IF NOT EXISTS idx_et_period ON enriched_trends(period);

-- 4. Embedding persistence (air-gapped local embeddings)
CREATE TABLE IF NOT EXISTS ticket_embeddings (
    ticket_id        TEXT PRIMARY KEY,
    embedding_blob   BLOB NOT NULL,
    source_text_hash TEXT NOT NULL,
    model_name       TEXT NOT NULL DEFAULT 'Qwen3-Embedding-0.6B',
    dim_size         INTEGER NOT NULL DEFAULT 1024,
    created_at       TEXT NOT NULL,
    FOREIGN KEY (ticket_id) REFERENCES tickets(ticket_id)
);
CREATE INDEX IF NOT EXISTS idx_te_model ON ticket_embeddings(model_name);
