---
id: settings-overview
title: The Settings sub-tabs at a glance
section: settings
section_title: Settings and connections
section_order: 8
order: 1
status: partial
features: [en_settings]
summary: Settings is split into seven sub-tabs — Connections, Providers, Sources, Style Guide, plus the Updates, Usage and Maintenance system tabs.
last_verified: 2026-07-22
---

The enablement Settings page is one page with seven sub-tabs. Most of what you
need day to day lives in two of them: Providers holds your credentials, Style
Guide holds the documents that shape generated cards. The last three are
system tabs — the same widgets the product side's Settings hosts, so updating,
usage and maintenance no longer require switching modes.

## How it works

| Tab | What is in it |
|-----|---------------|
| Connections | Status dots for Asana, Google Drive and Guru; the assistant provider choice; which mode the app starts in |
| Providers | Your identity, plus the full credentials panel — model, API keys, routing, Guru login, Google Drive |
| Sources | Asana boards, watched Drive folders, and the knowledge base controls |
| Style Guide | The style guide and the card/article template, with their stored libraries |
| Updates | Checking and installing app updates, the GitHub repository the check reads, and support & recovery (crash-report export and rollback) |
| Usage | Renn's metered activity — turns, tokens and the CLI-reported cost, with a 14-day activity chart and the recent turns |
| Maintenance | The memory profiler and the full-database reset danger zone |

Above the tabs, a header titles the page and carries a button labelled "Test
connections".

Updates and Maintenance are shared widgets, not copies: the Updates tab is the
same widget the product Settings page embeds, reading and writing the same
settings, so a repository or PAT saved in one mode is saved for both. Checking
for updates needs the GitHub repository configured (and a PAT for a private
repository). The Maintenance reset deletes data from the shared warehouse
regardless of which mode you are in, and asks you to type DELETE first — a
cancelled or mistyped confirm does nothing.

The Usage tab is this side's own. It comes from the Claude CLI's own per-turn
reports: each Renn turn is recorded automatically with its token counts and,
when the CLI reports one, its dollar cost — cost shows $0.00 on a
subscription CLI login, because the CLI reports no dollar figure there.
Background generation jobs are not metered yet, and the NLP scanner's cost
limits and plan utilization are gone from this side — they stay on the
product page's Scanning Costs tab, along with scan and report accounting.
Until a warehouse is connected the tab shows a placeholder.

Some of what you see in these tabs is display only. In the Connections card,
the poll interval is a read-only label rather than an editable field. In the
Sources tab, the Asana board panel below the connect row shows a fixed example
board with fixed field mappings — it is not reading your real Asana
configuration.

## How it should work

Anything you change should persist immediately, without a save step, and should
still be there after a relaunch. The assistant provider choice, the startup
mode choice, your identity email, the knowledge base toggle and every style
guide action all behave that way. The one deliberate exception is the Updates
tab's repository settings, which have an explicit save button.

A control that looks like a form field should accept input. Where it does not —
the poll interval, the board mapping fields — treat it as a label describing the
current behaviour rather than something you can change here.

The React versions of the Calendar and Workbench tabs are behind the
`enablement.web_tabs` setting, which is not present in the shipped
configuration. There is no switch for it on this page, and the native Qt tabs
render instead. That is expected, not a fault in your setup.

## If it doesn't

**A field will not accept typing.** Check the list above. The poll interval and
the Asana board mapping fields are labels. If a field you expect to be editable
is inert and is not one of those, flag it.

**The Asana board panel shows a board you do not recognise.** Expected in this
build — that panel is fixed example content and does not reflect your real
board. Your actual board configuration is what Renn saves during setup. See
*Connecting Asana*.

**Check for Updates reports a failure.** First confirm the GitHub repository is
set on the Updates tab (owner/repo), and add a PAT if the repository is
private. A check with no repository configured falls back to the built-in
default, which may not be reachable from your network.

**The Usage tab only shows a placeholder.** The dashboard appears once the app
has a warehouse open — on a normal install that is immediately; in a broken
state, reopening the app is the first thing to try. If it persists, flag it.

**A change does not survive a relaunch.** Note which control, then flag it.
Settings writes go straight to the settings store, so a lost value is a real
defect.

**You cannot find a Guru configuration card in Settings.** There is no Guru
cards section on this page. Your Guru login lives in the Providers tab and
your publish destination is set through Renn. See *Connecting Guru*.
