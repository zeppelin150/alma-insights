---
id: create-zendesk-macros
title: Editing macros
section: create
section_title: Create — Decks, Zendesk and the Content Studio
section_order: 5
order: 4
status: partial
features: [en_zendesk]
summary: The classic macro editor exposes only the public reply, and saving rewrites that reply as plain text while keeping the macro's other actions; the web workspace shows the full action list.
last_verified: 2026-07-24
---

A Zendesk macro is a list of actions — set a status, add tags, assign a group,
post a reply. This editor exposes exactly one of them: the public reply. Knowing
what a save keeps and what it overwrites matters before you touch a live macro.

## How it works

Selecting a macro draft fills in its name and puts its public reply in a
plain-text box. The reply shown is the **first** comment action on the macro;
if a macro somehow carries more than one, you only ever see the first.

Saving rebuilds the macro's action list. Precisely:

| Part of the macro | What a save does |
|---|---|
| Name | Replaced with the name field |
| Public reply | Replaced with the box, as plain text |
| Formatting in the reply | Lost — stored as a plain-text comment |
| A second comment action | Collapsed into the one reply |
| Tags, status, assignee, other actions | Kept |
| Order of the actions | Reply moves to the front |
| Description | Kept, and not editable here |

The important line is the third. If the macro's reply was rich text in Zendesk,
saving here converts it to a plain-text comment. Bold, links and lists in that
reply do not survive the round trip.

Pushing sends the whole rebuilt action list. A draft linked to a live macro
**replaces** that macro's actions with the local list rather than merging into
it, so any action someone added in Zendesk since your last sync is overwritten.
An unlinked draft creates a new macro instead.

As with articles, pushing while Zendesk is not connected marks the draft pushed
locally without contacting the API. See *Zendesk: syncing and drafting
articles*.

## How it should work

A save should leave every action you did not edit intact. You should be able to
open a macro that sets tags and a status, change only the wording of the reply,
save, and still have the tags and status.

The reply you see should be the reply that gets pushed. What is in the box is
what becomes the macro's comment.

Nothing should reach Zendesk until you push. Editing and saving are local.

## If it doesn't

**The reply box is empty but the macro clearly has a reply.** Its reply may not
be the first comment action, or it may be stored in a form the box does not
read. Do not save over it — a save would replace the reply with the empty box.
Flag it with the macro name.

**Formatting disappeared from the reply.** Expected. The editor is plain text.
If a macro's reply needs rich formatting, edit that macro in Zendesk instead.

**The action order changed after pushing.** Expected — the reply is moved to the
front of the list.

**Actions that existed in Zendesk are gone after a push.** The local action list
replaced them. Sync before you edit a live macro so you are working from its
current state, and flag it if you had synced first.

**You need to change something other than the reply.** Not possible here; this
editor covers the name and the reply only. Use Zendesk for the rest.

**A push reports success but the macro is unchanged in Zendesk.** Check that
Zendesk is connected. If it is, flag it — macro writes are a thin path and worth
reporting with the macro name and what you expected to change.

## The web workspace's macro editor

Everything above describes the classic tab. When the web workspace is enabled
(see *Zendesk: syncing and drafting articles*), macros render in a replica of
Zendesk's Admin Center editor instead: the **full action list** as rows — status,
priority, tags, assignee, the reply and the rest — read from the local mirror.
Renn's proposed macro changes appear as revisions with the reply preserved
verbatim as plain text — copying a reply puts plain text on the clipboard, so
formatting still has to be applied in Zendesk's editor — and nothing pushes to
Zendesk from there: you review the diff, copy the final values exactly, and
paste them into Zendesk yourself.
