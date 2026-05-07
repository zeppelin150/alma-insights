-- ─────────────────────────────────────────────────────────────────────
-- Migration 026 — Report findings + pipeline metadata
--
-- Extends `analysis_reports` with structured-output columns introduced
-- by the AI Reports R1 rebuild (see docs/AI_REPORTS.md, plan rebuild
-- 2026-05-06):
--   findings_json     — JSON array of Finding rows (parser output)
--   pipeline_kind     — 'single_pass' | 'multi_bridge'
--   specialist_count  — 0 for single_pass, N for multi_bridge
--   accuracy_score    — 0.0-1.0 composite from report_grounding
--   cost_usd          — totaled bridge cost for the run
--
-- Idempotent: every ALTER guarded so re-running on a populated DB is
-- a no-op (SQLite doesn't support ADD COLUMN IF NOT EXISTS pre-3.35;
-- the schema migrator's ADD-COLUMN helper handles the duplicate-column
-- error path — see src/updater/schema_migrator.py).
-- ─────────────────────────────────────────────────────────────────────

ALTER TABLE analysis_reports ADD COLUMN findings_json    TEXT DEFAULT '';
ALTER TABLE analysis_reports ADD COLUMN pipeline_kind    TEXT DEFAULT 'single_pass';
ALTER TABLE analysis_reports ADD COLUMN specialist_count INTEGER DEFAULT 0;
ALTER TABLE analysis_reports ADD COLUMN accuracy_score   REAL DEFAULT NULL;
ALTER TABLE analysis_reports ADD COLUMN cost_usd         REAL DEFAULT 0.0;

CREATE INDEX IF NOT EXISTS idx_ar_pipeline_kind ON analysis_reports(pipeline_kind);
CREATE INDEX IF NOT EXISTS idx_ar_accuracy      ON analysis_reports(accuracy_score);
