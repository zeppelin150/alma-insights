---
id: plan-drag-reschedule
title: Dragging to reschedule
section: plan
section_title: Plan — Calendar and Tasks
section_order: 3
order: 7
status: flag-gated
features: [en_calendar, asana_writeback]
summary: Dragging a task chip to another day exists only in the React Calendar, which is behind a setting that is not enabled in the shipped configuration.
last_verified: 2026-07-20
---

**You cannot do this in the app as it ships.** Drag-to-reschedule was built
for a second, browser-rendered version of the Calendar. That version is behind
a setting, the setting is not present in the shipped configuration, and so the
original Calendar renders instead — where chips are not draggable. This
article describes the feature so you know what it is and what to expect if it
is ever turned on.

## How it works

There are two Calendar implementations. Which one you get is decided by a
single setting, `enablement.web_tabs`, which can be off, can enable the
browser-rendered Calendar alone, or can enable both the Calendar and the
Workbench. It defaults to off, and it is not written into the shipped
settings file at all, so off is what you have. Everything described in *The
Calendar: month, week, and scope* is the version you are actually using.

In the browser-rendered version, dragging a chip onto a different day does not
move anything by itself. It only asks. The sequence is:

1. The page tells the app which task was dropped and on what date. That is all
   it can do — the page has no ability to write.
2. The app checks the request against its own records. A chip it does not
   recognise, a malformed date, a drop back onto the same day, or a Guru
   card-due chip is dropped silently.
3. Only one move can be in flight at a time, so one drag can never become two
   writes.
4. **A confirmation dialog appears naming the task and the new date, and
   defaults to No.** It is a real application dialog, not part of the web page,
   so nothing running in the page can dismiss or click it.
5. Only after you say yes does the app run the change, through exactly the
   same guarded path the detail panel's due-date field uses.

The Calendar then shows a short message saying the move was cancelled or that
it is syncing.

## How it should work

The confirmation is the point. A drag is easy to do by accident, and the
target of that accident is a shared Asana board, so the design makes the
mouse gesture a proposal and your explicit yes the actual instruction.

Because the write runs through the same path as typing a date into the detail
panel, everything in *Adding subtasks, comments and due dates* and *When Asana
says the task changed* would apply — including the guard that blocks the move
if the task changed in Asana underneath you.

Guru card-due chips would not be draggable. Their dates come from card health,
not from anything you can move.

Until the setting is enabled, the way to change a due date is the detail
panel: open the task from the Calendar and set the date there. That path is
fully working today.

## If it doesn't

**Chips will not drag.** Expected. This is the shipped configuration, not a
fault in your setup and not something to report. Use the detail panel.

**You were told this feature exists.** It does exist in the codebase, and it
is gated off. Both things are true. Whether to enable it is an administrator's
decision, not something you can change from inside the app.

If the setting has been enabled for you and drag still misbehaves:

**A drop does nothing at all, with no dialog.** Some drops are ignored on
purpose — same day, a card-due chip, or a second drag while one is already
waiting. If a normal task dropped on a clearly different day produces no
dialog, flag it.

**A task moved without a confirmation dialog.** Flag this immediately and with
detail. A silent write is the exact failure the design exists to prevent.

**You said yes and the message said the move did not start.** The change was
never dispatched. Check the status line in the page header, then set the date
from the detail panel instead.
