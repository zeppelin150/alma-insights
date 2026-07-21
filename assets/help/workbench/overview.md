---
id: workbench-overview
title: "The Workbench: workspaces, drafts and views"
section: workbench
section_title: Create — Workbench
section_order: 4
order: 1
status: partial
features: [en_workbench]
summary: The Workbench holds up to four card drafts at once and shows each one in four views — preview, rich text, markdown source, and a change diff.
last_verified: 2026-07-20
---

The Workbench is where a document becomes a Guru card. It holds a small number
of drafts open at once, renders the active one on a canvas, and gives you four
ways to look at the same content.

## How it works

**Workspaces.** The strip across the top holds one chip per open draft, with a
count beside it. You can keep **four** drafts open at a time. Click a chip to
switch to that draft; press Ctrl+1 through Ctrl+4 to jump to the first through
fourth chip. The small × on a chip closes that workspace.

Opening a fifth draft does not fail — the oldest workspace is dropped to make
room, along with any unsaved state it was holding.

**Views.** Four buttons sit above the canvas and swap what the body shows:

| View | What you get |
|------|--------------|
| Guru preview | The card rendered the way it will look once published |
| Rich text | A formatting editor — headings, lists, tables, colour, callouts |
| Edit markdown | The raw markdown source in a monospace box |
| Review changes | A red/green diff of this draft against the card it updates |

A fifth button expands the editor to fill the window when you want to write
without the surrounding page. It carries its own rich-text and markdown pair;
what you commit there flows back into the draft.

**Saving.** There is no save button. Leaving an editing view — switching views,
switching workspaces, or collapsing the expanded editor — reads your changes
back and writes them to the draft. Switching between chips preserves the view
you were in, the unsaved text, and your cursor position.

A draft that has already been published to Guru is frozen: edits to it are not
written back.

## How it should work

Switching between chips should feel like switching tabs. Type into the markdown
view of one draft, jump to another chip, come back, and your text and cursor
should be exactly where you left them.

The preview should match what Guru shows after you publish. Formatting you
apply in the rich-text view — bold, headings, colour, highlight — should
survive the trip. See *Publishing to Guru*.

The chips should reflect real pending drafts. Every draft you import, upload,
or have Renn create should appear there. See *Bringing a document in*.

## If it doesn't

**The canvas shows a card about SSO setup for providers that you never
created.** That is the built-in sample content. It is displayed at startup and
is only replaced once a real draft loads — so when you have no pending drafts
at all, the sample card stays on screen with zero workspace chips. Ignore it;
import or upload something and it will be replaced. Worth flagging, but it is
not a sign your data is missing.

**The subtask counter and scratch-pad note under the canvas never change.**
That strip is currently fixed display text — it does not read your task's real
subtasks or scratch pad. Use the Tasks side of the app for real subtask state.
This is a known gap.

**A chip disappeared on its own.** You probably opened a fifth draft. Only four
workspaces are kept; the oldest is closed. Reopen it from wherever you opened it
originally — the draft itself is not deleted, only the workspace.

**Your edit reverted after switching chips.** Switching should preserve unsaved
text. If it does not, flag it, and say which view you were in when you switched.

**Edits to a draft do not stick at all.** Check whether that draft has already
been published. Published drafts are intentionally frozen. If it has not been
published and edits still vanish, flag it.
