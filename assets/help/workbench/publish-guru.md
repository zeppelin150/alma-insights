---
id: workbench-publish-guru
title: Publishing to Guru
section: workbench
section_title: Create — Workbench
section_order: 4
order: 5
status: available
features: [en_workbench, publish_draft, push_guru_draft]
summary: Publish a draft as a new Guru card or as an update to an existing one — importing a card first is what stops you creating duplicates.
last_verified: 2026-07-20
---

Guru is the one destination the Workbench can actually write to today. The
choice that matters is whether you are creating a card or updating one.

## How it works

The Tools menu offers two Guru destinations: a new card, or an existing card
picked from a submenu.

**New card.** The draft's markdown is converted to HTML with the same converter
the preview uses, native block directives such as callouts and collapsibles are
expanded into Guru's markup, and the card is created in your configured publish
collection. If you last edited in the rich-text view, the captured HTML is sent
instead so colour and highlight survive.

The new card's id is recorded on the draft. A later edit and republish therefore
updates that same card rather than creating a second one.

**Existing card.** Opening the submenu fetches your Guru cards; up to
twenty-five are listed. Choosing one stamps the draft with that card's id and
then publishes, which takes the update path.

**Importing a card does the same stamping, earlier.** When you bring an existing
card into the Workbench as a draft, it is linked to its source card at import
time. Publishing that draft updates the original. This is the reliable way to
edit an existing card — you never have to remember to pick the right target from
a menu. See *Bringing a document in*.

After a successful publish the draft is marked as pushed, which freezes it
against further edits, and an audit record of the update is written.

**There is no confirmation step on this path.** Choosing a Guru destination from
the Tools menu publishes. The separate sign-off gate — where a draft cannot be
pushed without a recorded human approval — applies to publishes that Renn
initiates from chat, not to this menu.

## How it should work

The published card should match what the preview showed. Headings, lists,
tables, links and callouts should all arrive; so should colour and highlight, if
your last edit was in the rich-text view.

Publishing the same draft twice should not create two cards. The second attempt
against an already-pushed draft reports success without doing anything.

A publish that Guru rejects should surface the error and leave the draft
unpublished and still editable — not silently mark it as done.

Renn will not publish unless you explicitly ask, and its route additionally
requires sign-off. See *Why Renn never writes on its own*.

## If it doesn't

**Publishing failed.** The message carries Guru's own error. The usual causes
are Guru not connected, a publish collection not configured in settings, or
insufficient permission on the target collection. Fix the connection or the
target and retry — the draft is untouched.

**The card landed in the collection but not in the folder you configured.** The
Workbench menu publishes into the configured collection; it does not pass a
folder when creating a card, even when a publish folder is set in settings. File
it in Guru afterwards, and flag it — the setting reads as though it should apply.

**A duplicate card appeared instead of an update.** Check whether the draft was
actually linked. A draft built from an uploaded file has no card link until you
publish it once or pick an existing card as the target. If you imported the card
from Guru and still got a duplicate, that is a bug — flag it with both card ids.

**The existing-card submenu is empty or says no cards were found.** It needs a
live Guru connection, and it fetches once. Connect Guru, then reopen the
Workbench. It also lists only the first twenty-five cards, so a card you expect
may simply not be in the list — use the Guru import instead, which opens a
picker with a collection filter and a search box rather than a capped list.
(There is no import-by-URL route for a Guru card; the URL prompt is the Drive
import path.) See *Bringing a document in*.

**You published by accident.** There is no undo here and no confirmation
beforehand. Edit the card in Guru directly, or in the Workbench start a fresh
draft by importing that card. Published drafts are frozen and cannot be edited
in place.

**You wanted to publish somewhere other than Guru.** See
*Publishing to Drive is not wired yet*.
