-- ─────────────────────────────────────────────────────────────────────
-- Migration 037 — Draft approval gate (M5: in-thread sign-off)
--
-- Closes a real hole: today the `push_guru_draft` tool publishes a card draft
-- to Guru with NO human sign-off. These columns make approval a hard,
-- DB-recorded precondition for publishing:
--   require_approval   1 = must be approved before it can be pushed (default ON
--                      for every draft, new and existing).
--   approved_at        set when a human signs off in the Agent review panel;
--                      while NULL + require_approval=1 the push is refused.
--   pending_push_json  set when a push was attempted but blocked — stores the
--                      intended {collection_id, folder_id} so an in-UI approval
--                      can complete the publish. Non-NULL = awaiting sign-off.
-- (`approved_by` already exists from migration 003 and records the approver.)
-- ─────────────────────────────────────────────────────────────────────

ALTER TABLE guru_content_drafts ADD COLUMN require_approval INTEGER DEFAULT 1;
ALTER TABLE guru_content_drafts ADD COLUMN approved_at TEXT;
ALTER TABLE guru_content_drafts ADD COLUMN pending_push_json TEXT;
