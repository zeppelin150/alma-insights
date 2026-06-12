-- ─────────────────────────────────────────────────────────────────────
-- Migration 030 — Zendesk Help Center content (enablement Zendesk tab)
--
-- Sync the live Help Center articles + macros into a local cache, and
-- stage AI-drafted new/updated content with a draft→review→human-gated-
-- push lifecycle (mirrors guru_content_drafts). A draft links back to its
-- live id (article_id / macro_id) so publish UPDATES rather than creates.
--
-- Idempotent: CREATE TABLE/INDEX IF NOT EXISTS only.
-- ─────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS zendesk_articles (
    article_id    INTEGER PRIMARY KEY,
    title         TEXT,
    body          TEXT,
    locale        TEXT DEFAULT 'en-us',
    section_id    INTEGER,
    html_url      TEXT,
    updated_at    TEXT,
    fetched_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_zda_section ON zendesk_articles(section_id);

CREATE TABLE IF NOT EXISTS zendesk_macros (
    macro_id      INTEGER PRIMARY KEY,
    name          TEXT,
    description   TEXT,
    actions_json  TEXT,
    active        INTEGER DEFAULT 1,
    updated_at    TEXT,
    fetched_at    TEXT
);

CREATE TABLE IF NOT EXISTS zendesk_article_drafts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id    INTEGER,                       -- live id when updating
    section_id    INTEGER,                       -- target section when creating
    title         TEXT NOT NULL,
    body          TEXT NOT NULL DEFAULT '',      -- markdown; → HTML on push
    locale        TEXT DEFAULT 'en-us',
    source_ref    TEXT,                          -- doc_id origin
    status        TEXT NOT NULL DEFAULT 'pending',  -- 'pending' | 'pushed'
    pushed_at     TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_zdad_status ON zendesk_article_drafts(status);
CREATE INDEX IF NOT EXISTS idx_zdad_article ON zendesk_article_drafts(article_id);

CREATE TABLE IF NOT EXISTS zendesk_macro_drafts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    macro_id      INTEGER,                       -- live id when updating
    name          TEXT NOT NULL,
    description   TEXT,
    actions_json  TEXT NOT NULL DEFAULT '[]',
    source_ref    TEXT,
    status        TEXT NOT NULL DEFAULT 'pending',  -- 'pending' | 'pushed'
    pushed_at     TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_zdmd_status ON zendesk_macro_drafts(status);
CREATE INDEX IF NOT EXISTS idx_zdmd_macro ON zendesk_macro_drafts(macro_id);
