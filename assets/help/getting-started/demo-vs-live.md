---
id: getting-started-demo-vs-live
title: Demo mode and live mode
section: getting-started
section_title: Getting started
section_order: 1
order: 5
status: available
features: [en_workbench, en_settings, en_tasks, en_calendar]
summary: Demo mode runs the real workflow against a throwaway database and never reaches your Guru; live mode uses your actual accounts.
last_verified: 2026-07-20
---

The enablement side runs in one of two modes. Demo mode lets you learn the
whole workflow without a single connected account and without any risk of
changing real content. Live mode does the real thing.

Demo mode is the default. If nobody has switched it off, you are in it.

## How it works

You can tell which mode you are in from the header. Demo mode shows a demo
label beside the page title, the status line offers to pull documents and draft
cards, and the scan button is labelled as a demo scan. Live mode drops the
label and the button simply scans.

**Demo mode is not a mock-up.** It runs the genuine pipeline — the same
document ingestion, the same draft generation, the same store, the same review
and diff and publish code paths. What changes is where the data goes and where
it stops.

- **A throwaway database.** Demo work is written to a separate database file in
  your system temp directory, not to the warehouse. It is deleted and rebuilt
  from scratch at the start of every session, so nothing you do in demo mode
  survives a restart.
- **No Guru client is handed to the publish path.** This is the important one.
  When you publish in demo mode, the code that talks to Guru is simply not
  given a connection. The draft is marked as published locally and the API is
  never called. There is no configuration mistake that can make a demo publish
  reach your real Guru, because there is nothing to reach it with.
- **Background work stays off.** Asana sync, the daily briefs, and the
  knowledge base do not run while demo mode is on. The Settings page lists this
  explicitly among the reasons a background capability is inactive.
- **Chat tools follow.** Renn's tools read and write the demo database too, so
  asking Renn to find or revise something operates on the demo content and not
  on anything real.

Scanning in demo mode seeds a set of sample tasks and runs a simulation that
produces real drafts you can open, edit, diff, and publish.

## How it should work

Demo mode should be a faithful rehearsal. Everything you learn about drafting,
reviewing, diffing, and publishing carries over to live mode unchanged — the
buttons are in the same places and behave the same way.

Switching to live mode is a setting an administrator changes. After the switch,
the app reads and writes your real warehouse database, publishes to your real
Guru collection, and starts the background sync. Your demo content does not
come with you, by design.

One wording quirk to expect: a successful demo publish reports the draft as
published to a named collection. Nothing was sent anywhere. The draft was
marked published in the throwaway database and that is all. The status line
appends a demo note to make that clear.

## If it doesn't

**Your work disappeared between sessions.** Expected in demo mode. The demo
database is wiped at the start of each session. If you need to keep something,
you need live mode.

**A card changed in Guru while you were in demo mode.** That should be
impossible. Check the header — you may have been in live mode without noticing.
If the demo label was showing and a real Guru card still changed, flag it
immediately with the card name and the time. That is the most serious bug this
mode can have.

**The Asana sync, calendar, or knowledge base is not updating.** Check the
knowledge base panel in Settings, which lists why each background capability is
inactive. Demo mode is the first reason it checks for.

**Renn refuses a knowledge base action and mentions demo mode.** Expected. The
knowledge base tools decline while demo mode is on rather than writing into a
throwaway folder.

**You are in live mode but the demo label is still showing.** Reopen the page.
The mode is read when the page is built. A label that persists after a restart
is worth flagging.

**Live mode shows nothing at all.** Your connections are probably not set up.
Work through *Your first 15 minutes*, then check the connection dots in
Settings.
