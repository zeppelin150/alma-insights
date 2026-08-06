-- ─────────────────────────────────────────────────────────────────────
-- Migration 056 — promote ASSIGNED Asana subtasks to first-class tasks.
--
-- Subtasks used to exist only as name+done checklist mirrors in
-- enablement_subtasks, so assigned subtask work never reached the calendar,
-- the "mine" scope or Renn. The monitor now promotes a subtask WITH an
-- assignee (any assignee — the "mine" display scope filters per-operator
-- downstream) into a real enablement_tasks row; unassigned subtasks stay
-- checklist-only, and the parent's checklist mirror is unchanged.
--
-- parent_task_ref stores the Asana gid of the subtask's PARENT task; NULL
-- for normal tasks. A row is a subtask iff parent_task_ref IS NOT NULL —
-- the single flag behind both calendars' "↳" marker, list_tasks'
-- parent_title join and Renn's is_subtask field. Board attribution of a
-- promoted row goes through task_board_links exactly like tasks (055 stays
-- the only authority; nothing is ever derived from the permalink).
--
-- Idempotent: single-line ALTER ADD COLUMN (the schema migrator's PRAGMA
-- table_info guard skips it when present) + CREATE INDEX IF NOT EXISTS.
-- The index serves the parent-title lookup (p.source='asana' AND
-- p.source_ref = t.parent_task_ref) list_tasks runs per listing, and the
-- source_ref equality probes the monitor already runs every poll.
-- ─────────────────────────────────────────────────────────────────────

ALTER TABLE enablement_tasks ADD COLUMN parent_task_ref TEXT;
CREATE INDEX IF NOT EXISTS idx_ent_source_ref ON enablement_tasks(source_ref);
