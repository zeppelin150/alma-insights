-- Migration 041 — Asana-parity task detail: capture the requester/submitter and
-- the full task body on enablement tasks.
--
-- submitter  = the Asana task creator (created_by.name) — "who asked".
-- description = the full task body (Asana `notes`), kept distinct from `summary`
--   (an LLM-generated short summary) so the two semantics don't collide.
-- ALTER ADD COLUMN is made idempotent by the schema migrator's PRAGMA guard.
ALTER TABLE enablement_tasks ADD COLUMN submitter TEXT;
ALTER TABLE enablement_tasks ADD COLUMN description TEXT;
