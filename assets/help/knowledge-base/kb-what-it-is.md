---
id: kb-what-it-is
title: What the knowledge base is and where it lives
section: knowledge-base
section_title: The knowledge base
section_order: 6
order: 1
status: partial
features: [kb, kb_search, index_drive_folder]
summary: The knowledge base is a folder of plain markdown index cards in your own Google Drive, mirrored locally so Renn can search it.
last_verified: 2026-07-20
---

The knowledge base is not a database you have to trust blindly. It is a folder
of ordinary markdown files in your own Google Drive. You can open it, read it,
and change it without the app being involved at all.

## How it works

The app creates a folder named **EC** in your Drive and treats it as the home
of the knowledge base. Inside it:

| What | Contents |
|------|----------|
| Topic subfolders | One per topic, holding the cards for that topic |
| `published-cards` | An archive of every card Renn publishes to Guru |
| `misc` | Overflow when the topic limit is reached |
| `_index.md` | An auto-written summary of each folder's contents |

Every card is a markdown file with a small block of structured fields at the
top — title, topics, a one-or-two sentence summary, key facts, and a pointer
back to the document it was built from. See *Index cards: the format, and
editing one by hand*.

The app keeps a local copy of every card so search is fast and works without a
round trip to Drive. Drive holds the truth; the local copy follows it. See
*Sync rules: Drive always wins*.

Cards get created two ways. You can ask Renn to index a Drive folder, which
reads the documents in it and writes one card per document. And when you
publish a card to Guru, a copy of the published content is archived into the
knowledge base automatically.

**Writes** are tightly scoped: the app only creates and edits files inside the
EC folder it owns, or files you explicitly picked.

**Reads** depend on which Drive connection your install uses, and the two are
quite different:

- **A service account** (the default). The app sees only what has been shared
  with that account. Nothing else in your Drive is visible to it.
- **Your own Google sign-in**, used by the in-chat connect flow and the folder
  picker. This grants read access across your Drive — it is your own account
  and your own consent, but it is broader than "only the folder I picked". The
  app reads the folders you point it at; the scope simply permits more.

If your install must be limited to explicitly-shared content, the service
account is the connection to use.

## How it should work

Writing inside the EC folder does not open a Confirm card, and that is
deliberate — EC is a folder the app owns, so it maintains it directly. Writes
anywhere else in Drive still wait for your click. See *Why Renn never writes on
its own*.

Everything in EC should be readable by a human with no tooling. If you open a
card in Drive and it looks like machine output you cannot follow, that is worth
flagging.

Renn will not read a Drive folder name back to you, including the ones inside
EC. It refers to topics by their topic name instead.

## If it doesn't

**There is no EC folder in your Drive.** Expected on a new install. The
knowledge base ships turned off and the folder is not created until you enable
it and run the setup step. See *Turning the KB on and bootstrapping the EC
folder*.

**The EC folder exists but is empty, or has only a marker file.** Setup ran but
nothing has been indexed yet. Ask Renn to index a Drive folder.

**Renn says the knowledge base is empty when you can see cards in Drive.** The
local copy is behind or the sync is not running. Check that Google is connected
for this session, then see *Sync rules: Drive always wins*.

**Cards appear in Drive but Renn cannot find them by searching.** Try
*Searching the knowledge base* first — the ranking has a known weakness with
multi-word phrases. If a single distinctive word from a card still returns
nothing, flag it with the card title.
