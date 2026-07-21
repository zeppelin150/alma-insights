---
id: create-content-studio
title: "The Content Studio: diagrams, quizzes, one-pagers and battle cards"
section: create
section_title: Create — Decks, Zendesk and the Content Studio
section_order: 5
order: 5
status: partial
features: [generate_diagram, generate_quiz, generate_doc, generate_deck, attach_artifact_to_draft, list_artifacts]
summary: Four generators that each take exactly one source and produce something you review before it goes anywhere.
last_verified: 2026-07-20
---

The Content Studio is a set of generators Renn runs for you. It has no screen of
its own — see *The Content Studio has no screen yet* for how to reach it.

## How it works

Four things can be generated:

| Generator | Produces | Notes |
|---|---|---|
| Diagram | Mermaid source | Best for process-shaped content |
| Quiz | A knowledge check | Two to twelve questions |
| One-pager | A structured summary | Fixed section headings |
| Battle card | Positioning and objections | Fixed section headings |
| Deck | A real .pptx file | Runs as a tracked job |

**Every generator takes exactly one source.** A task, a stored document, or text
you paste in. Not two, not zero. If you name several, Renn is told to come back
and ask which one you meant rather than guessing. Research is also offered as a
source type, but there is nothing to draw on — see *Background research is not
available yet*.

Output is checked by code, not by the model's own judgement, and one repair
attempt is made before giving up. A diagram must lint as valid Mermaid, and
anything that looks like embedded script is rejected outright. A quiz must have
at least two choices and at least one correct answer per question. A one-pager
or battle card must contain all of its required section headings. If the second
attempt still fails, you get an error rather than a broken artifact.

Decks are different in one respect: if the model cannot produce a usable outline,
the deck falls back to a mechanical heading-based outline rather than failing, so
you still get a file. Deck generation reports progress as a job you can watch.

**Anything you generate can be attached to a card draft.** Attaching appends the
content to the draft and marks the artifact attached. It does not publish. The
draft still goes through the normal review and approval before anything reaches
Guru. Decks are the exception — they do not attach to cards; a rendered deck
goes to Drive through a Confirm card instead.

## How it should work

Attaching should never publish. If Renn attaches something to a draft and then
tells you the card is live, that is wrong — see *Why Renn never writes on its
own — the Confirm card*.

Uploading a deck should always show a Confirm card naming the file, and it
should target the knowledge-base folder or a folder you name — never whichever
folder happens to be active.

Generated content should be traceable to its source. Each artifact records what
it was built from.

## If it doesn't

**Renn asks you to pass exactly one source.** Name a single document, task, or
block of text and try again.

**Generation fails saying no model is available.** The language model is not
configured or not reachable. Nothing is created; there is no half-made artifact
to clean up.

**Generation fails validation twice.** The model could not produce output in the
required shape. Shorten the source, simplify the instruction, or ask for fewer
quiz questions.

**Renn says a deck cannot be attached to a card.** Correct. Ask it to upload the
deck instead.

**Uploading says the artifact has not been rendered.** Only decks produce a file
today. Diagrams, quizzes and docs are stored as content, not as files — see
*What is deferred: diagram previews, quiz previews, podcasts*.

**Uploading says no knowledge-base folder is set up.** The destination folder
has not been configured yet. Set it up first, or give Renn an explicit folder.

**Renn claims it published something you never approved.** Flag it immediately
with the exact wording.
