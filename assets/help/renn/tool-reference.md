---
id: renn-tool-reference
title: Renn capability reference
section: renn
section_title: Renn, your assistant
section_order: 2
order: 8
status: available
features: [renn, help_search]
summary: What each family of Renn's capabilities actually does, grouped by what you are trying to get done.
last_verified: 2026-07-20
---

You never need to name a tool — Renn chooses. This reference exists for when
you want to know what is actually possible, or to check whether something you
expected is really there.

## How it works

**Finding content**

| You want | What happens |
|---|---|
| Everything in a collection, folder or board | A complete listing with a total count |
| A topic within one source | A ranked search of that source |
| Anything on a topic, anywhere | One query fanned across Guru, Zendesk and Drive |
| A product or policy answer | The knowledge base is checked first |
| One document, in full | The document is fetched and read |

**Working on a card**

Read the current draft, apply an edit you describe, publish to Guru. Publishing
happens only when you explicitly ask. Renn can also import an existing Guru card
as an editable draft — publishing then updates that same card rather than
creating a duplicate.

**Choosing where to publish**

List collections, then list folders inside one, then publish straight into the
folder you name.

**Managing work**

List, create and update tasks; draft and toggle subtasks; update a task's
scratchpad.

**Style guides and templates**

Find them, read the active one so drafts follow it, and switch which one card
generation uses.

**Generating content**

Diagrams (Mermaid), knowledge-check quizzes, one-pagers, battle cards, and
branded decks. Each takes exactly one source. Generated artifacts can be
attached to a card draft, which you still review and approve before publishing.

**Setting up and reporting**

Discover your Asana projects and save a board config; open pickers for Google,
Drive, Asana and the Guru publish target; report what is currently connected;
pull fresh items from your sources.

**Proposing changes** — see *Why Renn never writes on its own*. Asana updates,
Guru folder creation and renaming, and Drive uploads are all proposals that
open a Confirm card.

**The help you are reading** — Renn can search this Help Center and quote it
back to you. Ask it a "how do I…" or "why can't I…" question about the app and
it looks the article up. Because every article carries an availability status,
Renn will tell you when a feature is only partly built, gated off, or not
available yet — rather than confidently describing something that does not run.
For questions about the app itself this is faster than opening the Help Center
by hand; for anything about your own content, Renn searches Guru, Zendesk,
Drive and the knowledge base instead.

## How it should work

Renn should reach for the *narrowest* capability that answers your question:
list when you want completeness, search when you want relevance, the knowledge
base before the live sources for product questions.

Every answer should be grounded in a result from that turn. Renn should not
name a document, task ID or card that did not come back from a tool.

Renn should use only these capabilities. If it mentions shell access, file
access, or browsing the web, something is wrong.

## If it doesn't

**Renn mentions a capability that is not on this list** — running code,
reading arbitrary files, browsing the web. Flag it with the transcript. It is
instructed to redirect to enablement work.

**A capability listed here does not work when asked for directly.** Check the
status badge on the relevant article first — some features are documented but
gated off. If the article says available and it does not work, flag it.

**Renn picks a slower or wronger tool repeatedly** for a phrasing you use often.
Worth flagging: the phrasing is the useful detail, since the fix is usually in
how the capability is described to the model.
