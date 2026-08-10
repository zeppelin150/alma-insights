---
id: plan-task-panel-mirror
title: The task panel that looks like Asana
section: plan
section_title: Plan — Calendar and Tasks
section_order: 3
order: 8
status: available
features: [en_calendar, asana_writeback]
summary: Opening a task now shows an Asana-style panel — same fields, pills, description and activity feed you'd see in Asana itself, with the same four write-back actions underneath.
last_verified: 2026-08-10
---

Open a task from the Calendar or the task board and the side panel now renders
it the way Asana itself would: the title, the assignee and collaborator
avatars, the due date range, the custom-field grid with its colored value
pills, the formatted description, subtasks, attachments, and the comment feed
with its rule and status entries. The goal is recognition — a task from your
board should look like the task you know from Asana, not like a re-summary of
it.

## How it works

**Same data, new face.** The panel renders exactly the data the app already
synced — nothing extra is fetched when it opens. The *Updated … ago* stamp in
the header tells you how fresh that data is, and the refresh arrow next to it
re-fetches this one task from Asana on demand.

**The actions are unchanged — plus what the mirror adds.** Mark complete or
reopen, change the due date, post a comment, add a subtask — these are the
same four background write-back lanes described in *Adding subtasks, comments
and due dates*, with the same safeguards (local-first subtasks, conflict
protection, automatic revert if Asana rejects a completion). New with this
panel, because Asana itself has them:

- **Subtask check circles are clickable** — checking one completes that
  subtask in Asana too, with the same local-first-and-revert discipline.
- **Subtask names open the subtask** — subtasks are tasks. One that has its
  own row here opens in this same panel; one that hasn't been synced as its
  own task opens in Asana in your browser.
- **A subtask shows its parent as a breadcrumb** — the parent task's name
  sits above the title, exactly where Asana puts it, and clicking it goes
  back up. A parent tracked here reopens in this same panel; one that isn't
  opens in Asana in your browser.
- **The description is editable.** *Edit* switches to a plain-text editor
  (Markdown — bold, lists and links survive the round trip), and *Save*
  pushes it to Asana as rich text. This write is conflict-protected and
  remote-first: if Asana refuses it, nothing changes anywhere, so a save
  that looks successful is one that actually landed.

Both kinds of clickability appear only on Asana-linked content. The panel is
a different face on the same machinery, and your click is still the consent.

**Dates come from a picker.** Instead of typing `YYYY-MM-DD`, the due-date row
carries a small date picker. Only a full, changed date is sent.

**Links open deliberately.** Links in the description, the comments, and the
*Open in Asana* button open in your browser. The panel will only open a link
that the task actually carries — a link that isn't part of the task's own
content goes nowhere.

**Attachments resolve on click.** Attachment URLs are never stored. Clicking
one asks Asana for a fresh link right then, and only web links open. Slack,
Google Drive and Zendesk attachments are grouped under **Apps**, the rest
under **Attachments**.

**The feed splits people from rules.** The activity area has *Comments* and
*All activity* tabs. Rule-posted entries show with a ⚡ marker instead of an
avatar; status changes ("… completed this task") render as quiet gray rows.
Long feeds collapse their middle behind an "N more comments" row.

## How it should work

The Asana-style panel is on by default, and the previous panel remains as the
automatic fallback: if the new panel cannot be built on your machine, the app
quietly shows the classic panel instead — same data, same actions, no error to
dismiss. Nothing about the write-back lanes, their conflict protection, or
their local-first degradations changes with the new face.

If you need the classic panel deliberately, set `enablement.task_web: false`
in the settings file and restart. There is no toggle for this in the Settings
page — it exists as an escape hatch, not a mode.

## If it doesn't

**The panel looks like the old one.** That is the fallback working. It means
the new panel could not be built on this machine (usually a broken web-engine
install). Everything still works; flag it so the install can be looked at.

**The panel is blank.** Close and reopen the task once. If it stays blank,
set `enablement.task_web: false` in settings, restart, and flag it — the
classic panel will carry you in the meantime.

**A link in a comment does nothing.** The panel only opens links the task
actually carries. If a link you can see refuses to open, flag it with the
task name.

**An attachment says it failed to resolve.** Asana declined to hand out a
fresh link — usually a connection or permission problem. Check the Asana
connection in Settings and retry.

**A save says the task changed in Asana.** Someone (or something — a rule, a
comment) touched the task since it was last synced, and the conflict guard
refused to overwrite what you haven't seen. The message shows right next to
the Save button and your draft is kept. Click the **↻** in the panel header —
that pulls the fresh state and re-arms the guard — review, then Save again.

**Fields show em dashes.** An em dash is an empty field, faithfully mirrored
from Asana — fill the field in Asana and refresh the task.

**The due range shows one date.** Tasks without a start date show only the
due date, exactly as Asana does.
