-- ─────────────────────────────────────────────────────────────────────
-- Migration 038 — Chat action requests (M0: Renn action channel)
--
-- A dedicated channel for UI actions the agent asks the operator to take
-- (open a picker, connect Google). It is deliberately SEPARATE from
-- chat_tool_executions because that table has a TEXT PRIMARY KEY, so its
-- rowid is NOT monotonic with insertion order — two pollers on the same
-- cursor interleave and silently drop envelopes. AUTOINCREMENT + a
-- ``consumed`` flag makes the cursor irrelevant, makes the atomic
-- claim-on-emit single-winner trivial, and decouples the action stream
-- from the tool-call timeline entirely.
--
--   request_id    server-minted uuid4 (UNIQUE) — client/LLM-supplied ids
--                 are never trusted; the resolver mints its own.
--   type          drive_folder_picker | asana_board_picker |
--                 guru_publish_picker | google_connect
--   payload_json  TINY + non-PHI — control keys only (e.g. {have_client:…});
--                 never folder/board/collection names or listing data.
--   consumed      0 until a poller atomically claims it (UPDATE … WHERE
--                 consumed=0); exactly one emit wins.
-- ─────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS chat_action_requests (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id    TEXT NOT NULL,
    request_id    TEXT NOT NULL UNIQUE,            -- server-minted uuid4
    type          TEXT NOT NULL,                   -- picker / connect type
    payload_json  TEXT,                            -- tiny, non-PHI, control-only
    consumed      INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_chat_action_requests_session
    ON chat_action_requests(session_id, consumed);
