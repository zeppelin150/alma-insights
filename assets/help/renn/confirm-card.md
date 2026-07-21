---
id: renn-confirm-card
title: Why Renn never writes on its own — the Confirm card
section: renn
section_title: Renn, your assistant
section_order: 2
order: 3
status: available
features: [renn, request_asana_task_update, request_create_guru_folder, request_upload_artifact_to_drive]
summary: Every change to Asana, Guru folders or Drive waits for your click. Renn proposes; you execute.
last_verified: 2026-07-20
---

Renn has no ability to change anything outside the app on its own. There is no
direct-execute write tool. When Renn wants to make a change, it *proposes* one
and a Confirm card appears; your click is what performs the write.

## How it works

Ask for a change — "mark that task complete", "create a folder called Onboarding
in the Billing collection", "upload that deck to the knowledge base folder" —
and Renn opens a Confirm card describing exactly what will happen. Nothing has
happened yet at that point.

You click to confirm or cancel. Renn waits and does nothing further until you
decide. After you confirm, it continues; after you cancel, it does not retry
unless you ask.

Before the card even opens, the target is checked for feasibility. If the
destination cannot accept the change — a read-only, Guru-managed collection
cannot take a new folder, and publishing there will also fail — Renn tells you
plainly instead of opening a Confirm card for something doomed to fail.

## How it should work

The Confirm card should name the specific thing being changed, not a generic
"apply changes". You should always be able to cancel, and cancelling should
leave nothing behind.

Renn should stop talking and wait while a card is open. It should not queue up
further actions or claim the change is done before you click.

Two things have no Confirm card and that is intentional: drafting and revising
a card draft (nothing leaves the app until you publish), and the knowledge base
writing its own index cards into your EC folder (that folder is an allowlisted
destination the app owns).

One thing is impossible by design: **there is no way to delete a Guru folder
from this app.** If you need to delete one, do it in the Guru web app.

## If it doesn't

**Renn says a change is done but nothing happened.** Check whether a Confirm
card is still waiting. If Renn claimed success with no card and no change, flag
it — that is a serious bug and worth reporting with the exact request.

**The Confirm card describes something different from what you asked.** Cancel
it. Do not confirm a card you do not recognise. Flag it with both your request
and what the card said.

**You confirmed and nothing changed.** The write may have failed after
approval. Check the source system directly. If Asana rejects the change because
the task moved underneath you, you will see a "task changed — refreshed, please
retry" message, which is expected. Anything else, flag it.

**A Confirm card will not close.** Cancel it and restart the conversation. If
the card is stuck, that is worth flagging with a screenshot of the card.
