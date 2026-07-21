---
id: settings-connect-guru
title: Connecting Guru
section: settings
section_title: Settings and connections
section_order: 8
order: 2
status: available
features: [en_settings, guru]
summary: Enter your Guru email and API token in the Providers tab; the publish destination is set separately, through Renn.
last_verified: 2026-07-20
---

Guru is where published cards land, and where the Workbench pulls existing cards
from when you want to update one. It needs two things from you: the email you
use with Guru, and an API token.

## How it works

Open Settings, then the Providers tab, and scroll to the Guru card. There are
two fields — an email and a token — and a button to save them both.

The two fields are stored differently. The API token — the secret — goes into
the app's encrypted credential store. The email address does not: it is treated
as ordinary configuration and written in clear text to the app's local state
file, alongside other non-secret preferences. Both persist across launches. Once
saved, the app builds a Guru client at startup and the Workbench can list your
existing cards.

An email address is not sensitive the way a token is, so this is not a leak of
your credentials. But do not rely on the email being hidden — if that matters for
your install, the only value protected here is the token.

Your publish destination is a separate thing and is not set on this page. The
collection and folder that new cards publish into are configured through Renn —
ask it to set the publish target and it will find the collection for you. See
*Why Renn never writes on its own* for how that confirmation works.

## How it should work

After saving, the status text beside the button should confirm the save, and the
Guru dot in the Connections tab should read as connected the next time the app
checks. A connected Guru also means the Workbench "existing card" list fills
with real cards rather than showing nothing.

Use the same email you sign in to Guru with. A mismatched email is the common
cause of an authentication failure that looks like a bad token.

Saving credentials only stores them. It does not test them. The connection check
that produces the dot runs separately — see *Test connections: reading the
result*.

## If it doesn't

**The Guru dot says not connected right after you saved.** The connection check
runs at startup, so a fresh save is not reflected until the next launch. Relaunch
and look again before treating it as a failure.

**The dot says the authentication failed.** The credentials were found but Guru
rejected them. Re-check the email — it must match your Guru account exactly —
then re-issue the token in Guru and save it again.

**The dot says there are no credentials.** Nothing was stored. Re-enter both
fields and save, and confirm the status text acknowledges it. If the status text
reports a failure to save, flag it — that points at the credential store rather
than at Guru.

**The Workbench cannot list existing cards.** That list is fetched once per
session and only in live mode. If Guru connected after the app started, relaunch.
If Guru is connected and the list is still empty, flag it.

**Publishing fails even though Guru is connected.** The publish destination is
probably unset or points at a collection Guru will not accept writes into. Ask
Renn to set the publish target rather than looking for it in Settings.
