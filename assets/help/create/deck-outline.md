---
id: create-deck-outline
title: Editing the outline and exporting
section: create
section_title: Create — Decks, Zendesk and the Content Studio
section_order: 5
order: 2
status: partial
features: [en_pptx]
summary: The outline is plain text with two prefixes, and export writes a real .pptx with a cover slide added. The shipped brand template is a placeholder, so every export currently comes out unbranded.
last_verified: 2026-07-20
---

A deck has two views: a slide preview and a plain-text outline. The outline is
the real thing — the preview is drawn from it.

## How it works

The outline uses exactly two prefixes:

```
# Slide title
- A bullet
- Another bullet
```

A line starting with `# ` opens a new slide. A line starting with `- ` adds a
bullet to whichever slide is open. **Every other line is ignored.** Blank lines,
notes to yourself, prose paragraphs — none of it survives a save. A bullet
written before any slide title creates a slide of its own with a placeholder
title.

Switching to the slide view re-renders from whatever is currently in the
editor, including edits you have not saved. The preview is therefore a preview
of your typing, not of what is stored. Saving is what persists it, and the
status line confirms with the slide count it stored.

There is also a focus view that opens the deck in an overlay for editing
without the surrounding tab; committing there saves the same way.

Exporting asks you where to put the file and writes a real .pptx. Export loads
a template file that ships with the app and, if that file is missing or
unreadable, quietly falls back to python-pptx's plain default layout rather than
failing. In practice this makes no visible difference today: the shipped
template (`assets/templates/renn_deck.pptx`) is a **placeholder** — it is
python-pptx's own default deck re-saved, with the same theme, the same eleven
layout names and the same slide size. So every export currently comes out
unbranded whether the template loads or not. A **cover slide carrying the deck
title is added at the top**, which is why the export message reports one more
slide than the deck list shows. Speaker notes are written to the .pptx when the
outline carries them. Exporting marks the deck exported and records the path.

## How it should work

Saving should never change your bullets' wording or order. Exporting should
never change the stored outline — it only reads it.

The count difference between the list and the export message should be exactly
one, and always in the same direction: the export includes the cover.

Once exported, the deck's status shows as exported when you reopen it. The file
stays where you put it; the app does not move or re-upload it. Sending a deck to
Drive is a separate, confirmed action — see *Why Renn never writes on its own —
the Confirm card*.

## If it doesn't

**Text you typed disappeared after saving.** Expected, and the most common
surprise here. Anything not prefixed with `# ` or `- ` is dropped. Re-enter it
as a bullet.

**Speaker notes vanished after you saved.** This is a real limitation, not your
mistake. The outline editor has no way to show notes, so a deck that arrived
with speaker notes — a deck Renn generated, typically — loses them the first
time you save from this tab. If the notes matter, export before editing, or ask
Renn to regenerate. Worth flagging.

**Export fails saying a PowerPoint library is missing.** The .pptx writer is not
installed in this environment. This is an install problem, not a content
problem — flag it.

**Export fails saying the deck was not found.** The deck was removed underneath
you. Reselect it from the list.

**The exported deck looks unbranded.** Expected for now, and not a failure on
your end. The brand template that ships with the app is a placeholder identical
to python-pptx's default, so exports are unbranded even when the template loads
cleanly — nothing "degraded". The deck is still valid. There is no setting that
turns branding on; it needs a real Alma-branded template to be dropped in to
replace the placeholder. If a branded deck matters, flag it so the template can
be supplied, and in the meantime apply your brand in PowerPoint after export by
applying a branded theme or template there.

**The slide count in the list does not match the file.** A difference of one is
the cover slide and is expected. Any other difference is worth flagging with
both numbers.
