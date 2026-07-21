---
id: plan-task-writeback
title: Adding subtasks, comments and due dates
section: plan
section_title: Plan — Calendar and Tasks
section_order: 3
order: 4
status: available
features: [en_calendar, asana_writeback]
summary: The task detail panel can add a subtask, post a comment, change the due date and complete or reopen a task — each pushed back to Asana in the background.
last_verified: 2026-07-20
---

The task detail panel is the one place in the enablement pages that writes
back to Asana. Open a task from the Calendar and the panel appears in the side
panel on the right with four actions available.

## How it works

**Add a subtask.** Type into the subtask box and confirm. The subtask is saved
locally first, then created underneath the parent task in Asana. Because it is
saved locally first, a subtask is never lost even if the Asana call fails.

**Post a comment.** This row only appears for tasks that came from Asana. The
text is posted as a comment on the Asana task. It is not mirrored locally —
Asana owns comments, and you will see it next time comments are refreshed.

**Change the due date.** Type a date as `YYYY-MM-DD` and confirm. The local
due date changes and the change is pushed to Asana. Clearing the box and
confirming removes the due date.

**Complete or reopen.** The button in the header row flips the task's state.
The task is marked done locally straight away and then pushed to Asana. If
Asana rejects it, the local change is rolled back, because a task that looks
done here but is open in Asana is worse than no change at all.

All four run in the background so the app never freezes on a slow network. The
status line in the page header narrates it: a syncing message while it runs,
then either confirmation that it synced, a note that it was saved locally, or
an error. When it finishes, the task views reload and the panel reopens on the
same task with fresh data.

## How it should work

**These four actions write immediately. Your click is the consent** — there is
no confirmation card, unlike the writes Renn proposes. That difference is
deliberate: you are acting directly on a task you have open in front of you.
See *Why Renn never writes on its own* for the assistant's side of that.

Three of the four degrade gracefully instead of failing:

- **Subtasks** work on any task. If the task did not come from Asana, or Asana
  is not configured, the subtask is still saved locally and the status line
  says so.
- **Due dates** behave the same way — set locally, with a note that it did not
  reach Asana.
- **Complete and reopen** also degrade. The status change is written locally
  and reported as not synced, so the button is never inert.

Only **commenting** is refused outright. A comment on a task that did not come
from Asana has nowhere to go, so it fails rather than being stored locally.

Due dates and complete or reopen are also guarded against overwriting a
teammate. If the task moved in Asana since you last synced, the write is
blocked and you are asked to retry — that is expected, and *When Asana says
the task changed* explains it.

The scratch pad in the panel is a local note. It never leaves the app.

## If it doesn't

**The status line says the change was saved locally.** The write did not reach
Asana. Either this task did not come from Asana, or the Asana connection is
not configured. Check the connection in Settings. Your change is not lost.

**The due date does not take.** Check the format. The field expects
`YYYY-MM-DD` and is not forgiving of other formats — a date Asana cannot parse
will be refused on its end. Retype it and confirm.

**The comment row is not in the panel.** That task did not come from Asana, so
there is no Asana task to comment on.

**You marked a task complete and it flipped back.** The push to Asana failed
and the app reverted the local change on purpose rather than leave the two
sides disagreeing. Check your Asana connection, then retry. If it reverts
repeatedly with a working connection, flag it.

**The status line reports that the task changed in Asana.** Not an error. Read
*When Asana says the task changed*.

**The panel closes or loses your place after an action.** The panel reopens
itself with fresh data after every write, so a brief flicker is normal. Being
dropped onto a different task, or onto an empty panel, is not — flag that.

**An action reports success but nothing appears in Asana.** Worth flagging,
with the task name and which of the four actions you used.
