-- ─────────────────────────────────────────────────────────────────────
-- Migration 051 — Zendesk mirror (web Garden-clone tab + AI revisions)
--
-- Extends the mig-030 content cache into a full-fidelity local mirror:
-- verbatim body_html, section/category tables, Guide list metadata
-- (draft/outdated/labels/author/position), import provenance
-- (origin 'pull'|'import', source_file, content_hash), FTS5 search, and
-- a widened draft lifecycle 'pending'→'ready'→'copied' (the native tab's
-- 'pending'|'pushed' semantics are preserved unchanged — status is TEXT,
-- no CHECK constraint exists in 030, so widening is comment-level).
--
-- body_text / actions_text are PYTHON-MAINTAINED plain-text projections
-- (zendesk_store strips tags on every upsert); the FTS mirrors index
-- those columns, so rows written by paths that skip the store would
-- index NULL — all writers MUST go through zendesk_store upserts.
--
-- Idempotent: CREATE IF NOT EXISTS; single-line ALTERs (migrator guard);
-- DROP TRIGGER IF EXISTS + CREATE (repair form); delete-all back-fill.
-- ─────────────────────────────────────────────────────────────────────

-- Articles: verbatim HTML + Guide metadata + provenance
ALTER TABLE zendesk_articles ADD COLUMN body_html TEXT;
ALTER TABLE zendesk_articles ADD COLUMN body_text TEXT;
ALTER TABLE zendesk_articles ADD COLUMN draft INTEGER DEFAULT 0;
ALTER TABLE zendesk_articles ADD COLUMN outdated INTEGER DEFAULT 0;
ALTER TABLE zendesk_articles ADD COLUMN labels_json TEXT DEFAULT '[]';
ALTER TABLE zendesk_articles ADD COLUMN author_name TEXT;
ALTER TABLE zendesk_articles ADD COLUMN position INTEGER;
ALTER TABLE zendesk_articles ADD COLUMN created_at_remote TEXT;
ALTER TABLE zendesk_articles ADD COLUMN content_hash TEXT;
ALTER TABLE zendesk_articles ADD COLUMN origin TEXT DEFAULT 'pull';
ALTER TABLE zendesk_articles ADD COLUMN source_file TEXT;
ALTER TABLE zendesk_articles ADD COLUMN raw_json TEXT;
CREATE INDEX IF NOT EXISTS idx_zda_origin ON zendesk_articles(origin);
CREATE INDEX IF NOT EXISTS idx_zda_hash ON zendesk_articles(content_hash);

-- Macros: plain-text projection of actions + provenance
ALTER TABLE zendesk_macros ADD COLUMN actions_text TEXT;
ALTER TABLE zendesk_macros ADD COLUMN content_hash TEXT;
ALTER TABLE zendesk_macros ADD COLUMN origin TEXT DEFAULT 'pull';
ALTER TABLE zendesk_macros ADD COLUMN source_file TEXT;
ALTER TABLE zendesk_macros ADD COLUMN raw_json TEXT;
CREATE INDEX IF NOT EXISTS idx_zdm_origin ON zendesk_macros(origin);

-- Drafts: copy-exact HTML, AI provenance, copied lifecycle timestamp.
-- status vocabulary now: 'pending' | 'ready' | 'copied' | 'pushed'.
ALTER TABLE zendesk_article_drafts ADD COLUMN body_html TEXT;
ALTER TABLE zendesk_article_drafts ADD COLUMN rationale TEXT;
ALTER TABLE zendesk_article_drafts ADD COLUMN sources_json TEXT DEFAULT '[]';
ALTER TABLE zendesk_article_drafts ADD COLUMN copied_at TEXT;
ALTER TABLE zendesk_macro_drafts ADD COLUMN reply_html TEXT;
ALTER TABLE zendesk_macro_drafts ADD COLUMN rationale TEXT;
ALTER TABLE zendesk_macro_drafts ADD COLUMN sources_json TEXT DEFAULT '[]';
ALTER TABLE zendesk_macro_drafts ADD COLUMN copied_at TEXT;

-- Guide taxonomy (never synced before; needed for the Manage-articles clone)
CREATE TABLE IF NOT EXISTS zendesk_categories (
    category_id   INTEGER PRIMARY KEY,
    name          TEXT NOT NULL DEFAULT '',
    description   TEXT,
    position      INTEGER,
    origin        TEXT NOT NULL DEFAULT 'pull',
    fetched_at    TEXT
);

