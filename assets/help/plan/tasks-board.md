---
id: plan-tasks-board
title: The Tasks board and the detail panel
section: plan
section_title: Plan — Calendar and Tasks
section_order: 3
order: 3
status: partial
features: [en_tasks, en_calendar]
summary: The Tasks tab lists your work with subtasks and scratch notes, but several of its controls are not wired up — the working way to open a task is from the Calendar.
last_verified: 2026-07-20
---

The Tasks tab is the list view of the same work the Calendar plots. It reads
well as an overview. Several of its controls, however, are presentation only —
this article says which, so you do not spend ten minutes wondering why a
button does nothing.

## How it works

**The list.** One row per task: a status dot, the title, and columns for
source, due date, priority, assignee, and a subtask count. Clicking a row
expands it in place to show that task's subtasks and its scratch pad, and
clicking again collapses it. The first row starts expanded.

**Mine and All.** The scope toggle at the left of the filter bar works, and it
is the same setting the Calendar uses. Changing it here changes it there. The
fallback described in *The Calendar: month, week, and scope* applies equally:
with no identity set, Mine shows everything.

**The detail panel.** This is the full view of a task — description, brief,
comments, attachments, custom fields, and the write-back actions. **It opens
from the Calendar, not from this list.** Click a chip on the Calendar, or open
a day's "+N more" list and pick a task from there. Once open it appears in the
side panel on the right. See *Adding subtasks, comments and due dates* for
what you can do from it.

The following controls on this tab are not connected to anything:

| Control | What actually happens |
|---|---|
| The source, status, due and priority filters | Nothing — they are labels, not dropdowns |
| The search box on this tab | Nothing — it is a label, not an input |
| The new-task button | Nothing |
| The add-subtask box inside an expanded row | Adds a row on screen only; nothing is saved |
| The AI subtask-drafting button inside a row | Adds three fixed placeholder lines; no model is called |
| The Workbench button inside an expanded row | Nothing |

None of these will damage anything. They just do not do what their labels
suggest.

## How it should work

Treat this tab as a read-only overview and do the work elsewhere:

- To open a task in full, go through the Calendar.
- To add a subtask that persists and reaches Asana, use the detail panel.
- To draft a subtask checklist with the assistant, ask Renn.
- To narrow the list, use the Mine and All toggle, which is the one filter
  that is real.

The list is capped at a couple of hundred tasks. On a busy board, older tasks
will not be in the list even though they exist.

Subtask counts and due dates in the list should match what the detail panel
shows for the same task; both are read from the same records. A disagreement
between the two is worth flagging.

## If it doesn't

**A filter chip, the search box, or the new-task button does nothing when
clicked.** Expected — see the table above. This is a known gap, not a fault in
your setup, and it does not need a new bug report.

**A subtask you typed into an expanded row vanished.** Also expected. That box
does not save. Re-add it from the detail panel, where it persists and syncs.

**Clicking a row does not open a detail panel.** Expected — rows expand in
place. Open the task from the Calendar instead.

**A task you know exists is not in the list.** Check the Mine and All toggle
first, then consider the list cap. If it is neither, flag it with the task
name.

**The list and the Calendar disagree about a task's due date or subtask
count.** Worth flagging with the task title — they are supposed to be fed from
the same data.
