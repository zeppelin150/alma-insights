---
id: reference-version-and-data
title: Version, updates and where your data lives
section: reference
section_title: Reference
section_order: 10
order: 4
status: available
features: [en_settings, updates]
summary: How to find which version you are running, how updates work, and exactly which of your data stays on this machine.
last_verified: 2026-07-20
---

When you flag a bug, the version number is the single most useful thing you
can include. This article tells you where to find it, and what happens to your
data in the meantime.

## How it works

**Finding your version.** The version appears in two places: at the bottom of
the sidebar, written as `v` followed by the number, and in Settings on the
Updates tab. This build is version 1.0.0.

**Updates.** The app can check for new releases on launch. The check compares
your version against the latest published release and reports back; it does
not install anything without the update flow running. Update checking needs an
access token to be configured, and it can be switched off entirely in
settings. If no token is configured you will see a warning at startup rather
than a silent failure.

If an update goes wrong, the app can roll back to the previous version, which
includes restoring the version number itself so what the sidebar shows always
matches what is actually installed.

**Where your data lives.** Everything the app stores about your work is in the
`data` folder next to the application:

| What | Where |
|------|-------|
| All app data | `data/local_warehouse.db` |
| Database sidecars | `local_warehouse.db-wal` and `-shm` |
| Your settings | `data/settings.yaml` |
| Logs | `data/logs` |

The database is a single SQLite file. Card drafts, enablement tasks, generated
artifacts, indexed document text, knowledge base cards, chat history and this
help content are all rows inside it. The two sidecar files are part of the
database, not temporary junk — copying the database without them can lose
recent writes.

**What stays local.** The database, your settings and your logs live on this
machine. There is no sync service and no remote copy of your database — the
app does not upload it anywhere. Indexed document text in particular is stored
locally so that a detail a summary omitted is still findable without going
back out to Drive.

**What leaves.** Four things reach the outside world, and each is something
you initiated:

- **Model calls.** Text you ask the app to work on is sent to the configured
  model provider.
- **Publishing to Guru.** A draft you publish becomes a card in Guru.
- **Asana changes.** Confirmed task operations are written to Asana.
- **Drive writes.** Index cards go into the EC folder; an artifact you upload
  goes into the Drive folder named on its Confirm card, which is the EC folder
  unless you named another.

Redaction runs on model calls. On enablement work — the lane Renn uses — the
base pass that removes emails, phone numbers, national identifiers, card
numbers and member IDs runs on every call, and if that pass ever cannot load
its rules the call fails rather than sending your text through un-redacted. On
that same enablement lane the additional pass that rewrites capitalised terms
is deliberately turned off, because it mangles ordinary product vocabulary and
ruins card quality. Turning that second pass off is a considered trade-off on a
lane that works with your own documentation rather than patient records, but it
is worth knowing about before you paste something sensitive into a draft.

The same fail-closed rule now holds on the app's other model path. The
separate path the app uses for its own operational model calls — not the
enablement lane — used to fail open if it could not load its redaction rules:
it logged the failure and sent the text through un-redacted rather than
blocking. It no longer does that. That path now aborts the call and raises if
it cannot load its rules, so no un-redacted text goes out on either path.
That path does not handle your card drafts anyway. Either way, treat the model
as something your text reaches, and ask before pasting anything sensitive.

## How it should work

The version in the sidebar and the version in Settings should always agree. If
they differ, something went wrong during an update.

Closing the app should leave the database consistent. You should be able to
reopen and find your drafts, tasks and chat history exactly as you left them.

Nothing should reach Guru, Asana or Drive without either an explicit publish
request from you or a Confirm card you clicked. See
*What Renn can write, and what always needs your click* for the full list.

## If it doesn't

**The sidebar version and the Settings version disagree.** Flag it, quoting
both numbers. This usually means an update or rollback finished partially.

**Startup warns that no update token is configured.** Expected on a fresh
install. Updates are an administrator setup step, not something you configure
per machine.

**You want to back up your work.** Copy the whole `data` folder while the app
is closed. Copying only `local_warehouse.db` while the app is running can miss
recent writes still sitting in the write-ahead log.

**Drafts or history disappeared after a restart.** Check whether the `data`
folder moved, or whether you are running a second copy of the app from a
different location — each location has its own database. Genuinely lost data
from a stable install is worth flagging urgently, with the version number and
roughly when you last saw the missing work.

**You are unsure whether something you typed left the machine.** Anything you
asked a model to work on did. Drafts, tasks and notes you never ran through
Renn did not. If you need certainty for a specific case, ask before pasting
rather than after.
