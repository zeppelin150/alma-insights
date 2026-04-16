-- ═══════════════════════════════════════════════════════════════════
-- Migration 002 — Source-Agnostic Warehouse + Watchlist System
-- Phase 3.5: Persistent classified metadata, rule engine, EWMA learning
-- ═══════════════════════════════════════════════════════════════════

-- ── Source Events (Cold Tier) ────────────────────────────────────
-- One row per classified record from ANY source. No PHI stored.
-- Subject/description are NEVER written to this table.

CREATE TABLE IF NOT EXISTS source_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source          TEXT    NOT NULL DEFAULT 'zendesk',
    ticket_id       TEXT    NOT NULL,
    created_at      TEXT    NOT NULL,
    trc_code        TEXT    NOT NULL,
    classification  TEXT    DEFAULT '',
    sentiment       TEXT    DEFAULT '',
    priority        TEXT    DEFAULT '',
    ticket_type     TEXT    DEFAULT '',
    tags            TEXT    DEFAULT '',
    flagged         INTEGER DEFAULT 0,
    classified_by   TEXT    DEFAULT '',
    inserted_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE(source, ticket_id)
);

CREATE INDEX IF NOT EXISTS idx_se_source  ON source_events(source);
CREATE INDEX IF NOT EXISTS idx_se_trc     ON source_events(trc_code);
CREATE INDEX IF NOT EXISTS idx_se_created ON source_events(created_at);
CREATE INDEX IF NOT EXISTS idx_se_class   ON source_events(classification);


-- ── Hourly Rollups ───────────────────────────────────────────────
-- Spike detection baselines. Pruned after 7 days by maintenance.

CREATE TABLE IF NOT EXISTS source_trc_hourly (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source          TEXT    NOT NULL DEFAULT 'zendesk',
    trc_code        TEXT    NOT NULL,
    hour_bucket     TEXT    NOT NULL,
    count           INTEGER NOT NULL DEFAULT 0,
    avg_sentiment   REAL    DEFAULT 0.0,
    UNIQUE(source, trc_code, hour_bucket)
);


-- ── Daily Rollups ────────────────────────────────────────────────
-- Trend analysis + Guru effectiveness. Kept indefinitely (small).

CREATE TABLE IF NOT EXISTS source_trc_daily (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source          TEXT    NOT NULL DEFAULT 'zendesk',
    trc_code        TEXT    NOT NULL,
    day_bucket      TEXT    NOT NULL,
    count           INTEGER NOT NULL DEFAULT 0,
    avg_sentiment   REAL    DEFAULT 0.0,
    UNIQUE(source, trc_code, day_bucket)
);


-- ── Watchlist Rules ──────────────────────────────────────────────
-- UI-configurable alert triggers with EWMA learning.

CREATE TABLE IF NOT EXISTS watchlist_rules (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    name                  TEXT    NOT NULL,
    rule_type             TEXT    NOT NULL,
    severity              TEXT    NOT NULL DEFAULT 'watch',
    source_filter         TEXT    DEFAULT '',
    is_system             INTEGER NOT NULL DEFAULT 0,
    enabled               INTEGER NOT NULL DEFAULT 1,
    keywords              TEXT    DEFAULT '',
    keyword_mode          TEXT    DEFAULT 'any',
    entity_type           TEXT    DEFAULT '',
    entity_filter         TEXT    DEFAULT '',
    volume_threshold      INTEGER DEFAULT 0,
    volume_window_minutes INTEGER DEFAULT 60,
    ewma_confidence       REAL    DEFAULT 0.5,
    ewma_alpha            REAL    DEFAULT 0.3,
    total_fires           INTEGER DEFAULT 0,
    total_confirmed       INTEGER DEFAULT 0,
    total_dismissed       INTEGER DEFAULT 0,
    cooldown_minutes      INTEGER DEFAULT 120,
    last_fired_at         TEXT    DEFAULT '',
    created_at            TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at            TEXT    NOT NULL DEFAULT (datetime('now'))
);


-- ── Watchlist Alerts ─────────────────────────────────────────────
-- Every fired alert logged for audit + feedback loop.

CREATE TABLE IF NOT EXISTS watchlist_alerts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_id         INTEGER NOT NULL REFERENCES watchlist_rules(id),
    source          TEXT    NOT NULL DEFAULT 'zendesk',
    severity        TEXT    NOT NULL,
    title           TEXT    NOT NULL,
    summary         TEXT    DEFAULT '',
    ticket_count    INTEGER DEFAULT 0,
    ticket_ids      TEXT    DEFAULT '',
    trc_code        TEXT    DEFAULT '',
    status          TEXT    NOT NULL DEFAULT 'open',
    llm_triage      TEXT    DEFAULT '',
    llm_confidence  REAL    DEFAULT 0.0,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    resolved_at     TEXT    DEFAULT '',
    resolved_by     TEXT    DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_wa_status ON watchlist_alerts(status);
CREATE INDEX IF NOT EXISTS idx_wa_rule   ON watchlist_alerts(rule_id);


-- ── Watchlist Examples (Few-Shot Bank) ───────────────────────────
-- Sanitized past alert examples for LLM triage few-shot prompts.

CREATE TABLE IF NOT EXISTS watchlist_examples (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_id         INTEGER REFERENCES watchlist_rules(id),
    example_type    TEXT    NOT NULL,
    sanitized_text  TEXT    NOT NULL,
    outcome         TEXT    NOT NULL,
    trc_code        TEXT    DEFAULT '',
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);


-- ── Downstream Compatibility ─────────────────────────────────────
-- Add source column to conversations table for future Auto-Import.
-- DEFAULT 'csv' tags all existing batch-imported data automatically.
-- This is a safe O(1) metadata-only operation in SQLite.

ALTER TABLE conversations ADD COLUMN source TEXT DEFAULT 'csv';
