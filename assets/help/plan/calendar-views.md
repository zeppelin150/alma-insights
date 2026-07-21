---
id: plan-calendar-views
title: "The Calendar: month, week, and scope"
section: plan
section_title: Plan — Calendar and Tasks
section_order: 3
order: 1
status: available
features: [en_calendar]
summary: How the Calendar tab lays out your due dates — the month and week views, the Mine and All scope toggle, and the "+N more" day expander.
last_verified: 2026-07-20
---

The Calendar tab plots everything with a due date on a grid: your enablement
tasks, plus Guru cards that are due for a refresh. It is a read-and-open
surface — clicking takes you somewhere, it does not change anything in Asana.

## How it works

**Month and week.** Two buttons at the top right switch the grid between a
full month and a single week. Both start the week on Sunday. The arrows beside
the month name move you a month at a time; switching months while you are in
the week view puts you back in the month view, because a week is not a thing
you can page through a month at a time.

**Colour.** Each chip is tinted by where it came from, and the legend along
the top spells out the four tints: Drive, Guru, Asana, and one for high
priority or due items.

**Mine and All.** A second pair of buttons filters between the tasks assigned
to you and everything on the board. Your choice is saved, and it is shared —
switching to Mine on the Calendar switches the Tasks tab too, and it is still
set the next time you open the app.

**Busy days.** A day cell holds three chips. On a busier day you see the first
two plus a "+N more" chip; clicking that opens a list of that day's tasks in
the side panel, and clicking any entry there opens its detail.

**Clicking a chip** opens the task detail panel in the side panel — not the
Workbench. Guru card chips behave differently; see *Card-due chips: why Guru
cards appear on your calendar*.

## How it should work

Month navigation should be unbounded — you should be able to page backwards
and forwards freely, and the current day should stay circled when you land
back on this month.

Chip titles are truncated to fit the cell, so a long task title will be cut
short. The full title is in the detail panel. That is expected, not a
rendering fault.

The Calendar and the Tasks tab should never disagree about scope. They are fed
from the same query, so if Mine is lit on one, it is lit on the other and both
are showing the same set.

Mine has a deliberate fallback: **if the app does not know who you are, Mine
shows everything rather than showing nothing.** The filter needs either your
Asana user ID or your display name. With neither set, a strict filter would
match no rows and you would stare at an empty board on your first day, so the
app shows all tasks instead. If Mine and All look identical, that is almost
certainly why — see *Setting up your Asana board with Renn* for how to set
your identity.

## If it doesn't

**Mine and All show exactly the same tasks.** Your identity is not set. This
is the deliberate fallback described above, not a broken filter. Set your
identity in Settings, then check again.

**The week view shows this week even though you navigated to another month.**
Known behaviour: the week view is always anchored to the real current week and
ignores the month you paged to. Use the month view to look at other periods.

**The day list from "+N more" is shorter than the number on the chip.** The
expander lists tasks only. Guru card-due entries are counted in the chip but
are not included in that list. Flag it if the gap is not explained by card
chips on that day.

**The grid is empty.** Either nothing has a due date, or the scope filter is
hiding it. Try All first. If All is also empty and you know there are tasks in
Asana, the board may not be connected yet.

**You see tasks dated June 2026 that you do not recognise.** The Calendar
falls back to a small set of sample chips before real data loads, which is
what demo mode shows. If you are not intentionally in demo mode, flag it.

**A chip opens a detail panel with no description, no assignee, and dashes
everywhere.** The panel could not match the chip to a real task. Flag it with
the chip's text.
