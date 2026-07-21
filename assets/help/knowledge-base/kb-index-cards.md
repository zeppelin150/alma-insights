---
id: kb-index-cards
title: "Index cards: the format, and editing one by hand"
section: knowledge-base
section_title: The knowledge base
section_order: 6
order: 2
status: available
features: [kb, kb_get_card, kb_list_cards]
summary: Every card is a markdown file with a structured header; you can edit one directly in Drive and the app will absorb your changes.
last_verified: 2026-07-20
---

A card is a plain markdown file. You are allowed to edit it in Drive, and you
are allowed to drop your own markdown file into a topic folder and have it
treated as a card. The parser is built to tolerate humans.

## How it works

Each card file is named after its title with a short identifier attached, like
`eligibility-recheck-policy--kb-3f9a2c17.md`. The identifier half is what the
app matches on, so renaming the title half is safe.

At the top of the file is a block of fields between two `---` lines:

| Field | What it is |
|-------|-----------|
| `card_id` | The stable identifier. Do not change it. |
| `title` | What the card is called |
| `type` | What kind of card it is, usually a source summary |
| `topics` | Short topic slugs |
| `summary` | One or two sentences — this is what search shows you |
| `key_facts` | Specific facts worth finding later |
| `source_id`, `source_url` | The document this card was built from |
| `source_modified` | When that source last changed |

Below the header is the body, which is ordinary markdown. When a source
document changes, the app appends a dated line under a `## Changelog` heading
in the body rather than silently overwriting the card.

The app tolerates — and keeps — a field it does not recognise. When it reads a
card it stores your own header fields alongside the ones it manages, and when it
writes the card back to Drive it merges them into the header again. So a field
you add by hand survives the app's rewrites, not just its reads. The body is
preserved word for word too, so a longer note still reads better there.

## How it should work

Edit the summary, the key facts, the topics, or the body freely. On the next
sync your version is read back into the app and becomes what search returns.
Your edit wins over the app's copy — see *Sync rules: Drive always wins*.

If you break the header — a stray quote, a bad indent — the app does not fail
and does not repair it. The card is marked as needing repair and is left
exactly as you wrote it. The app never rewrites a file it did not create, so a
card you dropped in by hand stays yours.

Two things to leave alone:

- **`_index.md`** is not a card. It is regenerated from scratch after each sync
  and says so in its own text. Anything you write there is overwritten. Edit
  the cards instead.
- **`card_id`** is how the app matches your file to its record. If you remove
  it, the app falls back to the identifier in the filename; if that is gone
  too, the file is treated as a brand new card.

Very large files are truncated rather than loaded whole, so a card is not the
right place to paste an entire document. Point at the document instead.

## If it doesn't

**Your edit disappeared and the app's wording came back.** That should not
happen — the rule is that the Drive version wins. Flag it with the card title
and roughly when you edited it.

**A card is flagged as needing repair.** The header did not parse. Open the
file, check the block between the `---` lines for a stray quote or a mis-typed
list, and fix it. The app will not fix it for you and will not overwrite your
file.

**A card shows up twice.** Editing a card in place does not cause this. Even
if you strip the identifier from both the header and the filename, the app
still recognises the file it already knows and keeps it as the same card.
Duplication comes from a *copy*: when you duplicate a card file in Drive, the
copy gets a fresh identity the app has never seen, and if that copy carries no
`--kb-` identifier in its header or filename it is read as a brand new card
while the original stays. Delete the duplicate in Drive; the local copy drops
it on the next full pass. To avoid it, edit the original file rather than
copying it, or keep the `--kb-` identifier on any copy you do make.

**You renamed a card and it vanished from search.** Check the filename still
ends with the `--kb-` identifier and `.md`. Files that do not end in `.md` are
skipped entirely.

**A field you added to the header disappeared.** That should not happen — the
app keeps custom header fields across its rewrites now, merging them back in
each time it saves the card. If one vanished, flag it with the card title and
the name of the field.
