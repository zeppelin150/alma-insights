---
id: settings-test-connections
title: "Test connections: reading the result"
section: settings
section_title: Settings and connections
section_order: 8
order: 5
status: partial
features: [en_settings, asana, guru, google_drive]
summary: The three status dots carry real per-source results, but they are filled in once at startup and the button beside them does nothing.
last_verified: 2026-07-20
---

The Connections tab shows a coloured dot and a short phrase for each of Asana,
Google Drive and Guru. Those results are genuine — each one comes from an actual
call to that service, not a guess based on whether a key is present.

## How it works

At startup the app checks all three sources on a background thread and reports
each result independently.

- **Asana** — if a key is stored, it calls Asana and reports what came back. If
  no key is stored, it says so rather than reporting a failure.
- **Guru** — if an email and token are stored, it authenticates against Guru.
  A rejection reports as an authentication failure, which is different from
  having no credentials at all.
- **Google Drive** — reports whether Drive reading is configured and usable
  right now, which includes whether you have reconnected Google this session.

The button labelled "Test connections" above the tabs is not wired to anything.
Clicking it does not re-run the check. The dots only refresh when the app starts.

The check is also skipped entirely in demo mode, so the dots stay unset there.

## How it should work

Each dot should reflect that source alone. Guru failing should not change the
Asana dot. A green dot means the app reached that service and the service
accepted it; anything else carries a short reason.

The phrasing distinguishes three different situations, and the difference
matters:

| What it says | What it means |
|--------------|---------------|
| connected | Reached the service, credentials accepted |
| no credentials | Nothing stored; the app never tried |
| not configured | Stored, but a required setting is off |
| an error message | The call was made and failed |

A dot that never leaves its neutral state means the check has not run yet, or
you are in demo mode.

## If it doesn't

**You clicked the test button and nothing changed.** Expected — it is not
connected to anything. Relaunch the app to re-run the check. Flag the button as
a bug; it looks functional and is not.

**You fixed a credential and the dot still shows the old result.** Same cause.
The dots do not refresh mid-session. Relaunch.

**Google Drive says not configured while Google shows as connected in the
Providers tab.** Drive reading is gated on a separate organisation setting as
well as on the connection. See *Connecting Google Drive*.

**Google Drive shows a message asking you to connect your Google account after
you used a different control.** Some identity actions report their failure
through the Drive dot. It is telling you the truth about Google, just from an
unexpected place.

**A dot reports connected but the feature does not work.** The check verifies
that credentials are accepted, not that a specific folder, board or collection is
reachable. Test the actual action to narrow it down before flagging.

**All three dots stay neutral.** Either the app is in demo mode, or the startup
check did not run. If you are in live mode and see nothing after a relaunch,
flag it.
