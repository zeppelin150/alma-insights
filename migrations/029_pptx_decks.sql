-- ─────────────────────────────────────────────────────────────────────
-- Migration 029 — PowerPoint decks (enablement PowerPoint tab)
--
-- A deck is staged as a draft (an editable JSON slide outline) and
-- "exported" to a real .pptx file via python-pptx — same draft→review→
-- ship lifecycle as guru_content_drafts.
--
--   pptx_decks.outline_json = {"title": str, "slides": [{"title": str,
--                              "bullets": [str, ...]}, ...]}
--
-- Idempotent: CREATE TABLE/INDEX IF NOT EXISTS only.
-- ─────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS pptx_decks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    title         TEXT NOT NULL,
    source_ref    TEXT,                          -- doc_id | 'topic:<text>'
    outline_json  TEXT NOT NULL DEFAULT '{}',
    status        TEXT NOT NULL DEFAULT 'pending',  -- 'pending' | 'exported'
    file_path     TEXT,                          -- last exported .pptx path
    slide_count   INTEGER DEFAULT 0,
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT,
    exported_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_pptx_status ON pptx_decks(status);
CREATE INDEX IF NOT EXISTS idx_pptx_updated ON pptx_decks(updated_at);
