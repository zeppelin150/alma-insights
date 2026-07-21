---
id: reference-settings-keys
title: Enablement settings keys
section: reference
section_title: Reference
section_order: 10
order: 2
status: partial
features: [en_settings, en_workbench, en_calendar]
summary: The enablement settings keys that change how the app behaves, what each one does, and which ones ship unset.
last_verified: 2026-07-20
---

Settings live in `data/settings.yaml` under the `enablement` section. Most of
these are written for you when you use a picker or a connect card in the app —
you rarely need to edit the file. This article exists so you can tell what a
key does when you find one, and so an administrator knows which keys are real.

Most of the keys below are absent from the shipped `settings.yaml`, which is
normal: a key missing from the file falls back to a default defined in code.
Absent is not the same as unset. The distinction that matters is whether a key
has a code default at all — one does not, and it is called out at the end.
That gap is why this article is marked partial rather than available.

## How it works

**Identity — who the app thinks you are**

| Key | What it changes |
|-----|-----------------|
| `operator_email` | Your email, set explicitly. Wins over detection. |
| `detected_email` | The email discovered from your Google sign-in. |
| `identity_auto_detect` | Whether detection is allowed to run at all. |
| `operator_name` | The name Renn uses for you. |
| `operator_asana_gid` | Which Asana user counts as "my tasks". |

Identity resolves in order: the explicit override, then the detected value,
then nothing.

**Mode and provider**

| Key | What it changes |
|-----|-----------------|
| `demo_mode` | When true, the Workbench runs on sample data and no monitors start. |
| `provider` | Which model provider the enablement lanes use. |

`demo_mode` is the big one. While it is true, background monitoring of Asana
and Guru does not start at all, so a Calendar or Tasks tab that never updates
is expected rather than broken.

**Google Drive**

| Key | What it changes |
|-----|-----------------|
| `drive.read_enabled` | Whether Drive reading is permitted. |
| `drive.credentials_path` | Path to the service-account credentials file. |
| `drive.active_folders` | The Drive folders currently in scope. |
| `google.oauth_client_path` | Path to the OAuth client configuration. |
| `kb.ec_folder_id` | The bootstrapped EC folder the app writes into. |

Drive reading needs both a credentials file and `read_enabled`; enabling one
without the other does nothing useful.

**Asana**

| Key | What it changes |
|-----|-----------------|
| `asana.active_board` | The board Renn reads and writes by default. |
| `asana.poll_interval_seconds` | How often Asana is polled. Default 60. |
| `poll_interval_seconds` | The general enablement poll cadence. Default 300. |
| `asana.extras_per_poll_cap` | How many tasks get enriched per poll. Default 10. |

The two poll-interval keys are different settings at different nesting levels.
The one inside `asana` governs the Asana feed; the top-level one governs the
wider enablement monitor.

**Guru**

| Key | What it changes |
|-----|-----------------|
| `guru.publish_collection_id` | Where published cards land. |
| `guru.publish_folder_id` | The sub-folder within that collection. |
| `analytics_poll_enabled` | Whether Guru analytics are polled. Off by default. |

**Writing and help**

| Key | What it changes |
|-----|-----------------|
| `style_guide_doc_id` | The document acting as the active style guide. |
| `card_template_doc_id` | The document acting as the active card template. |
| `help.bug_form_url` | The Asana bug-report form the Help tab opens. |
| `web_tabs` | Whether Calendar and Workbench render as React views. |

## How it should work

Setting a key through the app — a picker, a connect card, a toggle — should be
enough. You should never have to hand-edit the file to get a folder, board or
publish target configured, and Renn should never ask you for a raw folder ID
or Asana GID.

Two keys deserve individual attention — one because it genuinely has no
default, and one because being absent from the file is easy to misread as
being broken. Both fail in a defined way rather than breaking:

`help.bug_form_url` has no default at all. Until an administrator points it at your
team's Asana form, the flag-a-bug action explains that no form is configured
instead of opening a dead link. The form deliberately opens in your own
browser rather than inside the app, so the app never captures logs,
screenshots or document text into a bug report.

`web_tabs` accepts `off`, `calendar` or `all`, and defaults to `off`. With it
absent, the native Calendar and Workbench tabs render and the React versions
do not. Anything unrecognised also degrades to `off` — a bad value can never
take the working tabs away from you. See *Where Renn appears* for what this
costs you in practice.

There is a related app-level key, `ui.web_home`, which is deliberately kept
separate from `web_tabs` because Home is the first screen at launch. Turning
the two on is two independent decisions.

## If it doesn't

**You set a key and nothing changed.** Most settings are read when the feature
starts rather than continuously. Restart the app before concluding the key did
not take.

**Drive reads fail even though credentials are configured.** Check
`drive.read_enabled` as well — the credentials path alone is not sufficient.
Enabling Drive access is usually an organisation-level step, so it may not be
yours to turn on.

**Calendar or Tasks never refresh.** Check `demo_mode` first. Monitoring does
not start while demo mode is on, so an unchanging feed is the expected
behaviour rather than a stalled poller.

**The flag-a-bug action says no form is configured.** Expected on a fresh
install. Ask an administrator to set `help.bug_form_url`.

**A key you found in documentation or a code comment does nothing.** Not every
name that appears in the source is a live setting — some are described in
comments but never read. If a key documented *here* does nothing, that is
worth flagging with the key name and what you expected.
