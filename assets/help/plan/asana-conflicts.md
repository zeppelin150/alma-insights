---
id: plan-asana-conflicts
title: When Asana says the task changed
section: plan
section_title: Plan — Calendar and Tasks
section_order: 3
order: 5
status: partial
features: [asana_writeback]
summary: The message telling you the task changed in Asana is a safety guard doing its job, not a failure; to see the current state open the task in Asana, and wait for the next background sync before you retry.
last_verified: 2026-07-20
---

Sometimes you change a due date or complete a task and the status line says
the task changed in Asana, that it has been refreshed, and asks you to retry.
Nothing broke. Something in Asana moved underneath you and the app refused to
paper over it.

## How it works

The app remembers when each task was last modified in Asana. Before it pushes
a change that could overwrite someone else's work, it re-reads that timestamp
from Asana and compares.

If they match, nobody has touched the task since you last synced and the write
goes ahead. If they differ, the write is abandoned before anything is sent.
Your local copy is left untouched, the task views reload, and the panel reopens
on the same task.

One thing to be clear about: that reload reads your local copy, not Asana. A
conflict tells the app that Asana has moved on, but it does not pull Asana's new
values down — so the panel reopens showing the same values you were already
looking at, not whatever a colleague just changed. To see the current Asana
state, open the task in Asana, or wait for the background sync (about a minute)
to bring it down.

The check applies to the two actions that can destroy someone else's change:

| Action | Guarded |
|---|---|
| Change the due date | Yes |
| Complete or reopen | Yes |
| Add a subtask | No — it only adds |
| Post a comment | No — it only adds |

Subtasks and comments are skipped on purpose. They add to a task rather than
replace anything on it, so there is nothing to clobber and no reason to make
you retry.

## How it should work

Read the message as "look before you write". Because the reload does not fetch
Asana's new values, the reliable way to see what actually changed is to open
the task in Asana. Often you will not want your change any more — if a colleague
already moved the date to next Tuesday, your Friday is stale.

If you do still want your change, you have to wait before retrying. A conflict
does not advance the app's record of when the task last changed, so an immediate
second attempt compares against the same stale timestamp and conflicts again.
The background sync refreshes that record roughly once a minute; after it has
run, your retry compares against the fresh timestamp and goes through.

You should not keep seeing this message for the same task once the sync has
caught up, unless something really is changing it — an automation, or a teammate
working at the same time.

The guard is protection, not a gatekeeper. If the app cannot reach Asana to
make the comparison at all, it proceeds rather than blocking you. It is
narrow, not paranoid.

A very small race remains: two people writing within the same second can still
overlap. The background sync notices and settles the task to whatever Asana
holds, usually within a minute.

## If it doesn't

**The message appears every single time, on every task.** That is not normal
concurrency. If nobody else is working the board and no automation is running
against it, flag it — with the task name and roughly how often it happens.

**You retry straight away and it conflicts again.** Expected. A retry only
succeeds after the background sync has refreshed the app's timestamp for the
task, which happens about once a minute — an immediate retry always compares
against the same stale timestamp. Wait a minute, then retry. If it still
conflicts after that, someone or something really is editing the task in real
time; open it in Asana to see who, and settle it there.

**The message appears when adding a subtask or posting a comment.** Those two
are not supposed to be guarded, so seeing it there is a bug. Flag it with the
task name and which action you used.

**After the conflict the panel shows the same values as before.** Expected, not
a bug. The reload reads your local copy, which the conflict did not change; it
does not pull Asana's latest values down. If you need to see what Asana actually
holds now, open the task in Asana, or wait about a minute for the background
sync to bring the current values into the app — then the panel will show them.

**Your change appears to have been applied anyway.** A conflict should mean
nothing was written. If the change lands despite the message, flag it as a
serious bug with the task name.
