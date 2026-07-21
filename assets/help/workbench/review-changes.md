---
id: workbench-review-changes
title: "Review changes: reading the diff"
section: workbench
section_title: Create — Workbench
section_order: 4
order: 4
status: partial
features: [en_workbench]
summary: A red/green diff of your draft against the card it updates — but the comparison baseline is not currently supplied, so it reads as all-new.
last_verified: 2026-07-20
---

The review view exists so you can see exactly what a draft changes before it
goes anywhere, instead of reading a wall of regenerated text and hoping.

## How it works

Switch to the review view and the draft is compared line by line against the
current content of the Guru card it is set to update. Removed lines are shown in
red with a minus in the gutter, added lines in green with a plus, unchanged
lines in neutral. Where a block was replaced, the removed lines are listed first
and the added lines directly after, so it reads top to bottom.

When the two sides are identical, the view says so rather than showing an empty
box.

Switching into this view first commits whatever you were editing, so the diff
always reflects your latest text.

**The comparison baseline is not currently being supplied.** The Workbench has a
way to receive the linked card's current content, but nothing in the app calls
it, and the card data handed to the canvas does not carry it. In practice the
baseline is empty, so the diff has nothing to compare against and renders your
entire draft as green additions with no red.

## How it should work

For a draft created by importing an existing Guru card, the view should show the
live card on the red side and your edits on the green side, so a small wording
change reads as a handful of coloured lines in a mostly-neutral document.

For a genuinely new card there is no baseline, and all-green is the correct
result.

An all-green diff on a draft you know is an edit of an existing card means the
baseline is missing, not that you rewrote everything.

A word-level version of this diff — which highlights the specific words that
changed inside a modified line, rather than colouring the whole line — exists in
the code but only in the React version of the Workbench, which does not render
in the shipped configuration. See *Where Renn appears* for why. The view you get
today is line-level.

## If it doesn't

**Everything is green and nothing is red.** Expected today, for the reason
above. It does not mean your draft replaced the card wholesale. To check your
actual changes against the live card, open the card in Guru in a browser and
compare by eye before publishing. This is a known defect worth flagging so it
gets prioritised.

**The view says there are no changes when you know you edited something.** The
commit-on-leave step should have captured your edit. Switch to the markdown view
and confirm your text is actually there. If it is, and the diff still says no
changes, flag it.

**The diff is enormous for a small edit.** A whole-card AI revision regenerates
the entire card, so formatting and line breaks shift even where the wording did
not. That is expected after an inline AI action. See
*Editing: markdown, rich text and inline AI*.

**You want to review before Renn publishes.** Renn cannot publish without you
asking, and its chat-driven publish path additionally requires a recorded
sign-off. See *Why Renn never writes on its own* and *Publishing to Guru*.
