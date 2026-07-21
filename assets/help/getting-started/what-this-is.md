---
id: getting-started-what-this-is
title: What the enablement side is
section: getting-started
section_title: Getting started
section_order: 1
order: 1
status: available
features: [en_workbench, en_agent, en_attention]
summary: The enablement side turns product documents into Guru knowledge cards, and it never touches ticket or patient data.
last_verified: 2026-07-20
---

Alma Insights has two sides. The product side analyses support tickets. The
enablement side — the one this Help Center covers — is about content: taking
what the business already knows and keeping it correct inside Guru.

## How it works

The core loop is `detect → score → triage → draft → review → publish → measure`.
In practice that means:

1. **Detect** — a source document changes, or a card goes stale, or a gap shows
   up between what people ask and what is written down.
2. **Score and triage** — the affected cards are ranked so you work on the ones
   that matter first. That ranking is what the Attention Queue shows you.
3. **Draft** — a card draft is generated from the source document, following
   your style guide and card template.
4. **Review** — you read the draft, see the diff against the live card, and
   check the evidence behind each change.
5. **Publish** — you push it to Guru. Every publish is gated on a human. There
   is no path where content reaches Guru without someone approving it.
6. **Measure** — effectiveness is tracked Guru-natively, using views and
   comment activity on the card itself.

Renn, the assistant, drives most of this on your behalf. See
*What Renn can do, and what Renn will refuse*.

## How it should work

The single most important thing to understand is what the enablement side does
**not** touch.

Enablement works from the Guru live API and its own local enablement tables —
the content catalog, drafts, provenance, card health, signal snapshots. Ticket
and warehouse data is the product side's job, and nothing on this side is
built around it.

Be precise about what that separation is, though, because it is a matter of
**practice, not enforcement**. The assistant's tool set is not restricted to
enablement tools: the warehouse tools remain wired and, in live mode, point at
the real warehouse database. What makes the separation hold in practice is
that the enablement team does not use the product side, so there is nothing
there to read. Treat it as a working convention you should not rely on as a
security boundary.

So the following is what to expect day to day:

| You ask for | What happens |
|---|---|
| Ticket volumes or TRC trends | Not what this side is for — use the product side. |
| Anything involving patient data | Not part of any enablement workflow. |
| "Which cards need work" | Answered from Guru signals and card health. |
| "Did this card help" | Answered from Guru views and comments, not tickets. |

Because of that decoupling, effectiveness here is measured as a Guru-native
proxy. A card that gets more views and fewer open comments after a rewrite is
working. Nobody on this side is counting tickets.

## If it doesn't

**Renn answers a question about ticket volume.** This is not blocked in code,
so it is possible in principle — but on an enablement install there is no
ticket data to read, so a confident-looking answer is far more likely to be
invented than retrieved. Do not act on it. Flag it with the exact question you
asked and the answer you got, so the boundary can be tightened.

**Something published to Guru that you did not approve.** Every publish is
supposed to wait for you. If a card changed in Guru without you pushing it,
flag it immediately with the card name and roughly when it happened.

**You cannot find the ticket analytics you expected.** That is the product
side, not this one. Switch modes rather than looking for it here.

**The loop stalls at a step.** Each stage has its own article in this Help
Center — start with *Where everything lives* to find the surface that owns the
step you are stuck on.
