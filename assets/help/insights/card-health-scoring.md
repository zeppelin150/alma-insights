---
id: insights-card-health-scoring
title: How card health is scored
section: insights
section_title: Insights and the attention queue
section_order: 7
order: 4
status: available
features: [en_attention, home]
summary: The percentage on each attention-queue row is a weighted blend of four signals — verification freshness, demand, open comments and content health.
last_verified: 2026-07-20
---

Every row in the attention queue carries a percentage. It is a single health
score from 0 to 100 percent, where higher is healthier. The score decides the
order within a bucket; it does not decide which bucket a card lands in.

## How it works

Four components are measured, each scored from 0 to 1, then blended by fixed
weights:

| Component | Weight | Full marks when | Zero when |
|---|---|---|---|
| Verification freshness | 40% | Not past due | 30 days overdue or more |
| Demand | 25% | Around 50 views or more | Never viewed |
| Open comments | 15% | No open comments | Five or more open |
| Content health | 20% | No duplicate, no gap | Both a duplicate and a gap |

Freshness falls off in a straight line across those 30 days, so a card five
days overdue keeps most of its freshness and one a month overdue keeps none.
Open comments work the same way across five comments. Demand rises quickly at
first and then flattens, so the difference between 2 and 20 views matters far
more than the difference between 200 and 2,000. Content health starts full and
loses half for being a near-duplicate of another card and half for leaving a
document only partly covered.

The bucket is chosen separately, by the first thing that applies: a changed
source document, then an overdue verification, then a duplicate or gap. A card
with none of these is healthy and is not shown at all.

Staged drafts are not scored. They are pinned at zero so they sort to the top
of their own section.

## How it should work

Read the score as a queue position, not a grade. Its job is to order the rows
you already know need work.

**A low score does not always mean a bad card.** Demand is a quarter of the
weight, so a card nobody has viewed loses that quarter outright — a perfectly
fresh, uncommented, unduplicated card that has never been opened scores about
75 percent, while a heavily-used card in the same condition scores 100. Two
cards with the same problem will rank by popularity, with the obscure one
first. That is deliberate: it surfaces content nobody is finding.

The corollary is that a very popular card can carry real problems and still
score respectably, because demand is propping it up. If a card is in the queue
at all, something is wrong with it regardless of the number beside it.

Duplicate and gap detection compares the wording of cards and documents, so it
finds things that are alike, not things that are identical. Near-misses in
both directions are normal at the edges.

## If it doesn't

**Two cards look equally bad but score very differently.** Usually demand. The
higher-scoring one is being viewed more. Compare the reasons in the bucket
heading rather than the percentages.

**A card scores well but is in the queue anyway.** Expected — the bucket is
assigned independently of the score. The bucket is the reason; the score is
only the ordering.

**Everything scores in a narrow band.** With no usage data synced, demand is
zero for every card and the scores compress into the top three-quarters.
Pull fresh analytics from Guru and the spread returns. See *Guru Analytics:
filters, refresh and the expand overlay*.

**A card is flagged as a duplicate and clearly is not.** The comparison is
based on title, summary and topics, so two cards written in very similar
language about different subjects can trip it. Worth flagging with both card
titles if it happens repeatedly.

**A score changes when you did not touch the card.** Normal. Views accumulate,
comments open and close, and the days past due keep climbing, so scores drift
between refreshes on their own.

**A real Guru card shows zero.** A zero on its own is not proof of a bug. A
staged draft is pinned at zero, but a genuine card also reaches exactly zero
when it is worst-case on every signal at once — 30 or more days overdue, never
viewed, five or more open comments, and both a duplicate and a gap. Before you
flag it, check whether the card really is that bad: if even one of those is not
true, the zero is wrong and worth flagging with the card title. For what the
buckets themselves mean, see *The attention queue: card health buckets*.
