---
id: reference-glossary
title: Glossary
section: reference
section_title: Reference
section_order: 10
order: 1
status: available
features: [en_workbench, en_agent, renn]
summary: Plain definitions of the words this app uses for your content — card, draft, artifact, index card, EC folder, and the rest.
last_verified: 2026-07-20
---

The enablement side uses a small number of words in a specific way. If a term
here does not match what you see on screen, the article is wrong and worth
flagging.

## How it works

**Card** — a published Guru knowledge card. This is the finished thing your
colleagues read. Cards live in Guru, not in this app; the app reads them and
writes updates back to them.

**Draft** — an unpublished working copy of a card, held inside the app. Drafts
are where all editing happens. A draft can be brand new, or it can be pointed
at an existing card so that publishing updates that card instead of creating a
new one. Nothing in a draft reaches Guru until you publish.

**Workspace** — one open draft in the Workbench, shown as a chip you can
switch between. Each workspace keeps its own unsaved edits, view and cursor
position, so switching away and back does not lose your place. Closing a
workspace closes the chip, not the draft.

**Artifact** — a generated piece of content that is not a card: a Mermaid
diagram, a knowledge-check quiz, a branded .pptx deck, a one-pager, or a
battle card. Artifacts are produced by asking Renn and are stored in the app.
A sixth kind, podcast, is reserved in the data model with no implementation
behind it — nothing generates one. Artifacts have no dedicated tab; you make
them by asking Renn. See *What Renn can do, and what Renn will refuse*.

**Index card** — a short markdown summary of one source document, written by
the app into your knowledge base. Indexing a Drive folder produces one index
card per document, each holding a two-sentence summary, a handful of key
facts, and topic tags. The full extracted text of the document is also kept
locally, so a detail the summary omitted is still findable.

**EC folder** — the Google Drive folder named "EC" that the app owns and
writes into. It holds the index cards, per-topic `_index.md` head files, and
archived copies of published cards. It is the single allowlisted destination
for the app's own Drive writes: the app will not write index cards anywhere
else. It has to be bootstrapped once before indexing or artifact upload will
work.

**Confirm card** — the approval prompt that appears when Renn proposes a
change outside the app. Renn cannot perform the write; your click does. See
*Why Renn never writes on its own — the Confirm card*.

**Attention queue** — the list of cards the app thinks need looking at, based
on Guru analytics such as staleness and low engagement. Each row offers a way
to open a targeted update, or to dismiss the row.

**Targeted update** — a revision pass that changes only the parts of a card
affected by a source document, leaving the rest of the card and its formatting
alone. This is the difference between updating a card and rewriting it.

**Style guide** — a document you designate as the house voice. Renn reads it
when drafting and revising so that generated text sounds like your team's
writing. One style guide is active at a time.

**Card template** — a separate designated document describing the expected
structure of a card, such as which sections appear and in what order. The
style guide governs how it reads; the template governs how it is laid out.

**TRC** — a ticket root cause code. This is a product-analytics term used on
the ticket side of the app. It does not appear anywhere in the enablement
workspace, so you should not expect to see it in the Workbench, Calendar,
Tasks or Help.

## How it should work

The words above should be the words on screen. Where a term names something
you can point at — a workspace chip, an attention-queue row, a Confirm card —
the on-screen thing and the definition here should agree.

Two distinctions matter most in daily use, because getting them backwards
causes real mistakes:

| Term | Lives where | Reaches colleagues |
|------|-------------|--------------------|
| Draft | In the app | Only after you publish |
| Card | In Guru | Immediately |
| Artifact | In the app | Only after you upload it |
| Index card | In the EC folder | As soon as it syncs |

Index cards are the exception worth remembering: the knowledge base writes
them into the EC folder without a Confirm card, because that folder is an
allowlisted destination the app owns. Everything else that leaves the app
waits for your click.

## If it doesn't

**A term here does not match the label on screen.** Trust the screen and flag
the article. Help text is bundled content, so a wording correction is quick to
make.

**You cannot find the EC folder.** It may not have been bootstrapped yet. Renn
will say so plainly rather than fail silently — the message names the EC
folder specifically. If it has been bootstrapped and you still cannot find it,
check whether it was moved or trashed in Drive, because a trashed folder still
accepts writes into the trash.

**A search for a term returns nothing even though you know the content
exists.** Several search paths match your whole phrase as a single string
rather than as separate words, so a multi-word query can return nothing while
each individual word returns results. Try one distinctive word instead. This
is a known limitation, not a fault in your setup.

**You see TRC or other ticket-analytics vocabulary in an enablement tab.**
That would be unexpected — the two sides are separate. Flag it with a note of
which tab you were in.
