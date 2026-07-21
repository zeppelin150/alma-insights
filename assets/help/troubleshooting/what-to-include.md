---
id: troubleshooting-what-to-include
title: What to include in a bug report
section: troubleshooting
section_title: When something goes wrong
section_order: 9
order: 2
status: available
features: [en_help]
summary: What makes a report actionable, and what you must never paste into one.
last_verified: 2026-07-20
---

Because nothing is captured automatically, the quality of a report is entirely
down to what you write. This is the short version of what helps.

## How it works

Five things make a report actionable:

1. **What you did**, in order. The exact clicks or the exact sentence you typed
   to Renn. Phrasing matters more than you would expect — a lot of assistant
   bugs are about which wording sends it down the wrong path.
2. **What you expected.** Every Help Center article has a "How it should work"
   section; if the app disagreed with it, quote the line.
3. **What actually happened**, including the literal text of any message.
4. **Which surface.** Workbench, Calendar, the Agent page, a Renn drawer — the
   same feature can behave differently in each.
5. **Whether it repeats.** A bug that happens every time and one that happened
   once are investigated very differently.

## How it should work

You should be able to write all five from memory, without reproducing the
problem. If you cannot describe what you expected, say so — "I do not know what
this was supposed to do" is itself useful feedback about the Help Center.

## If it doesn't

**Never paste patient or customer information into a bug report.** No ticket
text, no conversation transcripts, no exported rows, no screenshots of a
ticket. If the problem only shows up with real content, describe the shape of
the data rather than the data itself: "a ticket whose body is about 4,000
characters with three payer names in it" is enough to reproduce from.

**Never paste credentials.** No API keys, no tokens, no service-account files.
Nobody investigating a bug needs them, and a form entry is not a safe place for
them.

**If you think the bug is about specific content**, say which document or card
by name or ID rather than pasting its body.

**If the app crashed**, note the time. Local logs on your machine can be
matched against it later by someone with access, which is safer than you
copying log text into a form where it might carry patient data.
