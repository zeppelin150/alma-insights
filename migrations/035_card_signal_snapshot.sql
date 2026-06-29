-- ─────────────────────────────────────────────────────────────────────
-- Migration 035 — card_signal_snapshot (Guru-native effectiveness proxy)
--
-- Replaces the now-moot ticket-volume effectiveness weld. At publish we
-- capture a BASELINE snapshot of a card's Guru-native demand signals
-- (view_count + open_comment_count, both via the GuruSignals facade); after
-- a window we re-pull a FOLLOWUP and report views_delta / comments_resolved.
-- FULLY DECOUPLED: nothing here is derived from a ticket / RCM / warehouse
-- table — the PHI boundary is absolute.
--
-- The table is also created lazily via ensure_table() in
-- src/data/content_update/effectiveness_proxy.py for code paths that run
-- before migrate; this migration registers it through the standard schema
-- path. IF NOT EXISTS keeps it idempotent alongside that defensive no-op.
-- ─────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS card_signal_snapshot (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id            TEXT NOT NULL,
    draft_id           INTEGER,
    view_count         INTEGER DEFAULT 0,
    open_comment_count INTEGER DEFAULT 0,
    captured_at        TEXT DEFAULT '',
    phase              TEXT DEFAULT 'baseline'   -- 'baseline' | 'followup'
);

CREATE INDEX IF NOT EXISTS idx_card_signal_snapshot_card
    ON card_signal_snapshot (card_id);
