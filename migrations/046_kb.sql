-- Migration 046 — self-building Drive knowledge base (WS2, renn-calendar-kb-studio).
--
-- The EC folder tree in the operator's Google Drive is the durable,
-- human-readable cloud index (Markdown cards with YAML frontmatter); these
-- tables are the LOCAL MIRROR (rebuildable cache) + the write allowlist +
-- the work queue. kb_folders is the gate-exemption enforcement surface: the
-- single write chokepoint (src/data/kb/drive_kb.py) refuses any Drive write
-- whose parent is not a row here; the model never supplies folder ids.
-- All writes: plain execute+commit, never atomic() (worker threads + the MCP
-- subprocess). Idempotent (IF NOT EXISTS). Numbering: 043-045=WS1, 046=WS2,
-- 047=WS3 (pre-allocated by the plan).

CREATE TABLE IF NOT EXISTS kb_folders (
    folder_id   TEXT PRIMARY KEY,             -- Drive folder id
    topic       TEXT NOT NULL DEFAULT '',     -- slug ('' for the root)
    parent_id   TEXT,                         -- Drive parent id
    role        TEXT NOT NULL DEFAULT 'topic',-- ec_root | topic (whitelist in code)
    status      TEXT NOT NULL DEFAULT 'ok',   -- ok | quarantined (trashed/unwritable)
    created_at  TEXT
);

CREATE TABLE IF NOT EXISTS kb_cards (
    card_id         TEXT PRIMARY KEY,          -- kb-<uuid8> (stable, in filename)
    drive_file_id   TEXT UNIQUE,               -- Drive file id of the .md
    topic_folder_id TEXT,                      -- kb_folders soft ref
    title           TEXT NOT NULL DEFAULT '',
    type            TEXT NOT NULL DEFAULT 'source_summary',
    topics_json     TEXT NOT NULL DEFAULT '[]',
    source_id       TEXT NOT NULL DEFAULT '',  -- Drive fileId | guru:<id> | asana:<gid> | ''
    source_url      TEXT NOT NULL DEFAULT '',
    source_mime     TEXT NOT NULL DEFAULT '',
    source_modified TEXT NOT NULL DEFAULT '',  -- the SOURCE doc's modifiedTime at summarize time
    summary         TEXT NOT NULL DEFAULT '',
    key_facts_json  TEXT NOT NULL DEFAULT '[]',
    body_md         TEXT NOT NULL DEFAULT '',
    content_hash    TEXT NOT NULL DEFAULT '',  -- skip-unchanged guard
    drive_modified  TEXT NOT NULL DEFAULT '',  -- Drive modifiedTime of the card FILE (echo suppression)
    synced_at       TEXT,
    status          TEXT NOT NULL DEFAULT 'ok' -- ok | needs_repair | source_missing
);
CREATE INDEX IF NOT EXISTS idx_kb_cards_topic  ON kb_cards(topic_folder_id, status);
CREATE INDEX IF NOT EXISTS idx_kb_cards_source ON kb_cards(source_id);

-- FTS over the card fields (external content — kept in sync by triggers).
-- unicode61 + remove_diacritics locked NOW, before data exists (payer names).
CREATE VIRTUAL TABLE IF NOT EXISTS kb_cards_fts USING fts5(
    title, summary, key_facts, body, topics,
    content='',
    tokenize='unicode61 remove_diacritics 2'
);
CREATE TRIGGER IF NOT EXISTS kb_cards_ai AFTER INSERT ON kb_cards BEGIN
    INSERT INTO kb_cards_fts(rowid, title, summary, key_facts, body, topics)
    VALUES (new.rowid, new.title, new.summary, new.key_facts_json,
            new.body_md, new.topics_json);
END;
CREATE TRIGGER IF NOT EXISTS kb_cards_ad AFTER DELETE ON kb_cards BEGIN
    INSERT INTO kb_cards_fts(kb_cards_fts, rowid) VALUES ('delete', old.rowid);
END;
CREATE TRIGGER IF NOT EXISTS kb_cards_au AFTER UPDATE ON kb_cards BEGIN
    INSERT INTO kb_cards_fts(kb_cards_fts, rowid) VALUES ('delete', old.rowid);
    INSERT INTO kb_cards_fts(rowid, title, summary, key_facts, body, topics)
    VALUES (new.rowid, new.title, new.summary, new.key_facts_json,
            new.body_md, new.topics_json);
END;

CREATE TABLE IF NOT EXISTS kb_sync_state (
    folder_id   TEXT PRIMARY KEY,              -- kb_folders soft ref
    cursor      TEXT NOT NULL DEFAULT '',      -- modifiedTime watermark (RFC3339)
    last_run_at TEXT,
    last_status TEXT NOT NULL DEFAULT '',
    last_error  TEXT
);

CREATE TABLE IF NOT EXISTS kb_queue (
    queue_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT NOT NULL,                 -- index_folder | write_card | distill | refresh
    target      TEXT NOT NULL DEFAULT '',      -- coalescing key (folder_id / card_id / ref)
    payload_json TEXT NOT NULL DEFAULT '{}',
    status      TEXT NOT NULL DEFAULT 'pending', -- pending | claimed | done | error | dead
    attempts    INTEGER NOT NULL DEFAULT 0,    -- claim-lease retry counter
    job_id      TEXT,                          -- agent_jobs soft ref (progress UI)
    created_at  TEXT,
    claimed_at  TEXT
);
-- Coalesce on insert: at most one PENDING row per (kind, target).
CREATE UNIQUE INDEX IF NOT EXISTS idx_kb_queue_pending
    ON kb_queue(kind, target) WHERE status = 'pending';

CREATE TABLE IF NOT EXISTS kb_sync_log (
    log_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    action     TEXT NOT NULL,                  -- create | update | pull | repair_flag | conflict
    card_id    TEXT,
    drive_file_id TEXT,
    detail     TEXT,
    created_at TEXT
);
