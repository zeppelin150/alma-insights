---
id: workbench-style-guides
title: Style guides and card templates
section: workbench
section_title: Create — Workbench
section_order: 4
order: 7
status: partial
features: [en_workbench, en_settings, list_style_guides, set_active_style_guide]
summary: An active style guide sets tone and formatting, an active card template sets the exact heading structure — but only for AI generation, not for plain uploads.
last_verified: 2026-07-20
---

Two documents shape what generated cards look like. They are separate on
purpose: the style guide governs how the writing sounds, the card template
governs how the card is laid out.

## How it works

Both live in the enablement settings, in the same tab, and behave identically.
For each you can:

- paste the text directly into a box and save it,
- upload one or more documents (.md, .markdown, .txt, .docx, .html, .htm),
- upload a whole folder — supported files are found recursively, capped at
  fifty, with hidden and Office lock files skipped,
- import one from a Google Doc URL or file id,
- view and edit the active one's text in place,
- switch which stored one is active, delete stored ones, or clear the setting.

Uploading several at once stores each of them; the last one becomes active. Only
one style guide and one card template are active at a time.

**How they reach the model.** When a card is generated or revised, the active
style guide is attached as a block instructing the model to follow it strictly
for tone, structure and formatting. The active card template is attached as a
separate block instructing the model to reproduce its heading layout exactly —
same headings, same order — filling the bracketed slots with real content and
dropping the template's own instructional notes rather than copying them into
the card.

Both blocks are injected into card generation from a Drive import, and into
every revision, including the four inline AI actions in the rich-text view. One
of those actions is specifically "rewrite this to match the style guide".

**They do not apply to local file uploads.** A file you drag in or upload from
disk is converted by deterministic Python with no model involved, so there is
nothing for a style guide or template to steer. The card mirrors the document's
own structure. See *Bringing a document in*.

The app ships with a default article template, used to seed the setting.

## How it should work

Setting a style guide should change the next generated or revised card, not
existing ones. Nothing is retroactive — to bring an old card into line, open it
and run a revision.

The settings panel should show the active guide's status and character count,
and list every stored one with the active one marked. Switching the active guide
should take effect on the next generation with no restart.

A card template with five headings should produce a card with those five
headings in that order, and none of the template's own guidance text.

## If it doesn't

**An uploaded file produced a card that ignores your style guide.** Expected —
that path uses no AI. Ask Renn to revise the draft, or use the "match style
guide" action in the rich-text view, and the guide will be applied then.

**A folder upload stored nothing.** Only .md, .markdown, .txt, .docx, .html and
.htm are picked up, and the scan stops at fifty files. Files whose names begin
with a dot or with a tilde-dollar are skipped as hidden or lock files.

**The wrong guide is active after a multi-file upload.** The last file stored
wins, and ordering follows the sort, not your selection order. Set the one you
want active explicitly in the list afterwards.

**Generated cards ignore the template's headings.** The template is an
instruction to the model, not a hard constraint the app enforces — a long or
ambiguous template gets followed loosely. Tighten it to a plain heading skeleton
with bracketed slots. If a short, unambiguous template is still ignored, flag it
with the template text.

**You cannot find a stored guide by searching.** Searching stored documents
matches the whole phrase as one string rather than the individual words, so a
multi-word search often returns nothing even when a matching document exists.
Search a single distinctive word instead. This is a known limitation.

**The status still reads as not set after you saved.** Reopen the settings tab
to force a refresh. If it is still unset, flag it with how you supplied the text
— pasted, uploaded, or imported from Drive.
