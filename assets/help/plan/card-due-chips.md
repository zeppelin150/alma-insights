---
id: plan-card-due-chips
title: "Card-due chips: why Guru cards appear on your calendar"
section: plan
section_title: Plan — Calendar and Tasks
section_order: 3
order: 2
status: available
features: [en_calendar, en_workbench]
summary: Guru cards that need reverification or have gone stale appear on the Calendar as chips; clicking one imports that card as an editable draft.
last_verified: 2026-07-20
---

Not everything on your Calendar is a task. Cards in your Guru knowledge base
that are due for attention are added to the grid as their own chips, titled
"Card due:" followed by the card name. They are generated from card health,
not from anything in Asana, and nobody created them.

## How it works

Three things put a card on the calendar:

| Reason | What it means |
|---|---|
| Unverified | The card is marked as needing verification, or stale |
| Verification due | Its verification date falls within the next two weeks |
| Stale but popular | It is one of your most-viewed cards and has not been modified in about three months |

A card qualifying for more than one reason appears once. The list is capped,
so it is a queue of the most pressing cards rather than an exhaustive audit.

**Clicking a card chip does not open a task panel.** It starts a targeted
update: the card is pulled out of Guru and written into the Workbench as an
editable draft, and you land on the Workbench tab. Publishing that draft later
updates the same card rather than creating a new one.

The import runs in the background, so there is a pause between the click and
the Workbench filling in. The status line in the page header tracks it.

## How it should work

A card chip should always end with you in the Workbench holding a draft of
that card — with the current content, not an empty page.

Card chips are read-only on the Calendar. They cannot be rescheduled, and
nothing about clicking one changes the card in Guru. The change only happens
when you publish the draft, which is a separate deliberate step.

Once you have updated and published the card, its chip should stop appearing
after the card health data is next refreshed. It will not vanish the instant
you publish.

The attention queue on the Home tab covers similar ground, but it is not the
same list. The Calendar's card chips are generated from card health stored in
this database and need no live connection. The Home attention queue is computed
from the live Guru API and shows nothing at all until Guru is connected. So the
two can disagree: with stored card health present but Guru not connected, the
Calendar shows card chips while the Home queue is empty. That is expected, not a
sign that either one is broken. When Guru is connected they line up, and a card
showing in both places is one card surfaced twice, not two pieces of work.

## If it doesn't

**Nothing happens when you click a card chip, and the status line says to
connect Guru.** Guru is not connected. Card chips can be *generated* from
stored card health without a live connection, but importing the card needs
one. Connect Guru in Settings and try again.

**A task detail panel opens for a moment before the Workbench appears.** A
known cosmetic quirk of the click path. As long as you end up in the Workbench
with the right draft, nothing is wrong. If you end up stuck on the panel with
no draft, flag it.

**A card chip is missing from the "+N more" day list.** Expected — the day
expander lists tasks only, so card chips are counted in the "+N more" number
but not shown in the list. Use the month grid to reach the chip directly.

**No card chips appear at all.** Card health data has probably not been
collected into this database yet. This is not an error state on its own — a
calendar with no card chips is normal before Guru analytics have been
gathered.

**The draft that opens has different content from the card in Guru.** Do not
publish it. That would overwrite the live card with stale text. Flag it with
the card name.
