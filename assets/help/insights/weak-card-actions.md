---
id: insights-weak-card-actions
title: Turning a weak card into an update or a task
section: insights
section_title: Insights and the attention queue
section_order: 7
order: 2
status: available
features: [en_analytics, analytics, en_workbench]
summary: Two rows on the Analytics tab carry actions — a card that needs attention becomes a draft in the Workbench, and an open comment becomes a task.
last_verified: 2026-07-20
---

Analytics is not only a read-out. Two of its panels let you turn a problem
into work: a card that has fallen behind becomes an editable draft, and an
open comment becomes a task on your list.

## How it works

**From a needs-attention row.** Each row is tagged with why it surfaced and
carries an action that starts a targeted update. Using it pulls that Guru card
into the app as a draft and moves you to the Workbench with the draft loaded,
ready to edit. Nothing is written back to Guru at this point — you are working
on a local draft until you publish it.

The three reasons a card lands in that panel:

| Tag | Why it surfaced |
|---|---|
| Unverified | The card's state is "needs verification" or "stale" |
| Verification due | Its next verification date is inside 14 days |
| High traffic, stale | Heavily viewed, but not modified in about 90 days |

**From an open comment.** Each comment row carries an action that converts it
into an enablement task. The task is titled after the card the comment is on,
and the comment author and text are carried into the task summary. Once
converted, the row shows a marker instead of the action, so you can see at a
glance which comments have already been picked up.

Converting the same comment twice does not create a second task — the comment
remembers the task it produced and returns that one.

## How it should work

The targeted update should land you in the Workbench with the right draft
already open. You should not have to find the card again yourself.

Comment conversion should be a single click with no dialog, and the row should
visibly change state afterwards.

**Rows in the top-used-cards list carry no actions.** That list is ranking
only. If you want to work on a card you see there, it has to reach you through
the needs-attention panel or through the attention queue — see *The attention
queue: card health buckets*.

A targeted update needs a live Guru connection to fetch the card. Without
saved credentials the app tells you to connect Guru rather than opening an
empty draft.

## If it doesn't

**You are told to connect Guru first.** The targeted update fetches the card
live. Add your Guru credentials in Settings and try the row again.

**You land in the Workbench but no draft is loaded.** The import fetched
nothing. Confirm the card still exists in Guru — a card deleted there will
still appear in analytics until the next sync. If the card is definitely
present, flag it with the card title.

**The comment action reports it could not create a task.** The most common
cause is that the comment is no longer in the local copy, which happens if it
was resolved in Guru after your last sync. Sync again; if the comment is still
listed and still fails, flag it.

**Converting a comment produces a duplicate task.** Conversion is meant to be
repeat-safe. Two tasks from one comment is a bug worth flagging with both task
titles.

**A row stays in the needs-attention panel after you updated the card.** That
panel reads local data, so it will keep showing the old state until you sync
from Guru again. Sync, then re-check before flagging.

**Clicking a top-used-cards row does nothing.** Expected — those rows are not
clickable.
