---
id: create-zendesk
title: "Zendesk: syncing and drafting articles"
section: create
section_title: Create — Decks, Zendesk and the Content Studio
section_order: 5
order: 3
status: partial
features: [en_zendesk]
summary: Sync pulls your Help Center into a local cache and writes nothing back; the article list shows drafts, and today there is no way to start one from this tab.
last_verified: 2026-07-20
---

The Zendesk tab has two halves: a one-way sync that reads your Help Center, and
a draft-and-push flow for articles. The two are less connected than they look.

## How it works

**Sync reads. It never writes.** Syncing pulls Help Center articles and account
macros into a local cache inside the app and refreshes rows already there.
Nothing goes to Zendesk during a sync, and no drafts are created from what it
finds. The status line reports how many of each it pulled.

Sync takes one page from each endpoint, so roughly the first hundred articles
and hundred macros rather than your whole Help Center.

**The list shows drafts, not synced articles.** The count beside the heading
comes from the cache; the list below it comes from pending drafts. Those are
different things, which is why a sync can report a healthy article count and
leave the list empty. That is the current behaviour, not a failure.

**Drafts are edited and pushed.** Selecting a draft loads its title and body
into the editor. Saving stores it locally. Pushing converts the body to HTML and
sends it: a draft already linked to a live article updates that article, and an
unlinked draft creates a new one. Pushed drafts leave the list, because the list
only shows pending ones.

Two limits are worth knowing before you rely on this:

- **Creating a new article needs a section**, and this tab has no way to choose
  one. An unlinked draft pushed against a live Zendesk returns an error saying a
  section is required.
- **Pushing with no Zendesk connection marks the draft pushed anyway.** No API
  call is attempted, the draft is flagged as pushed locally, and the button
  disables. "Pushed" is therefore not proof that anything reached Zendesk.

## How it should work

Syncing should be safe to run at any time. It is read-only, so it cannot damage
your Help Center, and re-running it just refreshes the cache.

Pushing should be the only action that leaves the app, and it should reflect
reality. When it says an article was pushed, that article should exist in
Zendesk with your title and body.

## If it doesn't

**The article list is empty after a successful sync.** Expected. Sync fills the
cache; the list shows drafts. They are separate.

**There is no way to start a new article draft.** This is a genuine gap in the
current build — nothing in this tab creates one, and the drafts you see in the
demo data were seeded rather than authored. Until that is wired up, draft the
content elsewhere. Worth flagging if you need it.

**Syncing says to connect Zendesk first.** No credentials are configured. Set
them up in settings, then sync again.

**Your Help Center has more articles than the count shown.** Expected — sync
takes the first page only. Flag it if the missing articles matter to you.

**Pushing fails saying a section is required.** The draft is not linked to an
existing article, so Zendesk needs to know where to file the new one. Create the
article in Zendesk first, or push against a draft that is already linked.

**Pushing fails with a Zendesk error.** The API rejected it. Check your
credentials and permissions, then flag it with the error text.

**The draft says pushed but Zendesk is unchanged.** Check whether Zendesk is
actually connected — a push with no connection marks the draft locally and
stops. If it is connected and the article still is not there, that is a real
bug; flag it with the draft title.
