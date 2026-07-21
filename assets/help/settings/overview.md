---
id: settings-overview
title: The four Settings sub-tabs at a glance
section: settings
section_title: Settings and connections
section_order: 8
order: 1
status: partial
features: [en_settings]
summary: Settings is split into Connections, Providers, Sources and Style Guide — here is what each one actually controls.
last_verified: 2026-07-20
---

The enablement Settings page is one page with four sub-tabs. Most of what you
need lives in two of them: Providers holds your credentials, Style Guide holds
the documents that shape generated cards.

## How it works

| Tab | What is in it |
|-----|---------------|
| Connections | Status dots for Asana, Google Drive and Guru; the AI provider choice; which mode the app starts in |
| Providers | Your identity, plus the full credentials panel — model, API keys, routing, Guru login, Google Drive |
| Sources | Asana boards, watched Drive folders, and the knowledge base controls |
| Style Guide | The style guide and the card/article template, with their stored libraries |

Above the tabs, a single row describes the page as an ETL source config and
carries a button labelled "Test connections".

Some of what you see in these tabs is display only. In the Connections row, the
poll interval is a read-only label rather than an editable field. In the Sources
tab, the Asana board panel below the connect row shows a fixed example board
with fixed field mappings — it is not reading your real Asana configuration.

## How it should work

Anything you change should persist immediately, without a save step, and should
still be there after a relaunch. The AI provider choice, the startup mode
choice, your identity email, the knowledge base toggle and every style guide
action all behave that way.

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

**A change does not survive a relaunch.** Note which control, then flag it.
Settings writes go straight to the settings store, so a lost value is a real
defect.

**You cannot find a Guru configuration card in Settings.** There is no reachable
Guru cards section on this page. Your Guru login lives in the Providers tab and
your publish destination is set through Renn. See *Connecting Guru*.
