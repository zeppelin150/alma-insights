---
id: getting-started-first-15-minutes
title: Your first 15 minutes
section: getting-started
section_title: Getting started
section_order: 1
order: 3
status: partial
features: [en_settings, en_agent]
summary: Connect Guru, then Google, then Asana — and know which of those three still needs an administrator.
last_verified: 2026-07-20
---

Three connections drive almost everything on the enablement side. Set them up
in this order, because each one unlocks more than the last. All of them live
under Settings in the sidebar.

Two of the three you can finish yourself. Asana currently cannot be completed
from the interface — read the Asana step before you spend time on it.

## How it works

**1. Guru — unlocks reading and publishing cards.** Open Settings and go to the
Providers tab. There is a Guru section with an email field and an API token
field, and a button to save both. Once saved, the app can read your live cards,
show diffs against them, and publish drafts back.

Use the email your Guru account is registered under. A wrong address fails
authentication in a way that looks like a broken token.

**2. Google — unlocks Drive reading and the knowledge base.** Same Providers
tab. There are two separate mechanisms and they are not interchangeable:

- A **service-account credentials file**, set at the organisation level.
- **Your own Google account**, connected per-user with a button that starts an
  authorization flow in your browser.

Connecting Google unlocks reading source documents out of Drive, the knowledge
base that indexes a product folder for you, and uploading generated artifacts.
Drive reading also requires read access to be enabled in settings and each
watched folder to be shared with the service-account address — the interface
says so in a warning strip on the Sources tab. Both are administrator steps.

Once Google is connected, go to the Providers tab and let the app detect your
email automatically. That is how Renn knows which tasks and calendar entries
are yours.

**3. Asana — unlocks your task list and calendar.** This is the step that is
not finishable from the interface today: the key field on the Sources tab is
not wired, so there is no way to connect Asana yourself. What is safe now is
"Set up with Renn" — without a connected key it discovers only built-in sample
projects, flags them as samples, and declines to save them rather than writing
a fabricated board into your settings. See below.

You can watch progress on the Connections strip at the top of Settings, which
shows a dot per service. Those dots are only filled in by a real connection
test in **live mode** — there, a probe runs once when the enablement pages are
first mounted, and each dot reflects the actual state, not what you typed. In
**demo mode**, which is the shipped default, no probe ever runs: the connection
test returns immediately and the dots stay unfilled. So if you are in demo mode,
do not read the dots as connection status — switch to live mode to see them
work.

## How it should work

After Guru and Google are connected, you should be able to open the Workbench,
see live cards, generate a draft from a Drive document, and publish it. That
much works.

You should also set your style guide and card template before generating
anything. Both live on the Style Guide tab in Settings, both accept a pasted or
uploaded document, and the active one shapes every card Renn writes. Skipping
this produces cards that do not match your house format.

Asana, when connected, should give you your task list, the calendar, and
"only mine" filtering. Renn should then be able to discover your projects and
custom fields and save a board configuration without ever asking you for an ID.
Until a key is connected, Renn will not save a configuration built from the
sample projects — it tells you Asana is not connected instead of persisting
anything.

## If it doesn't

**Pasting an Asana API key into the field on the Sources tab does nothing.**
This is confirmed: that field is not connected to anything that saves it, and
no other screen in the app writes an Asana key either.

That does *not* mean Asana is unusable — only that it cannot be connected from
the interface. The app reads the key from its encrypted credential store, and a
key put there another way works normally. Ask an administrator to install one;
the project's setup scripts do exactly this. To check whether yours is already
connected, ask Renn "what's connected?" rather than judging by the Settings
field, which will look empty either way.

**The Asana section shows two configured boards you have never seen.** Those
rows are placeholder content built into the page, not your data. The board
names, the field mappings, and the resolved assignee names in that section are
all fixed sample text. Ignore them.

**"Set up with Renn" says it can't set up your board yet.** If Renn replies
"I can't set up your Asana board yet — no Asana API key is connected", that is
expected without a connected key, not an error. Rather than silently saving a
board built from sample projects, setup now declines: discovery flags its
results as samples, and nothing fabricated reaches your settings. Ask an
administrator to connect a key, then run setup again. In demo mode this step
still runs against the sample projects on purpose — see *Demo mode and live
mode*.

**The Test connections button appears to do nothing.** It does nothing — it is
not wired to any action. This is a known defect, not a slow response.

**You fixed a credential and the dot did not change.** There is no way to
re-run the connection test from inside the app. The probe runs once, when the
enablement pages are first built, and nothing re-runs it afterwards: leaving
Settings and coming back only switches which tab is displayed, so the dots you
are looking at are as old as your session. Restart the app to re-test. In the
meantime, ask Renn "what's connected?" — that checks at the moment you ask
rather than reading the stale dots.

**All three dots are unfilled and nothing you do changes them.** Check whether
you are in demo mode, which is the default. Demo mode skips the connection test
entirely, so the dots are not reporting a failure — they are reporting nothing
at all. See *Demo mode and live mode*.

**Guru saves but the dot stays amber.** Check the email address first — the
most common cause is a near-miss on the domain. If the credentials are
definitely right and the test still fails, flag it.

**Google connects but Drive stays unconfigured.** The per-user connection and
the service-account file are separate. Drive folder reads also need read access
enabled and each folder shared with the service account. If all three are in
place and Drive still reports not configured, flag it.

**Renn says it does not know who you are.** Detect your email again on the
Providers tab, then check that it saved. If the field is populated and Renn
still cannot identify you, flag it — see *What Renn can do, and what Renn will
refuse*.
