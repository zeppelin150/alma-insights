-- Migration 043 — Asana events-API diff polling (WS1-M2, renn-calendar-kb-studio).
--
-- monitor_sources.events_sync: the per-board Asana /events sync token. The
-- legacy modified_since watermark stays in `cursor` — it keeps advancing on the
-- events path too, so a 412 full-resync fallback never re-lists from an ancient
-- watermark.
--
-- enablement_tasks.remote_modified_at: the Asana-side modified_at snapshot
-- stamped on every reconcile/create — the check-and-set anchor for the WS1-M5/M6
-- write-back conflict safety. (Also added to enablement_tasks._UPDATABLE — the
-- update_task whitelist silently drops unknown fields.)
-- ALTER ADD COLUMN is made idempotent by the schema migrator's PRAGMA
-- table_info guard.
ALTER TABLE monitor_sources ADD COLUMN events_sync TEXT;
ALTER TABLE enablement_tasks ADD COLUMN remote_modified_at TEXT;
