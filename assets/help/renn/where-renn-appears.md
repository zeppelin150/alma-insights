---
id: renn-where-it-appears
title: Where Renn appears
section: renn
section_title: Renn, your assistant
section_order: 2
order: 5
status: partial
features: [en_agent, en_workbench, en_calendar]
summary: The Agent page and the Workbench assistant panel both reach Renn and now share one continuous conversation; the in-page drawer still needs a setting that ships off.
last_verified: 2026-07-20
---

Renn is the same assistant with the same tools and the same capabilities
wherever you reach it — and now the conversation is shared too. The Agent page
and the Workbench assistant panel resolve to the same chat session, so a thread
you start on one surface continues on the other. Move between them freely. The
one surface you may not see is the in-page drawer, which needs a setting that
ships off.

## How it works

**The Agent page** — the dedicated full-screen chat, reached from the sidebar.
This is the richest surface: it carries voice input, the jobs sidebar for
long-running work, pickers, and Confirm cards.

**The Workbench assistant panel** — "Open Assistant ›" from the Workbench,
which puts Renn beside the draft you are editing so it can act on that draft
without you naming it.

**The in-page drawer** — a slide-out Renn inside the Calendar and Workbench
web views. This one is behind a setting that is currently off, so you will not
see it.

The Agent page and the Workbench panel resolve to the *same* chat session, so
what you said on one is visible on the other — one Renn, one transcript, one
continuous history. Whichever surface you open first creates the session; the
other picks up that same session through a shared pointer rather than starting
its own. (The in-page drawer, when its setting is on, joins the same session.)

## How it should work

The active draft should be picked up automatically when you open the assistant
from the Workbench — you should be able to say "tighten the second section"
without identifying which card.

You can move between the Agent page and the Workbench panel mid-thread without
losing the conversation. Start something on the Agent page, switch to the
Workbench panel to work on the draft in front of you, and Renn still has the
earlier context — it is the same thread, not a fresh, empty one.

## If it doesn't

**You cannot find the in-page drawer inside Calendar or Workbench.** Expected.
The React versions of those tabs are behind the `enablement.web_tabs` setting,
which is not enabled in the shipped configuration — the native Qt tabs render
instead, and they do not carry the drawer. Use the Agent page or the Workbench
assistant panel — both reach the same Renn and the same conversation. This is
on the *Known issues and current limitations* list, not a fault in your setup.

**Renn does not know which draft you mean** when opened from the Workbench.
Name the draft explicitly as a workaround, then flag it.

**History is missing or a session appears empty.** The Agent page and the
Workbench panel share one session, so a thread from one *should* appear on the
other — switching surfaces is no longer expected to lose it. If a surface opens
empty even though you had a live conversation on the other, that is worth
flagging, not expected behaviour. To browse older Agent-page conversations, use
the Agent page's own history browser.

**Voice input does not appear.** Voice lives on the Agent page only, not the
Workbench panel.
