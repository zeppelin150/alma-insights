---
id: settings-connect-asana
title: Connecting Asana
section: settings
section_title: Settings and connections
section_order: 8
order: 3
status: partial
features: [en_settings, asana]
summary: Asana works fully once a key is stored, but the key field does not save what you type — and pressing setup without a key saves a fabricated board configuration.
last_verified: 2026-07-20
---

Asana supplies your tasks, your calendar and the "mine" filter. Once an API key
is present the whole board flow works. Getting the key stored is where this build
falls short — and the setup button behaves badly when the key is missing, so read
*How it works* before you press it.

## How it works

The Asana card lives in the Sources tab. It shows a field labelled for an Asana
API key and a button to set up with Renn, followed by an example board panel.

**The API key field does not save.** Typing a key into it and moving on stores
nothing. There is no other credential entry for Asana anywhere in the app, so
the key has to be provisioned into the credential store outside this page. If
your install already works, someone did that during setup.

**With a key stored, the setup button works.** It asks Asana for your projects,
custom fields and enum values, then writes a board configuration into your
enablement settings — you never have to find a GID yourself. It picks the first
project it finds and looks for fields named for assigned team, urgency and
assigned people.

**Without a key stored, the setup button does something worse than nothing.** It
does not stop and it does not tell you a credential is missing. It falls back to a
built-in sample workspace, runs the entire flow against that, and saves a board
configuration built from invented IDs into your source settings. The chat panel
reports it as a success. Nothing distinguishes that message from a real one except
the project names, so check them — see *If it doesn't* for what the sample data
looks like and what to do about it.

The button offering to add a board is not connected to anything, and the board
panel below it shows fixed example content rather than your real board.

## How it should work

With a key in place, the setup button should produce a short conversation in the
chat panel listing your real project names and field values, ending with a
confirmation that the board configuration was saved. The Asana dot in the
Connections tab should then read as connected, and the Tasks and Calendar views
should fill with real tasks.

Without a key, setup should refuse to run and say so. It does not — treat that
gap as the main risk on this page rather than an inconvenience.

Renn should never ask you for a project ID, a field ID or an enum value. Finding
those is the entire point of the setup step.

Renn's Asana scope here is deliberately narrow — it edits the Asana source
configuration and nothing else.

## If it doesn't

**You typed a key and nothing happened.** Expected in this build. That field
does not persist. Ask whoever set up your install to store the key, and flag the
inert field as a bug — it reads as functional and is not.

**The dot says there are no credentials.** Same root cause: no key is in the
credential store. Do not press the setup button while the dot reads this way —
it will not refuse, it will save fabricated configuration. See the next entry.

**Setup names projects you do not recognise.** This is the failure to watch for,
and it is not harmless. With no key stored, setup runs against a built-in sample
workspace instead of stopping. The giveaway is the project names: a workspace
called "Alma Health" containing "Enablement Requests", "Launch Coordination" and
"Provider Onboarding". If you see those and they are not your board, none of it
came from Asana.

By the time that message appears the configuration is already saved — setup
writes before it reports, so there is no prompt to decline and nothing to undo
from this page. Do not treat any ID, field name or enum value it showed you as
real, and do not repeat them to anyone as your board's settings.

Recovering needs someone with access to your install, for two reasons. Board
configurations are keyed by project ID, so storing a real key and running setup
again adds a second board rather than replacing the invented one — the phantom
stays. And nothing on this page removes a board: the add-board button is inert and
the panel below it is fixed example content, so there is no delete control to
reach for. Ask whoever set up your install to store a real Asana key and remove
the phantom source, then re-run setup. Flag it as a bug at the same time: a setup
step that saves invented configuration when a credential is missing is a defect,
not a demo mode.

**Tasks stop appearing, or the board reports errors every cycle, after a real key
is added.** If a phantom board from the case above is still configured, the poller
keeps trying to read a project that does not exist and records an error for it on
every pass. The real board is polled independently and should still work, so check
whether you are looking at one failing board or all of them before flagging.

**Setup runs but names no projects.** Either the key is stored but rejected by
Asana, or the account it belongs to cannot see any projects. Check the Asana dot
first to tell those apart.

**Setup finds your project but saves nothing.** It is looking for specific field
names on that project. If your board names its fields differently, there is
nothing to map. Flag it with your actual field names.

**Setup configures the wrong project.** It takes the first project returned, not
one you choose. If you have several, that is a real limitation worth flagging.

**Resolving your Asana ID fails with a prompt to connect Asana.** That is the
same missing-key problem surfacing from a different control. See *Who you are:
identity and your Asana ID*.
