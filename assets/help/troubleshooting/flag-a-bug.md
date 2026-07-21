---
id: troubleshooting-flag-a-bug
title: How to flag a bug
section: troubleshooting
section_title: When something goes wrong
section_order: 9
order: 1
status: available
features: [en_help, bug_form_url]
summary: The Flag a bug button opens your team's Asana form in your own browser — nothing is captured or sent automatically.
last_verified: 2026-07-20
---

Every article in this Help Center ends by telling you when something is worth
flagging. This is where that goes.

## How it works

**Flag a bug** appears in the Help Center header, and the **Feedback** button
in the top bar does the same thing. Both open your team's Asana intake form in
your normal web browser — not inside the app.

Nothing is collected on your behalf. The app does not attach logs, take a
screenshot, read your drafts, or send anything anywhere. You fill in the form
yourself, so you can see exactly what you are reporting before it leaves your
machine. That is deliberate: this app handles support tickets that can contain
patient information, and an automatic diagnostic capture is a very easy way to
leak it by accident.

The form address is a setting, so it can be updated without a new release.

## How it should work

Clicking the button should open your browser on the form. If no form address
has been configured yet, you should get a short explanation saying so — never a
dead link or a blank tab.

The form should open **externally**. A help page should never be able to
navigate the app itself somewhere.

## If it doesn't

**A message says no bug-report form has been configured.** That is expected on
a fresh install. An administrator sets the form address in settings
(`enablement.help.bug_form_url`); until then, report bugs however your team
normally does.

**Nothing happens at all when you click.** Check whether a browser window
opened behind the app. If not, this is worth reporting through your usual
channel — the button that reports bugs failing is a bug that cannot report
itself.

**The form opens inside the app instead of your browser.** That is a defect.
Report it.

**You are not sure whether what you found is a bug.** Read
*Renn says it cannot do something* and *Known issues and current limitations*
first — several things that look broken are known and already documented. If it
appears on that list, you do not need to report it again.
