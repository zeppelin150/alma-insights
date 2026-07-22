---
id: getting-started-content-command-center
title: The Content Command Center
section: getting-started
section_title: Getting started
section_order: 1
order: 2
status: available
features: [en_calendar, en_tasks, en_workbench, en_zendesk, en_attention, en_analytics]
summary: Content Command Center is the enablement workspace — Asana, Guru, and Zendesk connected in one review-to-publish workflow.
last_verified: 2026-07-22
---

Content Command Center is an AI-powered workspace that connects Asana, Guru, and Zendesk, allowing the Content team to review requests, analyze existing knowledge, generate recommended updates, and publish content through one centralized workflow.

That is the enablement side's working name. When the app opens in enablement
mode, the startup screen says **Content Command Center** rather than the
product side's "Alma Insights — RCM Issue Analysis", and the same name sits in
the top bar and on the banner at the top of Home. This article maps each part
of the sentence above onto the surface that does the work, so every claim has
a place you can click.

## How it works

**Connects Asana.** The Calendar and the Tasks board read from your configured
Asana boards. Changes flowing back the other way are gated: Renn can request a
task update, but nothing is written to Asana without your approval. Setup and
caveats live in the *Plan* section of this Help Center.

**Connects Guru.** Guru is where published knowledge lives. The Workbench
drafts against your live cards, the Attention Queue ranks the cards that need
work, and Guru Analytics reports card health. Every publish to Guru is gated
on a human — see *What the enablement side is*.

**Connects Zendesk.** The Zendesk tab syncs your Help Center articles and
macros into a local cache for reference, and article drafts written there can
be edited and pushed. The sync itself is read-only; see *Zendesk: syncing and
drafting articles* for the current limits.

**Review requests.** Incoming work — Asana requests, stale-card flags,
gaps — lands on the Tasks board and the Attention Queue, ranked so the items
that matter most surface first.

**Analyze existing knowledge.** Card health scoring, knowledge-base search,
and Guru Analytics answer "what do we already say, and is it still right"
before anything gets rewritten.

**Generate recommended updates.** Drafts are generated from source documents
against your style guide, with a diff against the live card and the evidence
behind each change, ready for your review.

**Publish through one centralized workflow.** The loop is
`detect → score → triage → draft → review → publish → measure`, and it runs
inside this one app. Publishing is always human-approved.

## How it should work

The name describes the workspace, not a separate product: everything above is
the enablement mode of Alma Insights, and the product side's ticket analytics
are unaffected by it. "AI-powered" concretely means Renn — the assistant that
drives detection, drafting, and research on your behalf — plus the generation
tools behind the Workbench and the Content Studio.

## If it doesn't

**The startup screen still says "RCM Issue Analysis".** Then the app opened in
product mode. The splash brands by the mode the app is opening into — switch
to enablement (the Home tiles do it) and the next launch will brand as the
Content Command Center.

**A connection in the sentence is not live for you.** Each of the three has
its own setup path and its own failure modes — start with *Your first 15
minutes*, which walks Guru, Google, and Asana in order and says which of them
needs an administrator.
