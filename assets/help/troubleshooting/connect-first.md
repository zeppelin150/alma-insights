---
id: troubleshooting-connect-first
title: Connect-first messages and empty states
section: troubleshooting
section_title: When something goes wrong
section_order: 9
order: 3
status: available
features: [en_settings, en_workbench, en_calendar]
summary: Most empty screens are a missing connection rather than a fault — how to tell the difference.
last_verified: 2026-07-26
---

The most common "something is broken" report is a screen with nothing on it.
Usually nothing is broken.

## How it works

Several surfaces need an external system connected before they have anything to
show. When one is missing they render an empty state rather than an error,
because a missing connection is a normal state for a new install.

The usual causes, roughly in order of frequency:

- **Google is not connected in this session.** The Google connection is not
  persistent across app launches — you reconnect each time you start the app.
  Anything Drive-backed will be empty until you do.
- **No active Drive folder is set**, so there is nothing to read from.
- **No Asana board is configured**, so the Tasks board and Calendar have no
  source. Note that Asana cannot currently be connected at all — see
  *Known issues and current limitations*.
- **Guru is not connected**, so card lists and pickers come back empty.
- **The Zendesk mirror is empty.** The Zendesk workspace and Renn's Zendesk
  tools read a local mirror, not the live API — until you pull or import
  content, they report an empty mirror rather than a connection problem. Only
  the pull itself needs Zendesk credentials, and it only ever reads with them:
  the Zendesk connection is one-way, so no connection state anywhere in the
  app can change your Help Center.
- **Nothing has been scanned yet.** Sources are pulled on a scan or a monitor
  tick, not continuously.

## How it should work

An empty state should tell you *which* connection is missing, not just show
nothing. Ask Renn "what's connected?" for a direct answer covering the active
Drive folders, the active Asana board, and the Guru publish target.

A disconnected source should be reported as disconnected — never as an error,
and never silently skipped.

## If it doesn't

**A screen is empty and says nothing at all.** Ask Renn what is connected. If
everything reports connected and the screen is still empty, that is worth
flagging — an empty state with a healthy connection is a real bug.

**You connected something and the screen is still empty.** Switching to another
screen and back now refreshes it — each enablement screen re-reads its local
data when you return to it, so anything already pulled into the app shows up.
What switching does *not* do is fetch fresh data from Drive, Asana or Guru: a
source you just connected is pulled in on a scan or a monitor tick, not by
navigating. So if returning to the screen still shows nothing, run a scan or
wait for a monitor tick to pull the new data. If that does not populate it,
restart the app; a fresh launch rebuilds every screen from scratch.

**A source you connected reports as not connected.** Check Settings. Be aware
that the Test connections button does nothing at present, so a lack of result
there is not evidence either way — see *Known issues and current limitations*.

**Everything is empty and you are in demo mode.** Demo mode uses a throwaway
database that is wiped each session and never touches your real accounts. Check
whether demo mode is on before reporting missing data.
