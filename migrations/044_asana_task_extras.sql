-- Migration 044 — rich Asana intake side table (WS1-M3, renn-calendar-kb-studio).
--
-- Full task context (html_notes, custom fields, attachments, comment stories)
-- lives BESIDE enablement_tasks so the list/calendar query stays lean; the
-- detail panel joins lazily per task. Refresh is capped per poll (the
-- pre-mortem extras-storm fix): a row is stale when for_modified_at no longer
-- equals the task's remote_modified_at (same-source comparison — never compare
-- our clock to Asana's); story activity forces staleness by blanking
-- for_modified_at (comments never bump task modified_at).
-- html_notes is UNTRUSTED rich HTML: stored raw, rendered PlainText-only.
CREATE TABLE IF NOT EXISTS asana_task_extras (
    task_id            TEXT PRIMARY KEY REFERENCES enablement_tasks(task_id) ON DELETE CASCADE,
    html_notes         TEXT,
    custom_fields_json TEXT,
    attachments_json   TEXT,
    stories_json       TEXT,
    for_modified_at    TEXT,   -- the remote_modified_at these extras were fetched for
    fetched_at         TEXT
);