CREATE TABLE IF NOT EXISTS zendesk_sections (
    section_id    INTEGER PRIMARY KEY,
    category_id   INTEGER,
    name          TEXT NOT NULL DEFAULT '',
    description   TEXT,
    position      INTEGER,
    origin        TEXT NOT NULL DEFAULT 'pull',
    fetched_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_zds_category ON zendesk_sections(category_id);

-- FTS mirrors (048/050 discipline: contentless, unicode61, delete triggers
-- pass ORIGINAL values, delete-all back-fill so re-application is safe)
CREATE VIRTUAL TABLE IF NOT EXISTS zendesk_articles_fts USING fts5(
    title, body_text, labels_json,
    content='',
    tokenize='unicode61 remove_diacritics 2'
);

DROP TRIGGER IF EXISTS zendesk_articles_ai;
DROP TRIGGER IF EXISTS zendesk_articles_ad;
DROP TRIGGER IF EXISTS zendesk_articles_au;

CREATE TRIGGER zendesk_articles_ai AFTER INSERT ON zendesk_articles BEGIN
    INSERT INTO zendesk_articles_fts(rowid, title, body_text, labels_json)
    VALUES (new.rowid, new.title, new.body_text, new.labels_json);
END;
CREATE TRIGGER zendesk_articles_ad AFTER DELETE ON zendesk_articles BEGIN
    INSERT INTO zendesk_articles_fts(zendesk_articles_fts, rowid, title, body_text, labels_json)
    VALUES ('delete', old.rowid, old.title, old.body_text, old.labels_json);
END;
CREATE TRIGGER zendesk_articles_au AFTER UPDATE ON zendesk_articles BEGIN
    INSERT INTO zendesk_articles_fts(zendesk_articles_fts, rowid, title, body_text, labels_json)
    VALUES ('delete', old.rowid, old.title, old.body_text, old.labels_json);
    INSERT INTO zendesk_articles_fts(rowid, title, body_text, labels_json)
    VALUES (new.rowid, new.title, new.body_text, new.labels_json);
END;

INSERT INTO zendesk_articles_fts(zendesk_articles_fts) VALUES ('delete-all');
INSERT INTO zendesk_articles_fts(rowid, title, body_text, labels_json)
    SELECT rowid, title, COALESCE(body_text, ''), COALESCE(labels_json, '[]')
    FROM zendesk_articles;

CREATE VIRTUAL TABLE IF NOT EXISTS zendesk_macros_fts USING fts5(
    name, description, actions_text,
    content='',
    tokenize='unicode61 remove_diacritics 2'
);

DROP TRIGGER IF EXISTS zendesk_macros_ai;
DROP TRIGGER IF EXISTS zendesk_macros_ad;
DROP TRIGGER IF EXISTS zendesk_macros_au;

CREATE TRIGGER zendesk_macros_ai AFTER INSERT ON zendesk_macros BEGIN
    INSERT INTO zendesk_macros_fts(rowid, name, description, actions_text)
    VALUES (new.rowid, new.name, new.description, new.actions_text);
END;
CREATE TRIGGER zendesk_macros_ad AFTER DELETE ON zendesk_macros BEGIN
    INSERT INTO zendesk_macros_fts(zendesk_macros_fts, rowid, name, description, actions_text)
    VALUES ('delete', old.rowid, old.name, old.description, old.actions_text);
END;
CREATE TRIGGER zendesk_macros_au AFTER UPDATE ON zendesk_macros BEGIN
    INSERT INTO zendesk_macros_fts(zendesk_macros_fts, rowid, name, description, actions_text)
    VALUES ('delete', old.rowid, old.name, old.description, old.actions_text);
    INSERT INTO zendesk_macros_fts(rowid, name, description, actions_text)
    VALUES (new.rowid, new.name, new.description, new.actions_text);
END;

INSERT INTO zendesk_macros_fts(zendesk_macros_fts) VALUES ('delete-all');
INSERT INTO zendesk_macros_fts(rowid, name, description, actions_text)
    SELECT rowid, name, COALESCE(description, ''), COALESCE(actions_text, '')
    FROM zendesk_macros;
