---
id: create-zendesk
title: "Zendesk: syncing and drafting articles"
section: create
section_title: Create — Decks, Zendesk and the Content Studio
section_order: 5
order: 3
status: partial
features: [en_zendesk]
summary: The Zendesk connection is one-way — the app reads your Help Center and can never write to it; drafts are edited locally and reach Zendesk only when a person copies them into the Zendesk editor.
last_verified: 2026-08-05
---

The Zendesk connection is **one-way**. Everything in this app reads from
Zendesk; nothing in it can write to Zendesk. That is a deliberate decision,
not a missing feature: a Help Center is a public site and its content is not
restorable the way Guru and Asana content is, so the app is not allowed to
touch it. Approved content gets there when **you** copy it and paste it into
Zendesk's own editor.

The tab has two halves: a sync that reads your Help Center, and a local
drafting flow for articles. The two are less connected than they look.

There is also a newer, flag-gated **web workspace** (see *The web workspace and
the local mirror* below) that replaces this tab with a high-fidelity replica of
Zendesk's own editors, working entirely out of a local mirror. Everything in
the next sections describes the classic tab, which remains the default.

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

**Drafts are edited and saved locally.** Selecting a draft loads its title and
body into the editor. Saving stores it in the app's own database. No editing
step contacts Zendesk.

**Finishing a draft is a local bookkeeping step.** The action that used to push
now only marks the draft handled and drops it out of the pending list. No API
call is made, nothing is sent, and your Help Center is unchanged. It records
that you have dealt with the draft — usually because you copied it into
Zendesk yourself.

**Copy and paste is the only route to Zendesk.** To publish, open the article
in Zendesk, paste in the reviewed title and body, and save it there. The app
deliberately has no button that does this for you.

## How it should work

Syncing should be safe to run at any time. It is read-only, so it cannot damage
your Help Center, and re-running it just refreshes the cache.

Nothing you do in this tab should ever change your live Help Center. Editing,
saving and marking a draft handled are all local, and a live Zendesk connection
does not change that — the connection can only read.

## If it doesn't

**The article list is empty after a successful sync.** Expected. Sync fills the
cache; the list shows drafts. They are separate.

**There is no way to start a new article draft from this tab.** Nothing in the
classic tab creates one, and the drafts you see in the demo data were seeded
rather than authored. Renn can, though: ask it to propose an article or macro
update and the draft lands in the local mirror with a rationale attached, ready
for review in the web workspace's Revision Center.

**Syncing says to connect Zendesk first.** No credentials are configured. Set
them up in settings, then sync again. Those credentials are only ever used to
read.

**Your Help Center has more articles than the count shown.** Expected — sync
takes the first page only. Flag it if the missing articles matter to you.

**A draft disappeared and Zendesk is unchanged.** Expected. Marking a draft
handled removes it from the pending list and contacts nothing. If you have not
pasted the content into Zendesk yet, the article there is still the old one.

**You are looking for a Push or Publish button.** There isn't one, anywhere in
the app, by design. If something appears to offer one, that is worth flagging.

## The web workspace and the local mirror

When the `enablement.web_tabs` setting includes `zendesk` (or is `all`), this
tab is replaced by a web workspace that replicates Zendesk's own Guide article
editor and Admin Center macro editor, working entirely out of a **local
mirror** instead of your live instance.

**Filling the mirror.** Two ways, both one-directional into the app:

- **Pull** fetches Help Center articles, sections, categories and macros over
  read-only API calls. It needs Zendesk credentials and reports not-connected
  without them. Unlike the classic sync, it follows pagination — your whole
  Help Center, not just the first page.
- **Import** ingests files you downloaded yourself: Zendesk API/export JSON
  (single objects, arrays, or envelopes), saved article HTML, or any document
  the app can read. Each file gets a per-file report; re-importing an unchanged
  file is recognised and skipped.

**Working in the mirror.** The article and macro editors look and behave like
Zendesk's, but every change stays local. Renn researches from your go-to-market
docs, ticket exports and Asana briefs, and proposes updates as **revisions** —
each with a rationale and its sources. You review the diff against the
mirrored original, then copy the content whenever you are ready — a native
confirmation dialog in the app window shows the exact bytes before anything
reaches the clipboard — paste it into real Zendesk yourself, and mark the
revision ready or copied to track your progress by hand. The pending → ready →
copied statuses are workflow bookkeeping, not a gate: a revision can be copied
at any of them.

**Nothing writes back.** Neither the web workspace nor Renn can touch your
Zendesk instance — there is deliberately no push, no publish, and no API write
anywhere in the app. The specialist's copy-and-paste is the only path from
mirror to live, which is the point.
