---
id: create-zendesk-macros
title: Editing macros
section: create
section_title: Create — Decks, Zendesk and the Content Studio
section_order: 5
order: 4
status: partial
features: [en_zendesk]
summary: The classic macro editor exposes only the public reply, and saving rewrites that reply as plain text while keeping the macro's other actions; nothing is sent to Zendesk, and the web workspace shows the full action list.
last_verified: 2026-07-26
---

A Zendesk macro is a list of actions — set a status, add tags, assign a group,
post a reply. This editor exposes exactly one of them: the public reply.

Editing here is entirely local. The app cannot write to Zendesk at all (see
*Zendesk: syncing and drafting articles*), so nothing you do in this editor
reaches a live macro. Knowing what a save keeps and what it overwrites still
matters, because what you end up copying into Zendesk is whatever the save
left behind.

## How it works

Selecting a macro draft fills in its name and puts its public reply in a
plain-text box. The reply shown is the **first** comment action on the macro;
if a macro somehow carries more than one, you only ever see the first.

Saving rebuilds the macro draft's action list. Precisely:

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
reply do not survive the round trip, so re-apply them in Zendesk's editor after
you paste.

Finishing a macro draft is local bookkeeping, exactly as it is for articles: it
marks the draft handled and drops it out of the pending list without contacting
Zendesk. The live macro changes only when you open it in Zendesk and paste the
reviewed values in yourself.

## How it should work

A save should leave every action you did not edit intact. You should be able to
open a macro that sets tags and a status, change only the wording of the reply,
save, and still have the tags and status.

The reply you see should be the reply you copy. What is in the box is what
becomes the macro's comment.

Nothing should ever reach Zendesk from this editor. Editing, saving and marking
a draft handled are all local.

## If it doesn't

**The reply box is empty but the macro clearly has a reply.** Its reply may not
be the first comment action, or it may be stored in a form the box does not
read. Do not save over it — a save would replace the reply with the empty box.
Flag it with the macro name.

**Formatting disappeared from the reply.** Expected. The editor is plain text.
If a macro's reply needs rich formatting, edit that macro in Zendesk instead.

**The action order changed.** Expected — a save moves the reply to the front of
the local list.

**You need to change something other than the reply.** Not possible here; this
editor covers the name and the reply only. Use Zendesk for the rest.

**A draft you finished did not change the macro in Zendesk.** Expected. Nothing
is sent. Open the macro in Zendesk and paste the reply in yourself. Note that
pasting a reply changes only the reply — the actions already on the live macro
are whatever Zendesk has, not what this editor was holding.

## The web workspace's macro editor

Everything above describes the classic tab. When the web workspace is enabled
(see *Zendesk: syncing and drafting articles*), macros render in a replica of
Zendesk's Admin Center editor instead: the **full action list** as rows — status,
priority, tags, assignee, the reply and the rest — read from the local mirror.
Renn's proposed macro changes appear as revisions with the reply preserved
verbatim as plain text — copying a reply puts plain text on the clipboard, so
formatting still has to be applied in Zendesk's editor — and nothing is sent to
Zendesk from there either: you review the diff, copy the final values exactly,
and paste them into Zendesk yourself.
