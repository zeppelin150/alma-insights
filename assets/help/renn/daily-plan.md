---
id: renn-daily-plan
title: "Your daily plan: the morning briefing"
section: renn
section_title: Renn, your assistant
section_order: 2
order: 6
status: available
features: [renn, list_tasks, en_tasks]
summary: Renn is handed your real task data at the start of a turn, so "what's on my plate" is answered instantly and accurately.
last_verified: 2026-07-20
---

Ask Renn what your day looks like and it answers immediately, without going and
fetching anything.

## How it works

At the start of a turn, the app pre-loads your real tasks and hands them to
Renn as part of the conversation context. It is the same data the Tasks board
shows — pulled from the task store, not invented and not summarised by a model
first.

Because it arrives pre-loaded, Renn can brief you in one shot rather than
running a query and waiting.

Your identity is supplied the same way, so "what's assigned to me" resolves
without you naming yourself.

## How it should work

"What's on my plate today", "give me my morning briefing", "what's due this
week" should all produce a confident, specific answer drawn from real tasks.

Renn should never hedge about whether this data is real, and should never
describe it as an example or a placeholder. It is your actual board.

It should also not re-query just to double-check the data it was already given.

## If it doesn't

**Renn describes your task list as fabricated, invented or illustrative.**
That is a bug — the data is authoritative. Flag it with what you asked.

**The plan is empty but your board has tasks.** Two likely causes: your
identity is not set, so the "mine" filter has nothing to match; or the active
Asana board is not configured. Ask "what's connected?" to check both. If both
look right and the plan is still empty, flag it.

**The plan shows other people's tasks.** The "mine" filter falls back to
showing everything when neither an Asana ID nor a display name is set for you —
deliberately, so a new user does not see a blank board. Set your identity under
Settings → Connections to narrow it.

**Tasks look out of date.** Ask Renn to run the monitor to pull fresh items.
