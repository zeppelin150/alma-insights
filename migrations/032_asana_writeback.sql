-- ─────────────────────────────────────────────────────────────────────
-- Migration 032 — Asana write-back linkage
--
-- Enablement subtasks can now be pushed back to Asana as real subtasks of
-- the parent Asana task. We record the Asana subtask GID returned by the
-- create call so a local subtask is linked to its Asana counterpart (and a
-- re-sync is idempotent — a subtask that already carries a gid is skipped).
--
-- The parent task's Asana GID already lives in enablement_tasks.source_ref
-- (set by asana_monitor when source='asana'), so no task-side column is
-- needed; only the subtask link is new.
--
-- The schema_migrator applies ALTER TABLE ADD COLUMN idempotently
-- (PRAGMA table_info guard), so a bare ALTER is safe to re-run.
-- ─────────────────────────────────────────────────────────────────────

ALTER TABLE enablement_subtasks ADD COLUMN asana_subtask_gid TEXT;
