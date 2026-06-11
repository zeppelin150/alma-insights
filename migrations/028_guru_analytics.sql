-- ─────────────────────────────────────────────────────────────────────
-- Migration 028 — Guru Analytics
--
-- Local store for the Guru Analytics page (2026-06 app redesign):
--
--   guru_events            — rolling window (~90 days, pruned on sync) of
--                            usage events from GET /teams/{id}/analytics.
--                            Guru events carry no id, so event_key is a
--                            sha1 of (type|user|eventDate|cardId) — the
--                            1-day overlap re-fetch dedups through it.
--   guru_team_stats        — snapshots of GET /teams/{id}/stats.
--   guru_card_verification — the verification-manager queue (cards due
--                            for verification → calendar chips).
--   guru_card_comments     — open card comments; task_id links a comment
--                            converted into an enablement task.
--   guru_sync_state        — KV: events watermark, cached team id,
--                            last_sync_at.
--
-- Aggregates (top cards, KPIs) are computed at read time in
-- src/data/guru_analytics.py. Idempotent: CREATE ... IF NOT EXISTS only.
-- ─────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS guru_events (
    event_key     TEXT PRIMARY KEY,
    event_type    TEXT NOT NULL,
    user_email    TEXT,
    event_date    TEXT NOT NULL,
    card_id       TEXT,
    properties    TEXT DEFAULT '{}',
    fetched_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_guru_events_date ON guru_events(event_date);
CREATE INDEX IF NOT EXISTS idx_guru_events_card ON guru_events(card_id);
CREATE INDEX IF NOT EXISTS idx_guru_events_type ON guru_events(event_type);

CREATE TABLE IF NOT EXISTS guru_team_stats (
    snapshot_at   TEXT PRIMARY KEY,
    stats_json    TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS guru_card_verification (
    card_id                TEXT PRIMARY KEY,
    title                  TEXT,
    collection_id          TEXT,
    collection_name        TEXT,
    verification_state     TEXT,
    verification_reason    TEXT,
    next_verification_date TEXT,
    verification_interval  TEXT,
    last_verified_at       TEXT,
    last_modified          TEXT,
    comment_count          INTEGER DEFAULT 0,
    fetched_at             TEXT
);
CREATE INDEX IF NOT EXISTS idx_guru_verif_due
    ON guru_card_verification(next_verification_date);

CREATE TABLE IF NOT EXISTS guru_card_comments (
    comment_id    TEXT PRIMARY KEY,
    card_id       TEXT NOT NULL,
    card_title    TEXT,
    author        TEXT,
    text          TEXT,
    created_at    TEXT,
    status        TEXT DEFAULT 'OPEN',
    task_id       TEXT,
    fetched_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_guru_comments_card ON guru_card_comments(card_id);
CREATE INDEX IF NOT EXISTS idx_guru_comments_status ON guru_card_comments(status);

CREATE TABLE IF NOT EXISTS guru_sync_state (
    key           TEXT PRIMARY KEY,
    value         TEXT,
    updated_at    TEXT
);
