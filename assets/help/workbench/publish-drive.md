---
id: workbench-publish-drive
title: Publishing to Drive is not wired yet
section: workbench
section_title: Create — Workbench
section_order: 4
order: 6
status: not-available
features: [en_workbench]
summary: The Workbench's two Drive destinations write nothing — they only display a message. Publish to Guru or export manually instead.
last_verified: 2026-07-20
---

The Tools menu offers to save a draft to Drive as a new Google Doc or as an
update to an existing one. Neither works. Nothing is written to Drive.

## How it works

Choosing either Drive destination sets a status message and posts a matching
line into the assistant panel. That is the entire implementation — there is no
Drive call, no document created, no document updated, and no error raised.

Both messages describe an action that did not happen, and both are labelled as
demonstration text. Reading them as confirmation is the trap: it looks like a
successful publish.

There is no Drive-writing code behind these menu entries in this build. This is
not a permissions problem, a configuration problem, or something you can enable.

Reading from Drive is a different matter and does work — importing a Google Doc
into the Workbench fetches real content. See *Bringing a document in*. Only
writing back is missing.

## How it should work

Until this is built, use one of these instead:

**Publish to Guru.** Guru is the destination that is actually implemented, for
both new cards and updates to existing ones. See *Publishing to Guru*.

**Export by hand.** Switch to the markdown view, select the source, copy it, and
paste it into a Google Doc yourself. This is the only way to get Workbench
content into Drive today.

**Ask Renn to put an artifact in Drive.** Renn can upload generated artifacts to
Drive, and that route does open a confirmation before writing. It is a different
mechanism from the Workbench menu and does not publish the card draft you have
open. See *Why Renn never writes on its own*.

## If it doesn't

**You chose a Drive destination and the app said it worked.** The message is
wrong. Do not treat it as confirmation. Check Drive — the document is not there
and was never created.

**You have been publishing to Drive from here for a while.** Nothing you sent
this way arrived. Check whether the content exists anywhere else before assuming
a document was lost; the drafts themselves are still in the Workbench and can be
published to Guru or copied out.

**You want this fixed.** It is a known gap rather than a fault in your setup, so
a bug report will not tell anyone something new — but it is worth saying how you
were using it, because that shapes whether the fix should create Google Docs,
update them, or both.
