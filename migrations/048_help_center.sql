-- Migration 048 — in-app Help Center for the enablement side.
--
-- Articles are AUTHORED as markdown + YAML frontmatter under assets/help/ and
-- LOADED into these tables. assets/ is the source of truth; these rows are a
-- rebuildable index (the loader upserts on content_hash change), so shipping
-- new help content is a file replacement with no code change.
--
-- One corpus, two consumers: the Help tab browses these rows and Renn's
-- help_search tool retrieves them. Never fork the corpus per renderer.
--
-- Deliberately NOT kb_cards: that table is a rebuildable mirror of the
-- operator's Drive EC folder and is subject to reconciliation, quarantine and
-- source_missing status. Bundled help content has none of those semantics.
--
-- `status` is the honesty mechanism. Several documented features are stubs,
-- flag-gated off, or unreachable; the UI renders the status as a badge + a
-- banner and help_search returns it, so Renn states the limitation instead of
-- confidently describing something that does not run.
--
-- All writes: plain execute+commit, never atomic() (the loader runs at
-- startup and from the MCP subprocess). Idempotent (IF NOT EXISTS).

CREATE TABLE IF NOT EXISTS help_articles (
    article_id    TEXT PRIMARY KEY,            -- stable slug, in the filename
    title         TEXT NOT NULL DEFAULT '',
    section       TEXT NOT NULL DEFAULT '',    -- section slug
    section_title TEXT NOT NULL DEFAULT '',    -- display name of the section
    section_order INTEGER NOT NULL DEFAULT 0,  -- section position in the ToC
    article_order INTEGER NOT NULL DEFAULT 0,  -- position within the section
    status        TEXT NOT NULL DEFAULT 'available',
                  -- available | partial | flag-gated | not-available
    applies_to    TEXT NOT NULL DEFAULT 'enablement',  -- enablement | product | both
    summary       TEXT NOT NULL DEFAULT '',
    body          TEXT NOT NULL DEFAULT '',    -- markdown, frontmatter stripped
    features_json TEXT NOT NULL DEFAULT '[]',  -- tab keys / tool names documented
    last_verified TEXT NOT NULL DEFAULT '',
    content_hash  TEXT NOT NULL DEFAULT '',    -- skip-unchanged guard
    loaded_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_help_articles_section
    ON help_articles(section_order, article_order);
CREATE INDEX IF NOT EXISTS idx_help_articles_status
    ON help_articles(status);

-- FTS over the article fields (contentless — kept in sync by triggers).
-- Tokenizer locked NOW, before data exists, matching 046_kb.sql.
CREATE VIRTUAL TABLE IF NOT EXISTS help_articles_fts USING fts5(
    title, summary, body, features,
    content='',
    tokenize='unicode61 remove_diacritics 2'
);
-- NOTE on the delete form: this is a CONTENTLESS fts5 table (content=''), so
-- the 'delete' command must be given the ORIGINAL column values — the
-- rowid-only form used by some older tables in this schema does NOT remove the
-- indexed terms, which leaves a corrected article's OLD text searchable.
-- Caught by tests/test_help_store.py::test_fts_updated_not_duplicated_on_change;
-- without it, Renn could quote help copy that has since been fixed.
--
-- Triggers are dropped and recreated (rather than CREATE IF NOT EXISTS) so
-- re-running this migration repairs an earlier definition in place.
DROP TRIGGER IF EXISTS help_articles_ai;
DROP TRIGGER IF EXISTS help_articles_ad;
DROP TRIGGER IF EXISTS help_articles_au;

CREATE TRIGGER help_articles_ai AFTER INSERT ON help_articles BEGIN
    INSERT INTO help_articles_fts(rowid, title, summary, body, features)
    VALUES (new.rowid, new.title, new.summary, new.body, new.features_json);
END;
CREATE TRIGGER help_articles_ad AFTER DELETE ON help_articles BEGIN
    INSERT INTO help_articles_fts(help_articles_fts, rowid,
                                  title, summary, body, features)
    VALUES ('delete', old.rowid, old.title, old.summary, old.body,
            old.features_json);
END;
CREATE TRIGGER help_articles_au AFTER UPDATE ON help_articles BEGIN
    INSERT INTO help_articles_fts(help_articles_fts, rowid,
                                  title, summary, body, features)
    VALUES ('delete', old.rowid, old.title, old.summary, old.body,
            old.features_json);
    INSERT INTO help_articles_fts(rowid, title, summary, body, features)
    VALUES (new.rowid, new.title, new.summary, new.body, new.features_json);
END;
