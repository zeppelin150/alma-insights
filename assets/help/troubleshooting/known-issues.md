---
id: troubleshooting-known-issues
title: Known issues and current limitations
section: troubleshooting
section_title: When something goes wrong
section_order: 9
order: 6
status: available
features: [en_settings, en_workbench, en_calendar, en_agent]
summary: Everything currently known to be missing, stubbed or gated off — check here before reporting.
last_verified: 2026-07-20
---

If what you found is on this list, it is already known and you do not need to
report it. Anything **not** on this list is worth flagging.

## How it works

**Asana cannot be connected from the interface.** The API key field in Settings
is not wired to anything that saves it, and no other screen writes an Asana
key. Asana itself works normally once a key is installed into the credential
store another way (an administrator does this with the project's setup
scripts) — but you cannot do it yourself from the app, and the field will look
empty even when a key IS present, so it tells you nothing about your
connection state. Ask Renn "what's connected?" instead. Without a key, the
setup assistant now declines rather than saving a board built from sample data.

**The Test connections button does nothing.** It is present and clickable but
is not connected to any action. A lack of result from it tells you nothing.

**A dismissed attention-queue item comes back on the next refresh.** Dismissing
a row on the Home attention queue hides it for now; it is not recorded, so a
Refresh (or reopening the queue) brings it back. That is the recovery path if
you dismiss something by mistake — but it also means dismiss is "not now", not
"never again". Act on an item if you want it gone for good.

**The Asana board list in Settings is a mockup.** The "2 configured" heading,
the board rows, the field mappings and the resolved assignee names are fixed
placeholder text, not your configuration.

**Publishing to Drive from the Workbench writes nothing.** Both Drive
destinations only display a status message. Publish to Guru instead, or export
and upload by hand.

**The React Calendar and Workbench never render.** They are behind a setting
that is absent from the shipped configuration, so the standard Qt versions are
what you see. Anything documented as web-only — drag-to-reschedule, the in-page
Renn drawer — is unreachable.

**The Content Studio has no screen.** Diagrams, quizzes, one-pagers, battle
cards and decks are reachable only by asking Renn. Diagram and quiz previews
are deferred, and the podcast type is reserved with nothing behind it.

**Generated decks are not branded.** The PowerPoint template that ships is the
plain python-pptx default, not an Alma-branded one, so exported decks come out
in the generic Office look. Apply your own template or branding after export.

**Background research does not exist.** Renn will decline it.

**Draft search still matches a whole phrase.** Document and knowledge-base
search now handle your words individually, but the Workbench draft search still
matches a whole phrase only when the words appear together, so "prior
authorization escalation" can miss a draft containing those words apart. Use
fewer, more distinctive words there. The Help Center's own search does not have
this problem.

**The Drive folder picker has no search box.** You navigate the folder tree by
expanding it, which is slow with many folders.

**Drive search reads every page of results** (up to the requested limit) and
retries with backoff when Google rate-limits a request, so matches beyond the
first page are found. Very large result sets are still capped by the limit you
ask for.

**Drive folder monitoring does not descend into subfolders**, so changes in a
nested folder can be missed. Knowledge-base indexing is unaffected — it walks
subfolders itself, up to a depth and document limit.

**Your Guru email is stored in plain text.** The Guru API token is kept in the
OS credential vault, but the Guru email address that goes with it is saved
unencrypted in local app state. It is only an email address, not a secret, but
do not treat that file as private.

## How it should work

Each of these should eventually either work or disappear. Until then, the Help
Center marks affected articles with a badge and a banner, so the article you
are reading tells you its own status rather than leaving you to find out by
trying.

## If it doesn't

**Something on this list appears to work for you.** Worth reporting — a fixed
item that is still documented as broken is its own defect, and this page should
be corrected.

**Something is broken that is not on this list.** Report it. See
*How to flag a bug*.

**You are not sure which category you are in.** Check the status badge on the
article for that feature. The badge is generated from the same information as
this page, so the two should never disagree; if they do, that is worth
reporting too.
