-- Migration 040 — capture the Asana assignee GID for "only mine" task filtering.
--
-- enablement_tasks.assignee holds the display NAME today (asana_monitor writes
-- task.assignee.name). A stable GID lets the "my tasks" filter match
-- unambiguously across name collisions / renames; the name index keeps the
-- legacy/manual name fallback fast. ALTER ADD COLUMN is made idempotent by the
-- schema migrator's PRAGMA table_info guard.
ALTER TABLE enablement_tasks ADD COLUMN assignee_gid TEXT;
CREATE INDEX IF NOT EXISTS idx_ent_assignee_gid ON enablement_tasks(assignee_gid);
CREATE INDEX IF NOT EXISTS idx_ent_assignee ON enablement_tasks(assignee);
