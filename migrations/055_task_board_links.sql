-- ─────────────────────────────────────────────────────────────────────
-- Migration 055 — board↔task ownership is MANY-TO-MANY. Store it that way.
--
-- Migration 054 recorded attribution in a single TEXT column,
-- enablement_tasks.board_source_id, written by the poll that CREATED the
-- row. That is a true fact and it stays (as provenance), but it cannot
-- express the shape the data actually has.
--
-- An Asana library custom field carries ONE gid org-wide, so discovery
-- resolves the SAME indicator field/value for every project an operator
-- maps. Two mapped boards therefore routinely poll the SAME multi-homed
-- task: one poll creates the row, the other RECONCILES it (matched on
-- source_ref), advances its own modified_since cursor and takes its own
-- events token. Co-ownership is observed at poll time — and with a single
-- column there was nowhere to put it, so it was discarded.
--
-- The consequence was the second data-loss report of the same class:
-- ownership fell to whichever board sorted first (get_asana_config's
-- ORDER BY source_id), the Settings card showed the task under that board
-- only, and removing it DELETED the row — scratchpad, subtask state,
-- extras and all — while the other board was still mapped, still enabled
-- and still tracking the task in Asana. Not recoverable by re-polling:
-- the surviving board's cursor is already past that task.
--
-- task_board_links is the AUTHORITY for attribution from here on. One row
-- per (task, board that demonstrably tracks it), written by BOTH poll
-- sites in asana_monitor (_create_task_from_asana and
-- _reconcile_existing_task). Nothing derives attribution from
-- board_source_id or from source_url (the permalink names the task's HOME
-- project, which for a multi-homed task is not the importing board).
--
-- Both asymmetries are now expressed over links:
--   DELETION FAILS CLOSED — a task is deleted by a board removal only when
--   EVERY link pointing at it belongs to the board being removed. Rows with
--   no link at all (pre-054/055, manual, demo, non-Asana) are never deleted.
--   DISPLAY FAILS OPEN — a task is hidden from the calendar only when EVERY
--   board linked to it is a configured board with calendar off. Unlinked
--   rows, rows linked to an unmapped board, and every row when no board is
--   mapped stay visible.
--
-- ON DELETE CASCADE mirrors enablement_subtasks (027) and
-- asana_task_extras (044): a link can never outlive its task.
--
-- BACKFILL: one link per existing row that already carries a non-NULL
-- board_source_id. That fact was recorded by the previous round's poll and
-- is true. Rows with NULL stay UNLINKED — no link is invented from a
-- permalink, because that is the inference both prior rounds died on.
-- first_seen_at reuses the task's created_at so the backfilled link is not
-- stamped with migration time.
--
-- Idempotent: CREATE ... IF NOT EXISTS + INSERT OR IGNORE (the PK makes a
-- re-run a no-op), so re-applying on an already-migrated database is safe.
-- ─────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS task_board_links (
    task_id         TEXT NOT NULL REFERENCES enablement_tasks(task_id) ON DELETE CASCADE,
    board_source_id TEXT NOT NULL,          -- monitor_sources.source_id ('asana:<gid>')
    first_seen_at   TEXT NOT NULL,          -- when this board was first seen tracking the task
    PRIMARY KEY (task_id, board_source_id)
);

-- Removal, board_summary counts and the co-ownership probe all look up by
-- board; the PK's leading task_id column cannot serve those.
CREATE INDEX IF NOT EXISTS idx_tbl_board ON task_board_links(board_source_id);

INSERT OR IGNORE INTO task_board_links (task_id, board_source_id, first_seen_at)
SELECT task_id, board_source_id, COALESCE(created_at, datetime('now'))
  FROM enablement_tasks
 WHERE board_source_id IS NOT NULL
   AND TRIM(board_source_id) != '';
