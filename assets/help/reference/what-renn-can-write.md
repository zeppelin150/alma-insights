---
id: reference-what-renn-can-write
title: What Renn can write, and what always needs your click
section: reference
section_title: Reference
section_order: 10
order: 3
status: available
features: [renn, request_asana_task_update, request_create_guru_folder, request_rename_guru_folder, request_create_asana_task, request_upload_artifact_to_drive]
summary: The complete list of changes Renn can make on its own versus the five that stop and wait for your approval.
last_verified: 2026-07-20
---

This is the reference table behind
*Why Renn never writes on its own — the Confirm card*. If you want the
principle, read that article. If you want to know precisely which operation
falls on which side of the line, read this one.

## How it works

**Five operations open a Confirm card.** These are the only ways Renn can
change something outside the app, and none of them execute without your click.

| Operation | What it would change |
|-----------|----------------------|
| Create a Guru folder | A new folder in a collection |
| Rename a Guru folder | An existing folder's title |
| Create an Asana task | A new task on a board |
| Update an Asana task | Complete, reopen, due date, comment, subtask |
| Upload an artifact to Drive | A file written into a Drive folder — the EC folder by default, or another folder if one was named |

That list is enforced, not advisory. Any other write operation is rejected
before it can be dispatched, so a malformed or unexpected request cannot
smuggle itself through as one of these five.

**Four operations open a picker or a connect card.** These do not change
anything outside the app either — they hand you a chooser and wait.

| Operation | What you choose |
|-----------|-----------------|
| Connect Google | Authorising Drive read access |
| Drive folder picker | The active folder |
| Asana board picker | The active board |
| Guru publish target picker | The collection, and optionally the folder |

**Everything else Renn does stays inside the app** and needs no confirmation:
searching your content, reading a document, creating and revising a card
draft, generating an artifact, listing tasks, updating an enablement task,
writing a task scratchpad, and tracking a multi-step job.

**Two writes are deliberately un-gated.** Publishing a draft to Guru runs when
you ask for it in so many words, because asking to publish *is* the approval.
And the knowledge base writes its own index cards into the EC folder without a
card, because that folder is an allowlisted destination the app owns.

## How it should work

Renn should stop and wait after opening a Confirm card. It should not describe
the change as done, queue further actions behind it, or retry after you
cancel.

The card should name the specific thing being changed. You should be able to
read it and recognise your own request in it.

For an artifact upload in particular, the card names the destination folder by
its ID. An upload goes to the EC folder unless a different folder was named, in
which case it goes there instead — the EC folder is not a hard limit on where
a confirmed upload can land. So read the folder ID on the card and make sure it
is the one you intended before you approve.

Three limits are worth knowing because they are by design rather than by
oversight:

- **There is no way to delete a Guru folder from this app.** Renaming exists;
  deleting does not. Use the Guru web app.
- **Renn will not write a Drive folder name.** Folder names can carry
  identifying information, so no confirmed operation writes one.
- **Renn has no direct-execute Asana write tool.** Three older Asana write
  tools that ran without a card were retired, so every Asana change Renn
  initiates now goes through a Confirm card. This is specifically about Asana:
  the two writes described above as deliberately un-gated — publishing a draft
  to Guru, and the knowledge base writing its own index cards into the EC
  folder — do still run without a card, by design.

The request identifier for each pending confirmation is generated inside the
app rather than supplied by the model, so an approval cannot be fabricated or
replayed against a different change than the one you saw.

## If it doesn't

**Renn reports a change as complete without showing a card.** Verify in the
source system before believing it. If the change genuinely did not happen,
this is a serious bug — flag it with the exact request you made and Renn's
exact reply.

**A Confirm card describes something other than what you asked for.** Cancel.
Do not approve a card you do not recognise. Flag it with both your request and
the card's wording.

**Renn asks you for a folder ID, a collection ID or an Asana GID.** You should
never need to supply a raw identifier — that is what the pickers are for. Ask
for the picker by name, then flag it.

**Renn says it cannot delete a Guru folder.** Correct, and not a bug. Do it in
Guru.

**An artifact upload fails or is refused.** The EC folder may not be
bootstrapped yet, or it may have been trashed or moved in Drive. Renn should
name the EC folder in the message rather than failing vaguely. A vague failure
is worth flagging.

**A Confirm card wants to upload to a folder you did not expect.** Cancel it.
The destination is whatever folder ID the card shows, and it is not always the
EC folder — if a different folder was named it will be used instead. Only
approve once the folder ID on the card is the one you meant.

**A confirmed change is rejected afterwards.** If an Asana task moved
underneath you, a message about the task having changed and needing a retry is
expected. Anything else, flag it.
