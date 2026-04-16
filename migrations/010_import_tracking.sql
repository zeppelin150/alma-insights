-- Migration 010: Import run tracking for auditing and dedupe support
-- Stage 1 of persistent data architecture

CREATE TABLE IF NOT EXISTS import_runs (
    run_id             TEXT PRIMARY KEY,
    started_at         TEXT NOT NULL,
    completed_at       TEXT,
    source             TEXT NOT NULL,        -- 'csv' or 'lightdash'
    mode               TEXT NOT NULL,        -- 'incremental' or 'full_refresh'
    file_name          TEXT,
    tickets_seen       INTEGER DEFAULT 0,
    tickets_new        INTEGER DEFAULT 0,
    tickets_skipped    INTEGER DEFAULT 0,
    status             TEXT DEFAULT 'running',
    error_message      TEXT
);

CREATE INDEX IF NOT EXISTS idx_import_runs_status ON import_runs(source, status);
