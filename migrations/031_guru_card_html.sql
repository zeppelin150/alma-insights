-- ─────────────────────────────────────────────────────────────────────
-- Migration 031 — Guru card draft rich-HTML representation
--
-- Guru's card `content` field is HTML. Until now publish_draft sent the
-- draft's MARKDOWN body straight into that HTML field, so formatting was
-- lost on publish. We now keep markdown canonical (preview / editors /
-- chat tools / search all read it) and add a parallel cleaned-HTML
-- representation used ONLY on the Guru publish path:
--   - rich-editor edits capture cleaned QTextEdit HTML (carries text
--     color + highlight, which markdown cannot represent);
--   - markdown-only producers (LLM generation, source editor, chat
--     revise) leave content_html NULL, and publish_draft derives it via
--     markdown_to_html(content) — the same converter the in-app preview
--     uses, so the published card matches the preview.
--
-- The schema_migrator applies ALTER TABLE ADD COLUMN idempotently
-- (PRAGMA table_info guard), so a bare ALTER is safe to re-run.
-- ─────────────────────────────────────────────────────────────────────

ALTER TABLE guru_content_drafts ADD COLUMN content_html TEXT;
