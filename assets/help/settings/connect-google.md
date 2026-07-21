---
id: settings-connect-google
title: Connecting Google Drive
section: settings
section_title: Settings and connections
section_order: 8
order: 4
status: available
features: [en_settings, google_drive, google_oauth]
summary: Google is deliberately disconnected at every launch — you reconnect once per session, and that is by design rather than a bug.
last_verified: 2026-07-20
---

Google Drive is how the app reads your documents, imports style guides, and
maintains the knowledge base. There are two ways to connect it, and one
behaviour that surprises almost everyone.

## How it works

Both options are in the Providers tab, in the Google Drive card.

**Your own Google account.** A button connects your account through your
browser. You approve read access, and the app stores only a minimal refresh
record in your operating system's credential vault — never the access token
itself. The scopes requested are read-only Drive access plus the ability to
write files the app itself created. Full Drive access is never requested.

**A service account.** A path to a JSON key file, for installs where an
administrator shares folders with a service-account address instead. There is
also an option to supply your own Google Cloud OAuth client if your organisation
requires it.

**The part that surprises people: your Google connection does not survive a
relaunch.** This is deliberate. The app starts every session with Google
disconnected and performs no Google calls at boot. Until you explicitly
reconnect, Drive reads, knowledge base sync and uploads all stay idle. A stolen
credential file is useless without both your OS vault and a deliberate action
inside the app.

Because of that, the card shows three different states. Not connected offers to
connect. Authorized but inactive means your approval is remembered and a
reconnect will be silent — no browser, no re-approval. Connected this session
means Drive is live and offers to disconnect.

## How it should work

The first connection each session should take one click on reconnect and should
not open a browser. The browser only appears on a genuinely first connection, or
after the stored authorization has been revoked or expired.

Once connected, the Drive dot should read as connected, the knowledge base
status should stop reporting Google as unavailable, and importing a document
from Drive should work.

Disconnecting should remove the stored credential, not just end the session. The
next connection after a disconnect will open a browser again.

## If it doesn't

**Drive stopped working after you relaunched the app.** Expected. Reconnect. This
is the disable-on-launch behaviour, not a failure, and it happens every launch.

**The knowledge base status says Google is not connected this session.** Same
cause, same fix. Reconnect, then retry whatever you were doing.

**Reconnect opens a browser every time.** It should be silent when a valid
authorization is stored. A browser on every reconnect means the stored record is
not being kept — check whether your OS credential vault is available, then flag
it.

**Reconnect fails and the state falls back to not connected.** The stored
authorization was rejected, usually because it was revoked in your Google
account settings. Connect again from scratch.

**The card only offers the service account and never mentions your own account.**
The build may not carry an OAuth client. Supplying your own Google Cloud client
is the documented way around that.

**Disconnect reports that the credential is still stored.** The removal failed.
Flag it — a credential that will not clear is worth reporting.

**Drive imports fail while Google shows as connected.** Drive reading is also
gated on an organisation-level setting, and folders must be shared with the
account you connected. See *Managing style guides and card templates*.
