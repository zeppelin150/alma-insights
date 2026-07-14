-- Migration 045 — Haiku enrichment briefs (WS1-M4, renn-calendar-kb-studio).
--
-- Every Asana-sourced task gets a cached structured brief {ask, deliverable,
-- links, stakeholders, effective_date} built at intake and refreshed when the
-- task changes. brief_source_modified_at snapshots the remote_modified_at the
-- brief was built FROM (same-source staleness comparison, like
-- asana_task_extras.for_modified_at). brief_status: pending|ok|failed —
-- 'failed' still stamps the snapshot so a broken task doesn't burn a Haiku
-- call every cycle. All three columns are in enablement_tasks._UPDATABLE.
-- ALTER ADD COLUMN made idempotent by the schema migrator's PRAGMA guard.
ALTER TABLE enablement_tasks ADD COLUMN brief_json TEXT;
ALTER TABLE enablement_tasks ADD COLUMN brief_status TEXT DEFAULT 'pending';
ALTER TABLE enablement_tasks ADD COLUMN brief_source_modified_at TEXT;
