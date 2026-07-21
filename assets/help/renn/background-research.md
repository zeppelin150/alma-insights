---
id: renn-background-research
title: Background research is not available yet
section: renn
section_title: Renn, your assistant
section_order: 2
order: 7
status: not-available
features: [renn]
summary: Renn cannot run background research jobs in this build. Here is what it can do instead.
last_verified: 2026-07-20
---

If you ask Renn to "go research" a topic and come back with findings, it will
decline. That capability is not in this build.

## How it works

There are no background-research tools available to Renn. It cannot start a
research job, write a research manifest, or open a research-plan approval card.

Renn is explicitly instructed to say so rather than pretend. It should never
tell you research is running, queued, or will be delivered later — there is
nothing to deliver it.

## How it should work

When you ask for research, Renn should decline plainly and immediately offer
what it *can* do right now, which is search the content you already have:

- **Search everywhere at once** — one query across Guru, Zendesk and Drive.
- **Search the knowledge base** — the index cards Renn maintains for you.
- **Search one source deliberately** — Guru cards, Zendesk articles, Drive
  documents.
- **Read a specific document** in full so it can work from the actual text.

In practice, "research our position on X" becomes "search everywhere for X,
then summarise what we already have" — which is often what you wanted.

## If it doesn't

**Renn says research is running, queued, or in progress.** That is a
significant bug — there is no job to run and nothing will ever arrive. Flag it
with the exact wording Renn used.

**Renn promises to follow up later.** Same problem. It has no mechanism to come
back to you asynchronously on research. Flag it.

**Renn refuses a search that is not research.** If you asked it to look
something up in existing content and it declined as if you had asked for
research, flag it with your phrasing — that is a misclassification worth
fixing.
