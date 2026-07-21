---
id: plan-asana-setup
title: Setting up your Asana board with Renn
section: plan
section_title: Plan — Calendar and Tasks
section_order: 3
order: 6
status: partial
features: [asana_discover, set_asana_board_config, renn]
summary: Renn discovers your Asana projects and saves the board configuration for you. Without an API key it shows sample projects, clearly flagged, and declines to save a configuration built from them.
last_verified: 2026-07-20
---

Connecting an Asana board normally means hunting through URLs for numeric
IDs — for the project, for a custom field, for one option inside that field.
You should never have to do that here: Renn reads them from Asana and writes
the configuration for you.

**One prerequisite is not in the interface.** The API key field in Settings is
not connected to anything that saves it, so the key has to be installed into
the credential store another way — normally by an administrator using the
project's setup scripts. Everything below works once a key is present.

**Without a key, discovery falls back to sample projects — and says so.** The
result is tagged as sample data rather than passed off as your real board, and
the setup button will decline to save a configuration built from it. You can
still confirm with Renn ("what's connected?") before you trust a discovery
result. See also *Your first 15 minutes*.

## How it works

With a key installed, Renn does two things in order:

1. **Discovers.** It asks Asana for your projects and, for a project, its
   custom fields and the options inside them. It reports what it found in the
   chat, IDs included, so the mapping is visible rather than hidden.
2. **Saves.** It writes that configuration — which project to watch, which
   field and value marks a task as enablement work, and which fields carry
   priority and assignee.

You can also just ask. Renn has both steps as tools and will run them from a
plain request like "find my Asana projects" or "set up the Project Tracker
board". Asking is the better route when your board does not use the field
names the setup button expects.

**Without a key, Renn stops before saving.** Discovery still runs so the flow
is demonstrable, but the projects it returns are the built-in samples, tagged
as such. The setup button recognises that tag and declines rather than writing
fabricated project and field IDs into your settings — it tells you Asana is not
connected and asks you to connect it first. Demo mode is the one deliberate
exception: there the sample board is saved on purpose, so the rest of the app
has something to show.

Saving the board configuration is one of the few things Renn writes directly,
and it is tightly scoped: it touches the Asana source settings and nothing
else. Renn says so when it is done.

Once the board is saved, tasks flow into the Tasks tab and the Calendar.

## How it should work

**You should never be asked for a GID.** If Renn asks you to paste a project
ID, a field ID, or a workspace ID, that is a bug — the whole point of this
flow is that it resolves them itself. Flag it.

Setting up the board is separate from telling the app **who you are**, and the
Mine filter needs both. In the identity area of Settings you can detect your
email from the connected Google account, and resolve your Asana user from your
API key. Until at least one of your Asana user ID or display name is stored,
Mine falls back to showing every task — see *The Calendar: month, week, and
scope*.

The setup button takes an opinionated path. It picks your first project and
looks for a custom field named "Assigned Team" holding an option named
"Enablement", mapping priority from an "Urgency" field and assignee from an
"Assigned People" field. If your board uses those names, the button is the
fastest route. If it does not, ask Renn instead and describe your own field
names.

## If it doesn't

**Discovery shows projects you do not recognise.** This means no API key is
stored — discovery has fallen back to the built-in samples. They are tagged as
sample data, and the setup button will not save a board built from them; it
tells you Asana is not connected instead. Ask an administrator to install a
key into the credential store, then run setup again.

**You pasted a key in Settings and nothing happened.** Expected — that field
saves nothing, and it will still look empty even when a key IS installed. Ask
an administrator to install one into the credential store; do not judge your
connection state from that field.

**Renn lists your projects but nothing gets saved, and there is no error.**
With a key present, this is the most common outcome when your board does not
have the exact field names above. Nothing is broken and nothing was written —
ask Renn directly and name the field and value that marks enablement work on
your board.

**Renn asks you for an ID.** Flag it, and quote the request.

**Setup picked the wrong project.** The button takes the first project it is
given. Ask Renn to set up the one you want by name.

**Tasks still do not appear after a successful setup.** Confirm that real
tasks in that project carry the field value the configuration was saved
against, and check the Mine and All toggle — with an identity set, Mine will
hide anything not assigned to you.

**Resolving your Asana user reports that Asana is not connected.** The
identity lookup uses the same API key as everything else, so fix the key
first, then resolve again.

**Renn claims it changed something in Asana during setup.** It should not have
— setup only reads from Asana and writes local settings. Any live Asana change
has to go through a confirmation card, as described in *Why Renn never writes
on its own*. Flag it.
