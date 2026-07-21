---
id: renn-pickers
title: "Pickers: connecting Drive, Asana and Guru mid-conversation"
section: renn
section_title: Renn, your assistant
section_order: 2
order: 4
status: available
features: [renn, request_google_connect, request_drive_picker, request_asana_board_picker, request_guru_publish_picker]
summary: How Renn opens a real picker in the app instead of asking you to paste an ID.
last_verified: 2026-07-20
---

You never have to look up a Drive folder ID or an Asana GID. When Renn needs to
know *where* something lives, it opens a picker in the app and waits for you to
choose.

## How it works

Four pickers exist, and Renn opens them from the conversation:

- **Connect Google** — starts the sign-in flow.
- **Drive folder** — choose the active folder Renn reads from.
- **Asana board** — choose the active board your tasks come from.
- **Guru publish target** — choose where cards get published.

The picker reads nothing on Renn's behalf. It is a piece of app UI; your
selection is what tells the app which destination to use. Renn stops entirely
while a picker is open and resumes once you have chosen.

Renn is also told your folder ID and never your folder *name*, which is why it
refers to "the active folder" rather than naming it.

## How it should work

Ask in plain language — "connect Google", "point yourself at our enablement
Drive folder", "which board should I use" — and the right picker opens.

You should never be asked to paste an ID. If Renn asks for one, that is a bug.

To see what is currently configured, just ask: "what's connected?" or "what's
set up?" Renn reports the active Drive folders and their count, the active
Asana board, and the Guru publish target.

## If it doesn't

**Renn asks you for a folder ID or GID.** Flag it, with the request that
triggered it. It should have opened a picker.

**The picker opens but the tree is empty.** For Drive, this usually means
Google is not connected in this session — connection is not persistent across
launches by design. Ask Renn to connect Google, then retry.

**The Drive picker has no search box.** That is correct today, and a known
limitation: you navigate the folder tree by expanding it. If you have many
folders this is slow. It is on the known-issues list.

**Renn keeps talking while a picker is open**, or acts before you have chosen.
Flag it — it is supposed to stop and wait.

**You picked a folder but Renn still says nothing is configured.** Ask "what's
connected?" to confirm. If the picker's selection did not stick, flag it with
the sequence you followed.
