---
id: workbench-bringing-documents-in
title: Bringing a document in
section: workbench
section_title: Create — Workbench
section_order: 4
order: 2
status: partial
features: [en_workbench, import_drive_doc, import_guru_card_to_draft]
summary: Three ways to start a draft — a local file, a Google Doc URL, or an existing Guru card — and only one of them uses AI.
last_verified: 2026-07-20
---

Everything in the Workbench starts as a draft built from something you already
have. There are three routes in, and they behave differently in a way worth
knowing about.

## How it works

**A local file.** Use the upload button, the drag-and-drop strip along the
bottom of the tools bar, or drop a file anywhere on the Workbench. The file
picker offers .docx, .md, .markdown, .txt and .csv, with an all-files fallback.
Re-importing the same file refreshes the one draft you already have rather than
making a duplicate: each local draft is keyed on the file's path, so uploading
the same file again — even after you have edited it on disk — resolves to the
same document and refreshes its single draft. A draft you have already published
to Guru is left alone.

A Word document is read structurally rather than flattened: heading styles,
bold, italic, strikethrough, bulleted and numbered lists with nesting,
hyperlinks and tables are resolved into markdown. A .md file passes through
untouched. HTML is converted. Anything else is read as text.

**This conversion is plain Python — no AI, no network, no provider call.**
Turning a document into a card is treated as a text transformation, not a
reasoning job, so it is fast, offline, repeatable, and never invents content.
AI polish is a separate, explicit step you ask for afterwards. See
*Editing: markdown, rich text and inline AI*.

**A Google Doc or Drive URL.** From the Tools menu, choose the import option
for a Drive document. You are asked for a Google Doc URL or a file id — both a
full docs.google.com link and a bare id work. The document is fetched and its
text exported, then a card is drafted from it.

Unlike the local-file route, **this path does call an LLM** to turn the document
into a card, and it applies your active style guide and card template. If the
model call fails, the document is still saved and you are told the auto-draft
was skipped.

**An existing Guru card.** From the same Tools menu, choose to import an
existing Guru card. A picker opens against your connected Guru account. The
card's HTML is converted to markdown so you can edit it, and — this is the
important part — the draft is stamped with that card's id, so publishing later
updates the same card instead of creating a duplicate. The original HTML is kept
alongside the markdown so an unedited round trip does not lose fidelity. See
*Publishing to Guru*.

Imports run off the main thread. The Workbench switches to the new draft and
focuses it when the import finishes.

## How it should work

An upload should produce a draft that reads like the source document, with its
headings and lists intact, and should not contain sentences that were not in
the original. A rollout or effective date in the source is surfaced as a
callout near the top.

Re-importing the same document refreshes rather than duplicates on both routes.
A Drive re-import keys on the file id, and the local-file route keys on the
file's path: fetching or uploading the same source twice refreshes the draft you
already have — unless it was already published, in which case the published one
is left alone. To start a genuinely separate draft from a local file, import a
differently-named copy. To edit a draft without re-importing at all, open it and
change it in place — see *Editing: markdown, rich text and inline AI*.

A Drive URL and a raw file id should be interchangeable. So should a Guru card
URL and a raw card id.

An import failure should tell you what failed — the fetch, the export, or the
draft — not fail silently.

## If it doesn't

**Nothing happens when you drop a file.** Check the file type. Unrecognised
text extensions are read as plain text, which usually still works, but a binary
format such as .pdf or .pptx is reported rather than silently converted — you
get a note that the format can't be read locally instead of a draft built from
decoded garbage. Convert it to text or markdown first, or connect Google Drive
to extract it.

**The card from a Word document lost its formatting.** The reader is
deliberately tolerant — an element it cannot parse is skipped rather than
crashing the import. Heavy tables, text boxes and embedded objects are the usual
casualties. Flag it with the document if the loss is severe.

**The Drive import says Drive read is not configured.** Drive reading is set up
in the enablement settings. Until it is configured, use the local-file route
instead: download the doc and drop it in.

**The Guru import says to connect Guru first.** The card picker needs a live
Guru connection. Connect Guru in settings, then retry.

**The upload produced no summary or rewriting.** Expected. The local-file route
is deterministic by design and does not use AI. Ask Renn to revise the draft, or
use the inline AI actions in the rich-text view.

**A card you imported from Guru publishes as a new duplicate card.** That is a
bug — the import is supposed to stamp the target card id. Flag it with the
original card and the duplicate.

**Re-uploading a file didn't show your change.** A re-upload refreshes the
existing draft in place — it does not make a second one. If you don't see your
edit, confirm you saved the file before re-importing, and that the draft wasn't
already published: a published draft is frozen and the re-import leaves it
alone. To make a genuinely separate draft, import a differently-named copy of
the file. The Drive-import route refreshes the same way, keyed on the file id.
