-- Migration 011: Source registry for multi-source data architecture
-- Stage 2 of persistent data architecture

CREATE TABLE IF NOT EXISTS source_registry (
    source_id          TEXT PRIMARY KEY,
    source_name        TEXT NOT NULL,
    source_type        TEXT NOT NULL,        -- 'zendesk', 'kodif', 'custom'
    table_prefix       TEXT NOT NULL UNIQUE,
    column_mapping     TEXT,                 -- JSON: maps source columns to internal schema
    created_at         TEXT NOT NULL,
    is_default         INTEGER DEFAULT 0,
    ticket_count       INTEGER DEFAULT 0,
    last_import_at     TEXT
);

CREATE INDEX IF NOT EXISTS idx_source_registry_type ON source_registry(source_type);

-- Register the default Zendesk source
INSERT OR IGNORE INTO source_registry (source_id, source_name, source_type, table_prefix, created_at, is_default)
VALUES ('zendesk_default', 'Zendesk Support', 'zendesk', 'zendesk_default', datetime('now'), 1);
