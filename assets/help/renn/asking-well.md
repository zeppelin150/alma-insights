---
id: renn-asking-well
title: "Asking well: listing vs searching vs looking everywhere"
section: renn
section_title: Renn, your assistant
section_order: 2
order: 2
status: available
features: [renn, search_content, kb_search, list_guru_cards]
summary: Why "what's in this collection" and "do we have anything on X" get answered by different tools, and how to phrase each.
last_verified: 2026-07-20
---

Renn has three different shapes of lookup, and phrasing your question well
decides which one it uses. The difference matters: one of them is complete and
the others are ranked and may miss things.

## How it works

**Listing — complete enumeration.** When you want everything *in* a container,
Renn lists rather than searches. "What's in the Billing collection", "what
tasks are on the board", "list every card in that folder". Listing returns all
items and a total count.

**Searching one source — ranked, possibly partial.** When you want a topic
*within* one place: "find Guru cards about prior auth", "search Zendesk for
refund policy". These are query-ranked, so a low-relevance match can fall off
the end.

**Searching everywhere — the fan-out.** "Do we have anything on X anywhere"
sends one query across Guru, Zendesk and Drive at once and returns a single
merged list labelled by source. A source you have not connected is skipped and
reported, not treated as an error.

**The knowledge base first.** For product or policy questions, Renn checks the
knowledge base it maintains before going out to the live sources.

## How it should work

Use the container's name when you want completeness — "what's in", "list
everything", "all the cards in". Use topic words when you want relevance.

A disconnected source should produce a note ("Zendesk not connected"), never an
error and never silence.

Counts should be honest: a list result tells you how many items there are, so
if you asked for a folder's contents and got five items with no count, you are
looking at a search result rather than a list.

## If it doesn't

**You asked for everything in a collection and got a short, arbitrary-looking
subset.** Renn searched instead of listed. Rephrase with "list everything in…".
If it still searches, flag it with your exact wording — the phrasing that
misroutes is the useful part of the report.

**A source you have connected is reported as not connected.** Do not rely on
the "Test connections" button in the Settings page header — it is not
wired to anything and does nothing when clicked. Instead ask Renn directly,
"what's connected?" It reports your active Drive folders and their count, the
active Asana board, and the Guru publish target from the app's live settings.
If Renn's report shows the source as connected but a search still skips it,
flag it.

**Results look stale.** Ask Renn to run the monitor, which pulls fresh items
from your configured sources.

**A multi-word question returns nothing** when you know the content exists.
Document and knowledge-base search now handle your words individually, so this
is usually a genuine miss — try fewer, more distinctive words. One narrower path,
the Workbench draft search, still matches a whole phrase only when the words
appear together; see *Known issues and current limitations*.
