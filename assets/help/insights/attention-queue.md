---
id: insights-attention-queue
title: "The attention queue: card health buckets"
section: insights
section_title: Insights and the attention queue
section_order: 7
order: 3
status: available
features: [en_attention, home]
summary: A ranked list of the cards that need work, grouped into four buckets, with an action to start an update and a dismiss that hides a row until you refresh.
last_verified: 2026-07-20
---

The attention queue is the enablement landing page and the shortest route from
"something is wrong with our content" to "I am editing it". It scores your
Guru cards, keeps the ones that need work, and groups them by what is wrong.

It has its own sidebar entry, so you can return to it after navigating away.

## How it works

Opening the page starts a scoring pass in the background — you see a computing
message first, then the buckets. Refreshing recomputes from scratch.

Cards are grouped into four sections, each with a count:

| Bucket | What it means |
|---|---|
| Source changed | A source document on the card's topic is newer than the card |
| Verification overdue | The card is past its next verification date |
| Gap / duplicate | The card duplicates another, or half-covers a document |
| Staged drafts awaiting review | Your own pending drafts, not yet published |

**A card appears in one bucket only** — the worst thing standing against it,
in the order above. A card that is both overdue and duplicated shows under
verification overdue and nowhere else.

Within a bucket, the worst score sorts to the top. Each row shows the card
title, its health score as a percentage, and two actions. Staged drafts carry
no score of their own, so they sit at the top of their section.

**Start an update.** The first action moves you to the Workbench and opens the
card. A real Guru card is imported as a draft first; a staged draft opens
directly, since it already exists locally.

**Dismiss.** The second action hides the row so you can clear things you have
judged and are not going to act on. It means "not now": the row stays gone as
you move around the app, and comes back the next time you Refresh.

Cards that score as healthy are never displayed. When nothing qualifies, the
page says so rather than showing empty sections.

## How it should work

The queue should be short. It is a work list, not an inventory — if every card
you own is listed, the underlying signals are miscalibrated rather than your
library being uniformly bad. See *How card health is scored* for what each
number is made of.

Dismiss quiets a row for now, not forever. The dismissal is held in memory and
is not written down, so it survives you switching to another tab and back — but
it is cleared by Refresh. Refreshing recomputes the queue from scratch, and a
row you dismissed comes back with everything else. Treat dismiss as "hide this
until I next Refresh", not "I have dealt with this permanently": nothing about a
dismiss reaches Guru or changes a card's health, so the row will re-surface on
the next scoring pass anyway. This makes Refresh the recovery path — if you
dismiss a row by mistake, Refresh brings it straight back.

Both actions should be instant. Neither writes anything to Guru — starting an
update produces a local draft, and publishing stays a separate, deliberate
step.

## If it doesn't

**The page asks you to connect Guru.** Scoring reads live Guru data. Without
saved credentials there is nothing to score. Add them in Settings and refresh.

**A row you dismissed came back.** You Refreshed, or the queue recomputed —
Refresh clears every dismissal, so all dismissed rows return alongside the rest.
Expected, for the reason above. If the same rows keep coming back across
Refreshes without you dismissing them, that is the scoring pass telling you the
cards genuinely still need work.

**You dismissed a row by mistake and want it back.** Refresh the page. A
Refresh clears dismissals and rebuilds the queue, so the row returns with
everything else. Switching to another tab and back will not bring it back on its
own — only a Refresh does.

**The computing message never resolves.** The scoring pass runs in the
background and compares every card against your document catalog, so a large
library takes a while on first run. If it sits there across a refresh and an
app restart, flag it.

**The source-changed and gap/duplicate buckets are always empty.** Both are
computed by comparing cards against documents the app has catalogued. With an
empty or very small catalog neither can ever trigger, and only overdue cards
and staged drafts will appear. That is expected until the catalog is populated.

**A card you know is stale never appears.** Only cards in Guru's verification
queue are scored. A card that carries no verification state is invisible to
this page. Check it in Guru first; if it is in the queue there and still never
surfaces here, flag it with the card title.

**An error message appears in place of the buckets.** The page reports the
failure rather than crashing. Refresh once; if the same message returns, flag
it with the exact wording.
