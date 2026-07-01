-- ─────────────────────────────────────────────────────────────────────
-- Migration 039 — chat_action_requests.resolved (M3: the consumed-vs-resolved gap)
--
-- M0's ``consumed`` flag flips the instant the action poll EMITS the action
-- (i.e. the picker OPENS). But the human-gate (invariant 5) must block a
-- privileged write (set_drive_folder/set_asana_board/…) while a picker is
-- OPEN BUT NOT YET PICKED — a window ``consumed`` does not capture (it is
-- already 1 once emitted).
--
-- ``resolved`` adds that DB-visible state. A row's lifecycle is:
--     created   consumed=0 resolved=0   (minted by a resolver tool)
--   → emitted   consumed=1 resolved=0   (picker open in the app)
--   → resolved  consumed=1 resolved=1   (operator picked / cancelled-via-resolve)
--
-- has_pending_action() now keys off ``resolved=0`` (covers created AND
-- emitted-but-unresolved) so the gate stays closed for the whole open window.
-- mark_resolved() does a single-winner ``UPDATE … WHERE resolved=0`` so a
-- double-pick resolves exactly once.
--
-- Auto-discovered by SchemaMigrator's ``*.sql`` glob — same as 038.
-- ─────────────────────────────────────────────────────────────────────

ALTER TABLE chat_action_requests ADD COLUMN resolved INTEGER NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_chat_action_requests_session_resolved
    ON chat_action_requests(session_id, resolved);
