---
id: workbench-editing-by-hand
title: Editing a draft by hand
section: workbench
section_title: Create — Workbench
section_order: 4
order: 8
status: available
features: [en_workbench]
summary: Change a word or fix formatting yourself, directly in the draft — faster than describing the edit to Renn, and always allowed.
last_verified: 2026-08-09
---

You do not need Renn for small changes. Removing a word from two headings,
fixing a typo, re-ordering a list — open the draft and make the change
yourself. Describing an edit to the assistant and waiting for the revision is
the long way round for anything you could type in five seconds.

## How it works

Every draft is editable by hand, including the ones Renn created in chat —
chat drafts and Workbench drafts are the same records in the same store. Open
the Workbench, pick the draft, and switch to **Edit markdown** (the raw
source, kept verbatim) or **Rich text** (a formatting toolbar that also
captures colour and highlight). Type the change.

There is no Save button. Leaving an editing view — switching to **Preview**
or **Diff**, changing chips, or opening another draft — commits what you
typed into the draft, and the status line confirms with "Draft N updated
from the editor."

Two things worth knowing:

- **Pushed drafts are frozen.** Once a draft has been published, its record
  stays as published — hand edits to it are not written back.
- **Editing an approved draft drops the approval.** A sign-off certifies the
  exact bytes you reviewed. Change the bytes and the pending sign-off is no
  longer valid: the publish refuses because its scope changed, and the draft
  comes back for a fresh review. That is deliberate — nothing publishes bytes
  that were never reviewed.

## How it should work

The word you deleted should stay deleted: your text is committed verbatim to
the same draft Renn works on, so a later revision from chat starts from your
edited version, not a stale copy.

A sign-off given before your edit should never be redeemable by a publish
after it. Practical consequence: make hand edits first, then approve, then
publish — approving before editing just means approving twice.

## If it doesn't

**You keep typing wording instructions into Renn.** Faster path: open the
draft in the Workbench and make the change in **Edit markdown**. Renn is for
edits you would rather describe than perform — restructuring, rewriting to
the style guide, drafting from sources. See *Editing: markdown, rich text
and inline AI* for everything the two editors offer.

**Your approval "disappeared" after an edit.** That is the byte-fingerprint
gate working as designed. Re-open the review panel and approve the edited
version.

**Colour vanished after publishing.** Your last edit was in the markdown
view, which clears the captured rich HTML — publishing re-derived plain
formatting from markdown. Re-apply the colour in **Rich text** and publish
again. See *Editing: markdown, rich text and inline AI*.
