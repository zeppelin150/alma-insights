---
id: kb-searching
title: Searching the knowledge base
section: knowledge-base
section_title: The knowledge base
section_order: 6
order: 4
status: partial
features: [kb_search, kb_list_topics, kb_list_cards, kb_get_card]
summary: Search ranks the index cards and falls back to the full text of indexed documents; there are no embeddings, and multi-word phrases have a known weakness.
last_verified: 2026-07-20
---

Ask Renn to search the knowledge base and it runs a ranked query over the index
cards, then falls back to the full text of the documents behind them. There is
no semantic model anywhere in that path, by decision.

## How it works

Ranking happens in two layers.

**The card layer.** Your query is matched against every card, with the title
weighted most heavily, then the topics and key facts, then the summary and
body. The score also rewards a card that covers more of your query terms, a
match in the title, and a more recently updated source.

Before matching, your query is expanded in code — plurals are folded to
singulars, and payer and product-area names are expanded to their known
aliases. So a query using one name for a payer can still find a card that used
a different one.

**The full-text floor.** If the card layer returns fewer results than asked
for, the search falls through to the stored full text of every indexed
document and returns a snippet from around the hit. This exists so a specific
fact buried on page thirty of a deck is still findable even though the card's
two-sentence summary never mentioned it.

Cards whose source document has gone missing are excluded from the ranked
results.

Two other ways in, when ranking is the wrong tool: Renn can list every topic
with its card count, and list every card inside one topic. Those enumerate
completely rather than ranking, so use them when you want to know what exists
rather than what matches.

**There are no embeddings and no vector search.** This was chosen deliberately
for the enablement side of the app, so that why a result ranked where it did is
explainable and reproducible rather than a similarity score nobody can inspect.

## How it should work

A distinctive single word from a card — a payer name, a product term, an
unusual noun — should surface that card. A query that matches nothing in the
cards should still surface a snippet from a document that contains the phrase.

Results should quote something real from the card or document. Renn summarising
a result it did not actually retrieve is a bug worth flagging.

The full-text fallback handles your words individually. "eligibility recheck
timeline" surfaces a document that contains those words even when they are
scattered across the text — the fallback tokenizes and ranks your terms (with a
relevance floor, so an off-topic query still returns nothing) rather than
requiring the whole phrase to appear as one continuous string. The card layer
already worked this way, so both layers now agree.

If a multi-word question still comes up empty, it is usually a genuine miss;
search the most distinctive **single** word to confirm the content is there.

## If it doesn't

**A long, natural-language question returns nothing.** With both search layers
tokenizing, this is usually a genuine miss rather than a phrasing problem. Re-run
with the two or three most distinctive words; if a single word surfaces the
content, the longer question was simply too specific, not blocked by the search.

**Renn says the knowledge base is empty.** Nothing has been indexed yet, or the
local copy has not synced. Ask Renn to list the topics — an empty list confirms
it. See *Turning the KB on and bootstrapping the EC folder*.

**A card you can read in Drive never appears in results.** Check its header
parsed correctly; a card flagged as needing repair may have lost the fields
that ranking depends on. See *Index cards: the format, and editing one by
hand*.

**Results look stale compared to Drive.** Search runs against the local copy,
not Drive directly, so a recent Drive edit needs a sync pass first. See *Sync
rules: Drive always wins*.

**Renn cites a card that does not exist.** Every result should correspond to a
real file. A fabricated card title is a serious bug — flag it with the query
you used.
