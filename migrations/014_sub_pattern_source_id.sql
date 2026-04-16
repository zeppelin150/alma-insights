-- Migration 014: Add source_id to sub_patterns and nlp_scan_runs
-- Enables per-source friction filtering in Guru and scan-to-source linkage.

-- Safe: ALTER TABLE ADD COLUMN is a no-op if column already exists in SQLite
-- (will raise error caught by migrator's try/except).
ALTER TABLE sub_patterns ADD COLUMN source_id TEXT DEFAULT NULL;
ALTER TABLE nlp_scan_runs ADD COLUMN source_id TEXT DEFAULT NULL;
