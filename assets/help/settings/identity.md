---
id: settings-identity
title: "Who you are: identity and your Asana ID"
section: settings
section_title: Settings and connections
section_order: 8
order: 6
status: available
features: [en_settings, renn, asana]
summary: Your email and Asana ID drive the "mine" task filter — and an email on its own is not enough to make it work.
last_verified: 2026-07-20
---

The Providers tab opens with an identity card. It is the app's answer to "who is
using this", and it does two jobs: it lets Renn tell you who you are, and it
powers the filter that narrows tasks and the calendar to your work.

## How it works

There is an email field, a button that detects your email from your connected
Google account, and a button that resolves your Asana ID.

Detecting from Google reads the email off the account you connected this session
and caches it. Resolving asks Asana who the stored API key belongs to and saves
both the ID and your Asana display name.

A value you type into the email field wins over anything detected. The detected
email is always cached, but it only fills the field when you have not set one
yourself. That is the intended precedence — a deliberate override is respected,
which matters on a shared or service account.

For the "mine" filter, what actually gets matched is your **Asana ID or your
Asana display name** — never your email. Asana task records store the assignee's
display name, not an address. So an email on its own cannot filter anything.

The app handles that case by showing you everything. When neither an Asana ID nor
a name is known, the filter falls back to show-all rather than leaving you
staring at an empty board.

Identity here is a convenience scope, not a security boundary. It decides what
you see by default; it does not decide what you are allowed to do.

## How it should work

After resolving, the card should name you and show your Asana ID, and the mine
filter should narrow the Tasks and Calendar views to tasks assigned to you.
Switching between mine and all should persist across relaunches.

Renn should be able to answer "who am I" from this without a network call. If it
says it does not know who you are while this card shows an email, that is a bug.

The two buttons need different things. Detecting your email needs Google
connected this session. Resolving your Asana ID needs an Asana key stored.
Neither one substitutes for the other.

## If it doesn't

**The mine filter shows every task, not just yours.** Almost always this means
you have an email but no Asana ID and no Asana name. Resolve your Asana ID. This
show-all behaviour is deliberate, not a filter failure.

**The mine filter shows nothing.** The opposite problem — a name or ID is set but
does not match how Asana records your assignments. Check that the resolved name
matches your Asana display name exactly.

**Detecting your email reports that you should connect Google first.** Google is
not connected this session. Reconnect and try again — see *Connecting Google
Drive*. Note that this message can appear against the Drive status rather than
next to the button you pressed.

**Resolving your Asana ID reports that you should connect Asana first.** No Asana
key is stored. See *Connecting Asana* — the key field on the Sources tab does not
save, so this may not be something you can fix yourself.

**The email keeps reverting.** Something is writing over your override. A value
you typed should always win. Flag it.

**Your daily plan lists tasks that are not yours.** The plan uses the same
identity. Unassigned tasks are excluded from it by design, so an unexpected task
means an assignee matched one of your names.
