---
id: kb-quarantine
title: "When the KB stops writing: quarantine and re-bootstrap"
section: knowledge-base
section_title: The knowledge base
section_order: 6
order: 6
status: partial
features: [kb]
summary: A trashed or unwritable EC folder is quarantined and all writes are refused — but that check only runs when you re-run the folder setup.
last_verified: 2026-07-20
---

The app refuses to write anywhere in Drive except the EC folder it created. If
that folder becomes unusable, the safe response is to refuse everything rather
than write into the wrong place, so the folder is quarantined and writing stops.

## How it works

A folder is quarantined when any of three things is true:

- **It is in the trash.** This matters more than it sounds. A trashed Drive
  folder still accepts new files — they land in the trash with it. Refusing is
  the only way to avoid quietly filing your knowledge base in the bin.
- **It is no longer writable** by this sign-in.
- **It cannot be read at all** — deleted, or created by a different build of
  the app.

That last case is the surprising one. The app's Drive access is scoped to files
it created, and that scope is tied to the specific application registration. An
EC folder created by one build can be visible to another build and still reject
every write from it. This is why setup uploads a marker file rather than just
checking the folder exists — a folder you can see is not proof of a folder you
can write to. See *Turning the KB on and bootstrapping the EC folder*.

Once quarantined, the folder is no longer on the allowed list, and every
attempt to write a card into it is refused outright.

**The check runs when you re-run the folder setup, not continuously.** The
routine background sync does not re-test whether the EC folder is still in the
trash or still writable. So if you trash the EC folder while the app is
running, writes can keep going into the trashed folder until someone re-runs
setup and the problem is detected.

Renn can list the knowledge base topics with a quarantined marker on each, so
you can ask it which folders are affected.

## How it should work

When the folder is unusable, the setup step should tell you plainly which
problem it found and that a re-bootstrap is needed, rather than leaving you
with repeated permission failures and no explanation.

Recovery is to run the folder setup again. What happens next depends on the old
folder:

- **If you restored it first** — un-trashed it in Drive, or its permissions came
  back — setup re-verifies the old folder, finds it healthy again, and re-adopts
  it. It resets the folder to good standing and points the knowledge base back at
  the same folder, so your cards are right where they were. No second EC folder
  is created.
- **If the old folder is still unusable** — still trashed, still unwritable, or
  created by a different build — setup leaves it alone and creates a fresh EC
  folder instead. The content is not precious in itself: the cards were built
  from your source documents, so re-indexing those folders rebuilds them.

Either way the app deletes nothing in the old folder. If it is in the trash, it
stays in the trash for you to restore or empty.

## If it doesn't

**Cards stopped appearing in Drive but the app looks healthy.** Re-run the
folder setup. That is what triggers the check, and the result tells you whether
the folder was trashed, unwritable, or unreachable.

**You see repeated permission failures against Drive.** Most likely the folder
belongs to a different build or sign-in. Re-run setup to create a fresh one.

**Cards are being written into a folder in your trash.** Restore the folder in
Drive, then re-run the folder setup. The routine sync does not re-test the
folder on its own — the gap described above — so a trashed folder keeps taking
writes until setup runs; restoring it and re-running setup re-verifies and
re-adopts the same folder, and writes land in the right place again. If you
catch it early and restore the folder before setup has recorded a quarantine,
that alone is enough: the app keeps using the same folder, so un-trashing it
puts writes back on track.

**Setup keeps failing to create a folder at all.** Confirm Google is connected
for this session, then flag it with the message shown, because that is a
different failure from quarantine.

**After re-bootstrapping into a new folder, your old cards do not reappear.**
This happens when the old folder could not be re-adopted — it was still trashed,
unwritable, or from a different build — so setup created a fresh one. Re-index
the source folders to rebuild the cards. If you wanted the old folder reused,
restore it in Drive first and then re-run setup, so it can be re-adopted instead
of replaced.
