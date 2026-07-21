---
id: settings-guides
title: Managing style guides and card templates
section: settings
section_title: Settings and connections
section_order: 8
order: 8
status: available
features: [en_settings, style_guide, card_template]
summary: Paste, upload files, upload a folder, or pull from Drive — then keep a library and switch which document is active.
last_verified: 2026-07-20
---

The Style Guide tab holds two documents that shape every card the app generates.
The style guide sets tone and phrasing. The card template sets the heading
structure that generated cards reproduce exactly. They are separate documents
with identical controls.

## How it works

Each card offers five actions, and you can store several documents of each kind
while only one is active at a time.

| Action | What it does |
|--------|--------------|
| Paste | Opens a box pre-filled with the current text; saving replaces it |
| Upload | Pick one or more files; each is stored, the last becomes active |
| Upload folder | Stores every readable document in a folder |
| From Drive | Asks for a Google Doc link or file id and imports it |
| Clear | Deactivates the current document; it stays in the library |

Readable file types are markdown, plain text, Word documents and HTML. Word and
HTML files are converted to markdown as they are stored.

Below the actions, the active document shows as a rendered preview. There is an
edit control that flips the preview into a plain markdown editor, with save and
cancel. Below that is the library of stored documents, each showing its name and
character count, with the active one badged and the others offering to become
active. Every stored document can be deleted.

Pasting an empty string clears the document rather than storing a blank one.

**Clear does not delete anything.** It only drops the active pointer, so
generation stops following any document until you make one active again. The
document you cleared keeps its row and still appears in the library below, just
without the active badge — you can make it active again with one click. To remove
a document for good, use the Delete control on its library row instead.

Upload folder reads the folder you pick and every folder inside it — the search
is fully recursive, so documents in subfolders are collected too. The real limit
is a cap of 50 documents per folder pick; beyond that, the extras are ignored
rather than reported. If a folder holds more than 50 supported documents, upload
the ones you need in smaller batches.

The Drive option needs Drive reading configured and Google connected — it uses
the same Drive access as everything else. See *Connecting Google Drive*.

## How it should work

Uploading should acknowledge what it stored by name. Uploading several files at
once stores all of them and makes the last one active — which is worth knowing
if you expected to choose.

Making a different document active should change the preview immediately, and
the next card generated should follow the newly active document. Generation
follows the active document only; the rest of the library sits idle until you
switch.

Editing in place should save your changes and re-render. Cancelling should
restore the previous text and leave nothing stored.

A card template's job is exact heading structure. If your generated cards have
the right tone but the wrong headings, the template is the thing to change, not
the style guide.

## If it doesn't

**Uploading reports that nothing could be stored.** The files could not be read.
Check the file type — only markdown, text, Word and HTML are handled. A single
unreadable file among several is skipped silently; the error only appears when
nothing at all could be stored.

**A folder upload says there were no readable documents.** The folder and every
subfolder inside it contain no files of a supported type — only markdown, text,
Word and HTML are read. Subfolders are searched, so nesting is not the problem;
check the file types instead.

**A folder upload stored fewer documents than you expected.** Only the first 50
supported documents are kept per folder pick, and the extras are dropped without
a message. Split a large folder into smaller uploads if you need more than 50.

**The Drive import fails saying Drive read is not configured.** Either Google is
not connected this session or Drive reading is switched off for your install. See
*Connecting Google Drive*.

**Generated cards ignore your guide.** Check which document carries the active
badge — it is easy to upload a new one and then switch away from it. If the right
document is active and generation still ignores it, flag it with the card and the
guide.

**Your edit did not stick.** The preview updates optimistically before the save
completes. Leave the tab and come back; if the old text returns, the save failed
and is worth flagging.

**Asking Renn to find the document you imported returns nothing.** Document
search matches your whole phrase as a single run of text, so a multi-word query
often fails where a single distinctive word succeeds. Try one word. This is a
known limitation, not a missing document.

**A stored document will not delete.** Flag it with the document name.
