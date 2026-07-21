---
id: troubleshooting-blank-screen
title: A screen is blank or will not load
section: troubleshooting
section_title: When something goes wrong
section_order: 9
order: 4
status: partial
features: [en_agent, en_help]
summary: Telling a blank web view apart from an empty screen, and what to report for each.
last_verified: 2026-07-20
---

A screen with no content is usually a missing connection (see
*Connect-first messages and empty states*). A screen that is blank *white* —
no layout, no headings, no buttons — is a different and more serious problem.

## How it works

Some surfaces, notably the Agent page, render inside an embedded browser view.
When that view fails to start, you get a blank white panel while the rest of the
app keeps working normally. That contrast is the tell: if the sidebar and the
window chrome look fine but one panel is featureless white, the embedded view
died rather than the app.

This has historically been a platform problem rather than a content problem —
it depends on how the app was installed and signed, not on your data or your
settings.

## How it should work

A failed embedded view should never leave you stranded — every surface falls
back to a plain version when its web view does not come up. The Calendar and
Workbench tabs each drop back to their full native Qt version, so you keep a
working page there even when the web view underneath has failed.

The Agent page now has that safety net too. If its embedded view cannot start
but the assistant itself is fine, it falls back to a plain native chat wired to
the same assistant — no streaming and no side panels, but you can still type
and get answers. Only when the assistant engine cannot be built either do you
get a short line of text explaining that, and nothing else.

Nothing you can do in the app should be able to cause a blank view. If one
appears after a specific action you took, that is unusual and worth saying in
the report.

## If it doesn't

**One panel is blank white, everything else works.** Switch to another screen
and back first — returning to a screen now reloads a web view that failed to
come up, so a transient blank can clear on its own. If it comes back blank every
time you return, restart the app. If it is still blank on every launch, treat it
as the installation-level problem described next.

**It is blank every time, on every launch.** This is an installation-level
problem, not a settings problem. Report it and say which operating system you
are on and how the app was installed, because those are the two facts that
matter most for this class of failure.

**The whole window is blank or the app will not start.** Note the time and
report it. Do not paste log contents into the form — logs on this machine can
contain ticket text. See *What to include in a bug report*.

**A screen looks cut off or text is clipped rather than blank.** That is a
layout problem, not a load failure. Say what your window size is and include
which labels are affected — it is usually reproducible from that alone.
