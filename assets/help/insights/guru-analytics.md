---
id: insights-guru-analytics
title: "Guru Analytics: filters, refresh and the expand overlay"
section: insights
section_title: Insights and the attention queue
section_order: 7
order: 1
status: available
features: [en_analytics, analytics]
summary: The Analytics tab shows how your Guru library is being used and which cards are falling behind, pulled from Guru on demand.
last_verified: 2026-07-20
---

Analytics answers two questions: which cards people actually use, and which
cards are drifting out of date. Everything on the page is read from a local
copy of Guru data, so it is fast — and it is only as current as your last
sync.

## How it works

A pill at the top of the page tells you when the data was last pulled. Before
you have ever synced it reads "Never synced" and every number on the page is
zero. The button on the right pulls fresh data from Guru; it runs in the
background, so the page stays usable while it works. When the pull finishes the
status line shows the outcome: "Guru analytics synced." on success, or a short
"Analytics sync issue" message naming what went wrong. The panels repopulate a
moment before that, so the status line is the last thing to settle — it holds
the result of the sync rather than the page's usual task-and-draft count.

Four figures sit across the top:

| Figure | What it counts |
|---|---|
| In verification queue | Every card the verification pull returned, whether or not it carries a verification state |
| Due in 14 days | Cards due for verification within the next 14 days — or already past due |
| Open comments | Card comments still open |
| Needs verification | Cards in a "needs verification" or "stale" state |

Below that, four panels:

- **Top used cards** — ranked by views in the selected window, each with a
  proportional usage bar, a collection badge and a view count. Twelve rows
  here, twenty in the expanded view.
- **Verification** — a donut of verification states with a counted legend.
- **Needs attention** — up to six cards, each tagged with why it surfaced
  (unverified, verification due, or high traffic but stale) and its due date.
- **Open card comments** — up to eight, newest first, with the author and the
  start of the comment.

Three dropdowns filter the page: a time window of 7, 30 or 90 days (30 by
default), a collection, and a card type. The expand control opens the page
full-window with a large donut and a longer top-cards list; you leave it with
the collapse control or the Escape key.

## How it should work

Sync first, then read. A freshly-installed app has no Guru data at all, so
zeroes on first open are expected rather than a fault.

**The time window re-shapes two panels; collection and card type re-shape
one.** The time window changes which cards rank in the top-used-cards list, and
it also re-shapes the needs-attention panel — a card only counts as "high
traffic, stale" if it was viewed inside the window, so widening from 7 to 90
days can surface more stale cards there. Collection and card type re-shape only
the top-used-cards list. None of the three touch the four figures at the top,
the verification donut, or the open-comments panel — those are whole-library
counts and stay put when you change a filter.

Two panels offer a per-row action: needs-attention starts a targeted update,
and each open card comment carries a Create task button. See *Turning a weak
card into an update or a task*.

## If it doesn't

**Every number is zero and the pill says the data has never been synced.**
Expected on a new install. Pull from Guru and the page fills in.

**The sync reports that Guru is not connected.** The pull needs saved Guru
credentials. Add them in Settings, under the Guru connection, then sync again.

**Top used cards says there are no usage events.** Guru's analytics feed had
nothing in your window. Widen the window to 90 days before assuming it is
broken — a quiet 7-day window looks identical to a failed sync here.

**Changing the time window moves the needs-attention panel as well as the
top-cards list.** Expected — the "high traffic, stale" rows depend on the
window. Changing collection or card type moves only the top-cards list. The
four top figures, the donut and the comments panel never move on a filter
change. Not a bug.

**The card-type dropdown only offers "All card types".** Card-type data is
optional and may not be present in your local copy; the page degrades to an
empty list rather than failing. If you know your library is tagged and the
list stays empty after a successful sync, flag it.

**The sync finishes but the numbers do not move.** The pull is incremental —
it picks up from where the last one stopped. When the status line reads "Guru
analytics synced." but the figures are unchanged, that usually means nothing
changed in Guru since the last pull. Numbers that never move across several
days of real activity are worth flagging.

**The expanded view shows only the donut and the card list.** Correct — the
comments and needs-attention panels are not carried into it. Collapse back to
the normal page to reach them.
