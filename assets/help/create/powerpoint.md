---
id: create-powerpoint
title: "PowerPoint: modelling a deck"
section: create
section_title: Create — Decks, Zendesk and the Content Studio
section_order: 5
order: 1
status: partial
features: [en_pptx]
summary: Build a slide deck two ways — describe a topic and let the model draft it, or drag a document in and turn its headings into slides. A format the reader can't read locally is refused with a clear error rather than modelled into gibberish.
last_verified: 2026-07-20
---

The PowerPoint tab models decks as an editable outline first and a .pptx file
second. Nothing is written to disk until you export, so modelling is cheap and
you can throw a deck away.

## How it works

There are two routes into a new deck, and they behave differently.

**From a topic.** Type what the deck is about into the field at the top of the
tab and start it. This calls the language model, so it needs a working model
connection. The status line reads that it is modelling while it runs, then the
new deck appears in the list on the left and opens.

**From a document.** Drag a file onto the tab. This route uses no language
model at all. The document is read, its formatting resolved, and its structure
turned into slides mechanically:

| In the document | Becomes |
|---|---|
| Any heading | A new slide, titled with that heading |
| A bullet or numbered item | A bullet on the current slide |
| A short paragraph | A bullet on the current slide |
| Tables and raw markup | Skipped |

The document route caps each slide at eight bullets and trims any single
bullet to roughly 160 characters. A cover slide carrying the document's name
goes first, and empty slides are dropped.

The reader handles Word (.docx), HTML, and plain-text formats (.txt, .md, .csv,
.json and similar). A file in a format it cannot read locally — a PDF, a slide
deck, an image — is refused rather than modelled: the tab reports it couldn't
model a deck and writes nothing. It catches these two ways, by the file's type
and by sniffing the bytes, so a binary file with a misleading name (a PDF saved
as .txt) is turned away too. To turn one of these into a deck, convert the
source to Word or plain text and drag it in again, or connect Google Drive to
extract it.

The list on the left shows every deck with its slide count. Decks Renn
generates for you also land here — see *The Content Studio has no screen yet*.

## How it should work

The document route should work with no model connection and no network. If
dragging a document in fails while a topic works, that is backwards.

The document route should also be faithful. It invents nothing — every bullet
comes from a line in your file. If you see content in the deck that is not in
the source document, that is a bug worth flagging.

A new deck should appear in the list and open immediately, with the status line
naming it and its slide count. It is saved as a draft at that point; nothing
has been exported.

## If it doesn't

**Modelling from a topic fails and mentions a missing model client.** The
language model is not configured or not reachable. Check your model settings.
In the meantime the document route still works — drag a file in instead.

**Dragging a file in reports it could not be read.** The path is not a readable
file. Copy the file locally first rather than dragging from a network or
cloud-synced location that has not downloaded it.

**You dragged in an unsupported format and it reports it couldn't model a
deck.** Expected. The reader refuses a format it cannot read locally — a PDF,
another slide deck, an image, or any binary file — rather than decoding its raw
bytes into a nonsense deck. Nothing is written. Convert the source to Word or
plain text and drag it in again, or build the slides by hand in the outline
editor — see *Editing the outline and exporting*. Connecting Google Drive lets
the reader extract more formats.

**The deck comes out as one slide.** The document had no headings and no lists,
so there was nothing to split on. Add headings to the source and drag it in
again, or switch to the outline editor and build the slides by hand — see
*Editing the outline and exporting*.

**Bullets are cut off mid-sentence.** Expected. The document route trims long
bullets and stops at eight per slide. Edit them in the outline.

**Nothing happens at all and the status line stays empty.** Flag it with what
you typed or dropped.
