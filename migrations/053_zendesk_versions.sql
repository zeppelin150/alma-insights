-- ─────────────────────────────────────────────────────────────────────
-- Migration 053 — Zendesk version history (article versions + draft saves)
--
-- zendesk_article_versions: the SUPERSEDED mirror state, captured by
-- upsert_articles when an existing row's content_hash changes. The
-- CURRENT state is never duplicated here — it lives on the mirror row.
-- origin = the superseded row's provenance; replaced_by_origin = the
-- provenance of the write that superseded it. Capped at 50 per article,
-- oldest pruned at capture time (Python-side, zendesk_versions.py).
--
-- zendesk_draft_versions: one row per body-changing save of an article
-- draft (Renn propose / specialist edit / native editor), state AS OF
-- that save. seq is a 1-based per-draft counter; rollback_of records
-- which version a 'rollback' save restored. Capped at 50 per draft.
--
-- AUTOINCREMENT is deliberate on both PKs: pruning deletes rows, and a
-- reused rowid would break the monotonic version_id ordering that the
-- timeline, diffs, and rollback_of references depend on.
--
-- Soft refs by convention (052 precedent) — draft_id / article_id are
-- not FKs; orphan cleanup is Python-side (delete_draft / purge_mirror).
-- These tables have NO FTS mirrors and no triggers.
-- Idempotent: CREATE IF NOT EXISTS; post-hook seeds are guarded.
-- ─────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS zendesk_article_versions (
    version_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id         INTEGER NOT NULL,
    content_hash       TEXT,
    title              TEXT NOT NULL DEFAULT '',
    body_html          TEXT,
    body_text          TEXT,
    section_id         INTEGER,
    labels_json        TEXT DEFAULT '[]',
    author_name        TEXT,
    draft              INTEGER DEFAULT 0,
    outdated           INTEGER DEFAULT 0,
    position           INTEGER,
    updated_at         TEXT,                      -- superseded row's remote timestamp
    origin             TEXT NOT NULL DEFAULT 'pull',
    replaced_by_origin TEXT,
    captured_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_zdav_article
    ON zendesk_article_versions(article_id, version_id);

CREATE TABLE IF NOT EXISTS zendesk_draft_versions (
    version_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id    INTEGER NOT NULL,                 -- zendesk_article_drafts.id (soft ref)
    seq         INTEGER NOT NULL,                 -- 1-based per-draft save counter
    title       TEXT,
    body        TEXT,                             -- markdown/plain text as saved
    body_html   TEXT,                             -- html as stored at save time
    author      TEXT NOT NULL DEFAULT 'user',     -- 'renn' | 'specialist' | 'user'
    save_kind   TEXT NOT NULL DEFAULT 'save',     -- 'create' | 'save' | 'rollback'
    rollback_of INTEGER,                          -- zendesk_draft_versions.version_id restored
    created_at  TEXT NOT NULL,
    UNIQUE(draft_id, seq)
);
