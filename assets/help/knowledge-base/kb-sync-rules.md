---
id: kb-sync-rules
title: "Sync rules: Drive always wins"
section: knowledge-base
section_title: The knowledge base
section_order: 6
order: 3
status: available
features: [kb]
summary: When your Drive edit and the app's version disagree, the Drive version is kept and the app's is discarded — every time.
last_verified: 2026-07-20
---

There is exactly one conflict rule and it has no exceptions: **the Drive
version wins**. If the app has an update waiting for a card and you changed
that card in Drive first, the app throws its own version away and takes yours.

## How it works

The knowledge base syncs on a timer in the background. Each pass does the same
sequence: send any card writes the app has queued up, then read back everything
that changed in Drive since last time. Periodically it also does a fuller pass
that checks for cards you deleted and rewrites the `_index.md` head files.

Before sending a queued write, the app checks whether the file changed in Drive
since it last looked. If it did, the write is dropped and your Drive version is
read in instead. Nothing is merged, and the app does not try again with the
same content.

The app also recognises its own writes. When it saves a card it remembers the
resulting timestamp, so the next read pass skips that file rather than
re-importing it. That is what stops a card from bouncing back and forth.

Deleting a card in Drive removes it from the app on the next full pass. The
reverse is not true: **the app never deletes anything in your Drive.** If a
source document disappears, the card built from it is marked as having a
missing source and kept.

Sync only runs while Google is connected for the session. A pass with no
connection does nothing and records that it was skipped; the next connected
pass picks up the whole backlog.

## How it should work

You should be able to edit a card in Drive and, within a sync cycle or two,
have Renn quoting your wording back to you.

If you edit the same card in both places, expect the Drive edit to survive and
the app's edit to be gone. That is the design, not data loss you should report
— but it does mean Drive is the safe place to make a change you care about.

Queued work does not pile up forever. A write that keeps failing is retried a
few times and then set aside, and anything still waiting after about a week is
dropped rather than released as a flood when you reconnect.

## If it doesn't

**Nothing has synced for a long time.** The usual cause is Google not being
connected for this session — the connection is deliberately dropped at launch
and has to be re-established. Check the knowledge base status in the enablement
settings, which lists every reason a background job is inactive.

**Your Drive edit did not take.** Give it a full sync interval, then check the
file actually saved in Drive. If the card genuinely changed in Drive and the
app still shows the old text after several cycles, flag it.

**A card you deleted in Drive keeps coming back.** Deletion removal happens on
the periodic full pass, not every pass, so a short delay is normal. A card that
reappears in Drive after you deleted it is not normal — flag it, because the
app is not supposed to recreate deleted cards on its own.

**A card is marked as having a missing source.** The document it summarised was
deleted, moved out of reach, or was created under a different sign-in. The card
is kept deliberately. If you want it gone, delete it in Drive.

**Sync reports an error against the EC folder.** See *When the KB stops
writing: quarantine and re-bootstrap*.
