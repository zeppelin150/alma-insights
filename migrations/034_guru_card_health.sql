-- ─────────────────────────────────────────────────────────────────────
-- Migration 034 — guru_card_health (enablement card-health score)
--
-- The enablement card-health score (Milestone B) persists each card's
-- 0..1 health score, its worst-standing bucket, and the per-signal
-- component + raw-signal breakdown that produced it. Every value is
-- derived FULLY DECOUPLED from the ticket / RCM / product warehouse:
-- the Guru live API (via GuruSignals) + enablement-local tables only.
--
-- Mirrors src/data/enablement_health/store.py::ensure_table (lazy
-- CREATE TABLE IF NOT EXISTS) so DB-inspection tools and future
-- migrations can rely on the table existing through the schema path.
--
-- Idempotent: CREATE TABLE/INDEX IF NOT EXISTS only.
-- ─────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS guru_card_health (
    card_id         TEXT PRIMARY KEY,
    title           TEXT DEFAULT '',
    score           REAL DEFAULT 0,
    bucket          TEXT DEFAULT '',
    components_json TEXT DEFAULT '{}',
    signals_json    TEXT DEFAULT '{}',
    computed_at     TEXT DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_guru_card_health_bucket
    ON guru_card_health (bucket);
