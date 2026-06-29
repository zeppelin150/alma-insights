-- ─────────────────────────────────────────────────────────────────────
-- Migration 033 — card_update_provenance (content-update audit trail)
--
-- The content-update / enablement publish flow records the why/who/when of
-- every Guru card update in card_update_provenance (staged at proposal time,
-- finalized at publish; the published row also anchors the effectiveness
-- measure). The table was previously created lazily via CREATE TABLE IF NOT
-- EXISTS in src/data/content_update/provenance.py::ensure_table only when a
-- provenance function first ran. This migration registers it through the
-- standard schema path so DB-inspection tools and future migrations can rely
-- on it existing.
--
-- IF NOT EXISTS keeps this idempotent and harmless alongside ensure_table()
-- (which stays as a defensive no-op for code paths that run before migrate).
-- ─────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS card_update_provenance (
    draft_id      INTEGER PRIMARY KEY,
    card_id       TEXT,
    source_ref    TEXT DEFAULT '',
    source_title  TEXT DEFAULT '',
    summary       TEXT DEFAULT '',
    changes_json  TEXT DEFAULT '[]',
    dropped_json  TEXT DEFAULT '[]',
    issues_json   TEXT DEFAULT '[]',
    status        TEXT DEFAULT 'staged',
    approved_by   TEXT DEFAULT '',
    created_at    TEXT DEFAULT '',
    pushed_at     TEXT DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_card_update_provenance_card
    ON card_update_provenance (card_id);
