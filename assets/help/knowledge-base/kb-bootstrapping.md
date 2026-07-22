---
id: kb-bootstrapping
title: Turning the KB on and bootstrapping the EC folder
section: knowledge-base
section_title: The knowledge base
section_order: 6
order: 5
status: partial
features: [kb, index_drive_folder]
summary: The knowledge base ships off; turning it on and creating the EC folder are two separate steps, and the second one writes to your Drive.
last_verified: 2026-07-20
---

A fresh install has no knowledge base configuration at all, so the knowledge
base is off and no EC folder exists. Getting it running is two deliberate
steps, and the second one is not read-only.

## How it works

The controls live in the enablement settings, under the sources tab, in a
section for the knowledge base. It shows a status line, a control to turn the
knowledge base on or off, and a separate control to create and verify the EC
folder.

The status line is the single place that explains why any background capability
is idle. It names the specific reason — demo mode on, no Asana token, Drive
access unavailable, knowledge base disabled, or enabled but the folder was
never created. Read it before assuming something is broken.

**Turning it on comes first.** The folder setup control stays unavailable until
the knowledge base is enabled.

**Creating the folder requires Drive access to be available.** What that means
depends on how you connected: on a personal Google account it means connected
*for this session*, because that connection is deliberately dropped at every
launch, so reconnect first; on a service account it means Drive reads are
enabled and the credentials file is still in place, which survives a restart.

**The setup step writes to your Drive.** It does not just look for an existing
folder. It creates the EC folder if there is none, then uploads a small marker
file into it and updates that file, to prove this build can actually write
there. Read access is not the same as write access — see *When the KB stops
writing: quarantine and re-bootstrap*. So enabling the knowledge base is a
change to your Drive, not an inspection of it.

Once the folder exists, ask Renn to index a Drive folder to fill it. That
request is queued rather than run immediately; the background sync picks it up
on its next pass, and progress appears in the jobs list.

Indexing is capped, and you should expect it to stop early on a large folder:

| Limit | Value |
|-------|-------|
| Documents per indexing job | 500 |
| Subfolder depth searched | 6 levels below the folder you pick |

Hitting the document cap is reported as truncated, and the remainder is picked
up on a later run rather than lost. Folders nested more than six levels deep
are not visited at all.

## How it should work

After enabling and running setup, the status line should say the knowledge base
is active and show a card and topic count.

Indexing should never fail because the summarising model had a bad day. If the
model cannot produce a usable summary, the app falls back to a plain excerpt
from the document and still creates the card.

The full text of every document it indexes is stored, not just the summary, so
a fact the summary skipped is still findable. See *Searching the knowledge
base*.

Renn should never ask you for a raw Drive folder identifier. It should offer
you a picker instead. Being asked to paste an ID is worth flagging.

**Expect to restart the app after enabling the knowledge base.** The background
sync is set up when the app starts, and only if the knowledge base is already
on at that moment. Turning it on mid-session does not start it.

## If it doesn't

**The folder setup control does nothing or is unavailable.** Check the
knowledge base is turned on first — the control stays disabled until it is.

**Setup reports that Google is not connected.** On a personal Google account,
reconnect for this session and try again — this will recur after every app
launch by design. On a service account there is nothing to reconnect: check
instead that Drive reads are enabled and the credentials file is still where
you pointed it.

**Renn refuses to index and says the knowledge base is disabled, in demo mode,
or not set up.** All three are real states with a real fix, and the message
names which one. Work through them in that order: demo mode off, knowledge base
on, folder created.

**An indexing job sits queued and never runs.** The background sync has to run
it, and that needs Google connected — and, if you enabled the knowledge base
this session, an app restart. Jobs left waiting for about a week are dropped
rather than run late.

**Indexing finished with far fewer documents than the folder holds.** Check the
caps above. If it stopped well short of fifty documents and the folder is
shallow, flag it with the folder and the count you expected.

**Nothing is being written to Drive even though everything looks enabled.** See
*When the KB stops writing: quarantine and re-bootstrap*.
