---
id: create-studio-no-screen
title: The Content Studio has no screen yet
section: create
section_title: Create — Decks, Zendesk and the Content Studio
section_order: 5
order: 6
status: partial
features: [generate_diagram, generate_quiz, generate_doc, generate_deck, list_artifacts]
summary: There is no Content Studio tab or button — you reach every generator by asking Renn in plain language.
last_verified: 2026-07-20
---

The generators described in *The Content Studio: diagrams, quizzes, one-pagers
and battle cards* work, but nothing in the interface points at them. There is no
studio tab, no menu entry, no button. Asking Renn is the only way in.

## How it works

You describe what you want. Renn picks the generator. You do not need to know
what the generators are called.

Phrasings along these lines work, as long as the source is clear:

- "Write a one-pager from that document."
- "Turn this task into a battle card."
- "Make a five-question quiz from that document."
- "Draw a flowchart of this process:" followed by the steps.
- "Build a deck from that document for the enablement team."

Each of those names **one** source. That is the part to get right — the
generators accept exactly one, so "make a one-pager from these three documents"
will come back as a question rather than a result.

To find a source first, ask Renn to search for it, then refer to what it found:
"search Drive for the refunds policy", then "make a quiz from that one".

To see what already exists, ask for a list — "what artifacts do I have", "list
my quizzes". This enumerates them; it does not show their contents.

**Decks are the one kind you can see in the interface.** A deck Renn generates
is saved as a real deck and appears in the PowerPoint tab's list alongside decks
you modelled yourself, where you can preview, edit and re-export it. Before you
edit one there, read the speaker-notes warning in *Editing the outline and
exporting* — a Renn-built deck may carry notes that saving from that tab will
drop.

## How it should work

Renn should generate rather than describe. Asking for a one-pager should produce
one, not an explanation of what a one-pager is.

It should not send you looking for a screen that does not exist. If it tells you
to open a Content Studio panel or click a generate button, it is inventing an
interface.

Attaching output to a card draft should still leave the draft under your review.
Generating is not publishing.

## If it doesn't

**Renn tells you to open a Content Studio tab, page or button.** There isn't
one. Ask it to do the work directly instead, and flag the wording — pointing
users at interface that does not exist is a bug.

**Renn asks which source you meant.** Expected when your request named more than
one. Pick a single document, task, or block of text.

**Renn describes what it would create instead of creating it.** Ask more
directly, naming the output and the source in one sentence. If it keeps
narrating, flag it.

**Nothing appears anywhere after Renn says it generated something.** Expected
for diagrams, quizzes, one-pagers and battle cards — there is no viewer for them
yet. Ask Renn to list your artifacts to confirm it exists, or ask it to attach
the content to a card draft where you can read it. Decks are the exception and
should show up in the PowerPoint tab.

**Renn offers a kind of content not in the list of four.** Only diagrams,
quizzes, one-pagers, battle cards and decks exist. Anything else is invented —
flag it.
