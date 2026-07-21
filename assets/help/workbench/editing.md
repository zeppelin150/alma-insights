---
id: workbench-editing
title: "Editing: markdown, rich text and inline AI"
section: workbench
section_title: Create — Workbench
section_order: 4
order: 3
status: partial
features: [en_workbench, revise_card_draft]
summary: Edit a draft as markdown or in a formatting editor, and fold a highlighted passage into an AI revision without retyping it.
last_verified: 2026-07-20
---

Two editors work on the same draft. The markdown view is the source of truth;
the rich-text view is a formatting layer over it that additionally captures
colour and highlight for publishing.

## How it works

**Edit markdown** gives you the raw source in a monospace box. What you type is
kept verbatim.

**Rich text** gives you a toolbar: headings, bold, italic, underline,
strikethrough, inline code, text colour, highlight, bulleted, numbered and task
lists, blockquotes, code blocks, tables, links, images, horizontal rules, and
insertable callout, collapsible and card-link blocks.

The two round-trip through markdown. Editing in rich text also captures a
cleaned HTML copy, which is what gets sent when you publish, so colour and
highlight survive. Editing the markdown source clears that HTML copy — the
publish step re-derives it from your markdown instead.

**Inline AI.** In the rich-text view there are two ways to ask for a revision:

- Type `/` at the start of a line. A menu opens at the cursor and the slash is
  not inserted.
- Select some text and open the context menu. An "ask Renn" submenu appears; it
  is greyed out when nothing is selected.

Both offer the same four actions: rewrite more concisely, rewrite to be
AI-readable, turn the passage into numbered steps, or rewrite to match the
configured style guide.

**Your selection is folded into the instruction.** The highlighted passage is
attached to the request with a note telling the model to apply the change to
that passage only and return the full revised card. From the `/` menu with
nothing selected, the current line is used. With genuinely nothing to scope to,
the instruction applies to the whole card.

The revision runs off the main thread against your active draft, then the canvas
reloads in place from the newly-stored content. The revision is written to the
draft in the database, so it is already saved when you see it.

The revision applies your active style guide and card template. See
*Style guides and card templates*.

## How it should work

A scoped edit should change the passage you highlighted and leave the rest of
the card recognisably intact. The whole card is regenerated, so check the parts
you did not intend to change — that is what *Review changes: reading the diff*
is for.

The status line should say a revision is running, then say the draft was revised
and reloaded. A failure should say so rather than leaving you waiting.

A revision you dispatched on one draft, then switched away from, should still
land on the draft it was dispatched for — not on whatever is in front of you
when it finishes.

## If it doesn't

**You cannot type your own instruction.** Correct — the inline menu offers only
the four fixed actions. For anything else, open the assistant from the Workbench
and describe the change in your own words. Renn revises the same active draft.
See *Where Renn appears*.

**The `/` menu does not open.** It only triggers at the start of a line, and
only in the rich-text view. In the markdown view, `/` is just a slash.

**The AI actions do nothing inside the expanded editor.** Known gap. The
expanded editor hosts its own copy of the rich-text editor, and its inline AI
menu is not connected to anything — the menu appears and the action is
discarded. Collapse back to the inline rich-text view to use them, or ask Renn.
Worth flagging.

**The revision failed.** The message names the cause. The most common is no
model client available, which means the enablement provider is not signed in.
Your draft is unchanged when a revision fails — nothing is written unless the
model returned a card.

**The revision rewrote parts you did not highlight.** The model is asked to
return the complete card, so unintended drift is possible. Use the diff view
before publishing. Persistent, heavy drift is worth flagging with the
instruction you chose.

**Colour or highlight vanished after publishing.** Check whether you last edited
in the markdown view — that clears the captured HTML and publishing re-derives
plain formatting from markdown. Re-apply the colour in the rich-text view and
publish again.
