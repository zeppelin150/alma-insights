-- Migration 012: Create per-source tables for default Zendesk source
-- Copies existing data from shared tables to zendesk_default_* tables
-- OLD tables are KEPT as deprecated (removed in Stage 3 after verification)

-- Create per-source tickets table
CREATE TABLE IF NOT EXISTS zendesk_default_tickets (
    ticket_id       TEXT PRIMARY KEY,
    subject         TEXT,
    trc_code        TEXT,
    trc_label       TEXT,
    status          TEXT,
    priority        TEXT,
    channel         TEXT,
    csat_score      REAL,
    created_at      TEXT,
    updated_at      TEXT,
    solved_at       TEXT,
    requester_name  TEXT,
    requester_email TEXT,
    assignee_name   TEXT,
    group_name      TEXT,
    tags            TEXT,
    custom_fields   TEXT,
    assignment_to_resolution_hours  REAL,
    total_resolution_hours          REAL,
    first_reply_hours               REAL,
    requester_hash  TEXT DEFAULT '',
    provider_id     TEXT,
    client_id       TEXT,
    source_id       TEXT NOT NULL DEFAULT 'zendesk_default',
    imported_at     TEXT,
    metadata        TEXT DEFAULT '{}'
);

-- Create per-source conversations table
CREATE TABLE IF NOT EXISTS zendesk_default_conversations (
    ticket_id       TEXT PRIMARY KEY,
    subject         TEXT,
    trc_code        TEXT,
    trc_label       TEXT,
    status          TEXT,
    csat_score      REAL,
    created_at      TEXT,
    solved_at       TEXT,
    message_count   INTEGER,
    client_messages INTEGER,
    agent_messages  INTEGER,
    full_thread     TEXT,
    thread_preview  TEXT,
    dataset_id      INTEGER DEFAULT 0,
    source_id       TEXT NOT NULL DEFAULT 'zendesk_default',
    FOREIGN KEY (ticket_id) REFERENCES zendesk_default_tickets(ticket_id)
);

-- Create per-source comments table
CREATE TABLE IF NOT EXISTS zendesk_default_comments (
    comment_id      TEXT PRIMARY KEY,
    ticket_id       TEXT,
    author_name     TEXT,
    author_role     TEXT,
    body            TEXT,
    is_public       INTEGER DEFAULT 1,
    created_at      TEXT,
    source_id       TEXT NOT NULL DEFAULT 'zendesk_default',
    FOREIGN KEY (ticket_id) REFERENCES zendesk_default_tickets(ticket_id)
);

-- Copy existing data from shared tables to per-source tables
INSERT OR IGNORE INTO zendesk_default_tickets
    (ticket_id, subject, trc_code, trc_label, status, priority, channel,
     csat_score, created_at, updated_at, solved_at, requester_name,
     requester_email, assignee_name, group_name, tags, custom_fields,
     assignment_to_resolution_hours, total_resolution_hours, first_reply_hours,
     requester_hash, source_id, imported_at)
SELECT
    ticket_id, subject, trc_code, trc_label, status, priority, channel,
    csat_score, created_at, updated_at, solved_at, requester_name,
    requester_email, assignee_name, group_name, tags, custom_fields,
    assignment_to_resolution_hours, total_resolution_hours, first_reply_hours,
    requester_hash, 'zendesk_default', datetime('now')
FROM tickets;

INSERT OR IGNORE INTO zendesk_default_conversations
    (ticket_id, subject, trc_code, trc_label, status, csat_score,
     created_at, solved_at, message_count, client_messages, agent_messages,
     full_thread, thread_preview, dataset_id, source_id)
SELECT
    ticket_id, subject, trc_code, trc_label, status, csat_score,
    created_at, solved_at, message_count, client_messages, agent_messages,
    full_thread, thread_preview, dataset_id, 'zendesk_default'
FROM conversations;

INSERT OR IGNORE INTO zendesk_default_comments
    (comment_id, ticket_id, author_name, author_role, body, is_public,
     created_at, source_id)
SELECT
    comment_id, ticket_id, author_name, author_role, body, is_public,
    created_at, 'zendesk_default'
FROM comments;

-- Create per-source FTS index
CREATE VIRTUAL TABLE IF NOT EXISTS zendesk_default_fts USING fts5(
    ticket_id, subject, trc_label, full_thread
);

-- Populate FTS from per-source conversations
INSERT OR IGNORE INTO zendesk_default_fts (ticket_id, subject, trc_label, full_thread)
SELECT ticket_id, subject, trc_label, full_thread
FROM zendesk_default_conversations;

-- Update ticket count in source registry
UPDATE source_registry
SET ticket_count = (SELECT COUNT(*) FROM zendesk_default_tickets)
WHERE source_id = 'zendesk_default';
