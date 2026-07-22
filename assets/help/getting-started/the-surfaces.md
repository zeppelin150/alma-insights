---
id: getting-started-the-surfaces
title: Where everything lives
section: getting-started
section_title: Getting started
section_order: 1
order: 6
status: partial
features: [en_agent, en_calendar, en_tasks, en_workbench, en_powerpoint, en_zendesk, en_attention, en_analytics, en_help, en_settings]
summary: A tour of the ten sidebar entries on the enablement side, and the one capability that has no screen at all.
last_verified: 2026-07-20
---

The enablement sidebar opens with **Home**, then ten more entries grouped into
five bands. This article is the map: one or two lines each, and where to read
more.

**Home** sits above the bands and has no band heading of its own. It is the
page the app opens on in both product and enablement mode, and it carries the
mode tiles, a few at-a-glance counts, quick actions, and your recent activity.

It is marked partial for one reason — the Content Studio, which generates
diagrams, quizzes, one-pagers and decks, has no sidebar entry and no screen of
its own. It is covered at the end.

## How it works

**Assistant**

- **Agent** — the full-screen chat with Renn. The richest way to reach the
  assistant: voice input, the jobs sidebar for long-running work, pickers, and
  the Confirm cards that gate every write. Start here if you are not sure which
  surface you need, because Renn can usually do the thing for you. See
  *What Renn can do, and what Renn will refuse*.

**Plan**

- **Calendar** — your enablement schedule, driven by your Asana board. Needs
  Asana connected to show anything real.
- **Tasks** — your task list, filtered to you once your identity is set. Open a
  task to see its detail panel and scratchpad.

**Create**

- **Workbench** — where card drafts are written, reviewed and published. The
  editor, the diff against the live Guru card, the evidence behind each change,
  and the publish action all live here. This is the centre of gravity for the
  whole loop.
- **PowerPoint** — model a deck, preview the slides, and export a .pptx file.
- **Zendesk** — the help-centre articles and macros surface, alongside the Guru
  content.

**Insights**

- **Attention Queue** — the ranked list of what needs work: source-changed
  cards, overdue verifications, gaps and duplicates, and drafts you left
  staged. It is the default tab *within* the enablement workspace, so it is
  what you land on when you first move off Home.
- **Guru Analytics** — top cards, verification measures, open comments, and
  cards coming due.

**System**

- **Help** — this Help Center. See *How to read this Help Center*.
- **Settings** — connections, providers and identity, sources, and your style
  guide and card template. See *Your first 15 minutes*.

## How it should work

Nine of the ten entries — everything except **Agent** — select one page of a
single enablement screen, so moving between them keeps your context: the draft
you were editing is still the active draft when you come back to the Workbench.
**Agent is the exception.** It is not a page of that screen; it is a separate
full-screen surface with its own chat session. Switching to Agent and back does
not carry the enablement screen's context with it, and the conversation in the
Agent surface is not the same one as the Workbench's assistant panel.

Renn is available alongside these pages, not only on the Agent entry. The
Workbench has its own assistant panel that picks up whatever draft you are
editing, so you can ask for a change without naming the card. See
*Where Renn appears*.

**The Content Studio has no screen.** Diagram, quiz, one-pager, battle-card and
deck generation exist as things Renn can do, and generated artifacts are
listed and attached through Renn as well — but there is no sidebar entry and no
tab for them. The only way in is to ask Renn. If you are looking for a studio
page, you are not missing it; it is not there.

Two related gaps sit inside that studio. Mermaid diagram previews and quiz
previews are deferred, so generation is ahead of the ability to see the result
in the app. And a podcast artifact type is reserved in the data model with no
implementation behind it at all — asking for a podcast produces nothing.

## If it doesn't

**Calendar or Workbench looks different from a colleague's screen.** There are
two implementations of both, and the newer web versions are behind a setting
that ships off, so you are almost certainly seeing the standard versions. That
is expected. See *Where Renn appears* for what the web versions add.

**Calendar and Tasks are empty.** Almost always Asana. Check the connection
dots in Settings, and read the Asana step in *Your first 15 minutes* — that
step cannot currently be completed from the interface.

**The Attention Queue is empty.** In demo mode, run a scan first. In live mode,
an empty queue can be genuine — it means nothing is flagged — but a queue that
is empty right after a scan is worth flagging.

**You cannot get back to the Attention Queue.** It is the Insights entry in the
sidebar. It is also the page the section opens on, so switching away and back
into enablement returns you there.

**A sidebar entry opens a blank page.** Note which entry and flag it. Blank is
not a documented state for any of the ten.

**You asked Renn for a diagram or quiz and got no preview.** Expected — those
previews are deferred. The artifact itself may still have been created; ask
Renn to list your artifacts to check.
