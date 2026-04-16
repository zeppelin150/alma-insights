-- Phase 4: Guru Knowledge Base Integration tables
-- Executed by schema_migrator.py

-- Guru article cache (content synced from Guru API)
CREATE TABLE IF NOT EXISTS guru_articles (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id         TEXT    NOT NULL UNIQUE,
    collection_id   TEXT    DEFAULT '',
    collection_name TEXT    DEFAULT '',
    title           TEXT    NOT NULL,
    content_hash    TEXT    DEFAULT '',
    last_synced_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    friction_score  REAL    DEFAULT 0.0,
    status          TEXT    DEFAULT 'active'
);

-- Maps friction types (from sub_patterns) to Guru articles
CREATE TABLE IF NOT EXISTS guru_friction_coverage (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    friction_type   TEXT    NOT NULL,
    card_id         TEXT    NOT NULL REFERENCES guru_articles(card_id),
    coverage_score  REAL    DEFAULT 0.0,
    gap_description TEXT    DEFAULT '',
    analyzed_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    scan_id         TEXT    DEFAULT '',
    UNIQUE(friction_type, card_id)
);
CREATE INDEX IF NOT EXISTS idx_gfc_friction ON guru_friction_coverage(friction_type);

-- Effectiveness tracking: does updating Guru reduce ticket volume?
CREATE TABLE IF NOT EXISTS guru_effectiveness (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id          TEXT    NOT NULL REFERENCES guru_articles(card_id),
    friction_type    TEXT    NOT NULL,
    measurement_date TEXT    NOT NULL,
    source           TEXT    DEFAULT '',
    pre_volume       REAL    DEFAULT 0.0,
    post_volume      REAL    DEFAULT 0.0,
    pre_window_days  INTEGER DEFAULT 14,
    post_window_days INTEGER DEFAULT 14,
    delta_pct        REAL    DEFAULT 0.0,
    is_significant   INTEGER DEFAULT 0,
    created_at       TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- LLM-generated content drafts
CREATE TABLE IF NOT EXISTS guru_content_drafts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id         TEXT    DEFAULT '',
    friction_type   TEXT    NOT NULL,
    draft_type      TEXT    NOT NULL,
    title           TEXT    NOT NULL,
    content         TEXT    NOT NULL,
    source_tickets  TEXT    DEFAULT '',
    status          TEXT    NOT NULL DEFAULT 'pending',
    approved_by     TEXT    DEFAULT '',
    pushed_at       TEXT    DEFAULT '',
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_gcd_status ON guru_content_drafts(status);
