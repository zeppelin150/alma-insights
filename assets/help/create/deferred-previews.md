---
id: create-deferred-previews
title: "What is deferred: diagram previews, quiz previews, podcasts"
section: create
section_title: Create — Decks, Zendesk and the Content Studio
section_order: 5
order: 7
status: not-available
features: [generate_diagram, generate_quiz]
summary: Diagrams and quizzes are generated and stored but cannot be viewed in the app, and podcasts do not exist at all.
last_verified: 2026-07-20
---

Three things in this area are named but not built. Two are partly built and one
is a placeholder. Knowing which is which saves you looking for screens that are
not there.

## How it works

**Diagram previews are deferred.** Generating a diagram produces valid Mermaid
source and stores it. No picture is drawn. There is no viewer, no thumbnail, and
no render step anywhere in the app. Attaching a diagram to a card draft inserts
the diagram's **source text** in a fenced block, so the card carries the
definition rather than an image, and whether that ever becomes a picture depends
on what renders the card afterwards.

**Interactive quiz previews are deferred.** The quiz itself is real — questions,
choices, correct answers and explanations are generated and validated. What is
missing is the interactive card that would let you take the quiz inside the app.

There is a useful exception. Attaching a quiz to a card draft converts it into
plain static HTML with each answer behind a collapsible section, so a published
card is genuinely readable and usable as a knowledge check. The quiz is not lost
just because there is no in-app preview.

**Podcasts do not exist.** The word appears in exactly one place: a reserved
entry in the list of artifact kinds. There is no generator, no audio, no
text-to-speech, no player, and nothing that reads that entry. It marks where the
feature would attach if it were built. Nothing can create one, and asking for
one cannot work.

## How it should work

Renn should describe stored content honestly. For a diagram, it should say the
source is stored and will render when previews ship — not that you can view it,
and not offer to show it to you.

For a quiz, it should offer the route that works: attach it to a card draft,
review the draft, and publish it through the normal approval. See *The Content
Studio: diagrams, quizzes, one-pagers and battle cards*.

For a podcast, it should decline. The same rule applies here as in *Background
research is not available yet* — declining plainly is correct behaviour, and
promising delivery is not.

## If it doesn't

**Renn says "here is the diagram" as though something is displayed.** Nothing is
displayed. Ask it for the Mermaid source as text if you want to paste it
somewhere that renders it, and flag the wording.

**A published card shows raw diagram code instead of a picture.** Expected
today. The card carries the source text.

**Renn offers to make a podcast, or says one is being generated.** That cannot
happen — there is no implementation behind it. Flag it with what you asked and
what it said.

**You cannot find a quiz you generated.** There is no quiz screen. Ask Renn to
list your artifacts to confirm it exists, then attach it to a card draft to read
it.

**Quiz answers are visible without expanding them.** The answers are published
inside collapsible sections. If whatever is displaying the card does not support
those, everything shows at once. Worth flagging if you are publishing quizzes
people are meant to attempt.

**Renn promises a preview will appear shortly.** It will not. Nothing arrives
later — flag it.
