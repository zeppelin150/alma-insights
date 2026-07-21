---
id: renn-what-it-can-do
title: What Renn can do, and what Renn will refuse
section: renn
section_title: Renn, your assistant
section_order: 2
order: 1
status: available
features: [en_agent, renn]
summary: The short version of everything Renn can do for you, and the three things it will always decline.
last_verified: 2026-07-20
---

Renn is the assistant built into the enablement side of the app. It works on
your content and your task list — finding documents, drafting and revising
Guru cards, managing tasks, and setting up your Asana board.

## How it works

Renn does real work through tools rather than describing what it would do. In
practice it can:

- **Find things** — search your Guru cards, Zendesk articles, Asana tasks, the
  business Drive, and the knowledge base it maintains for you.
- **Work on a card draft** — read the current draft, apply an edit you describe
  in plain language, and publish to Guru when you ask it to.
- **Manage your work** — list tasks, create them, add subtasks, update the
  scratchpad on a task.
- **Set things up** — discover your Asana projects and save a board
  configuration, without ever asking you for an ID.
- **Generate content** — diagrams, knowledge-check quizzes, one-pagers, battle
  cards, and branded decks.

## How it should work

Ask in plain language. Renn picks the tool. You should not need to know tool
names, and you should never be asked for a Drive folder ID or an Asana GID —
if Renn asks you for a raw ID, that is a bug worth flagging.

Renn refuses three things by design:

1. **It will not publish to Guru unless you explicitly ask.** Drafting and
   revising are safe; publishing needs the word.
2. **It cannot perform writes on its own.** Anything that changes Asana, Guru
   folders, or Drive opens a Confirm card and waits for your click. See
   *Why Renn never writes on its own*.
3. **It will not repeat a Google Drive folder name back to you.** Folder names
   can carry identifying information, so Renn refers to "the active folder"
   instead. This is deliberate, not a glitch.

It will also decline background research — that feature is not built yet. See
*Background research is not available yet*.

## If it doesn't

**Renn describes an action instead of doing it** ("I would search for…"). Ask
it again more directly ("search Guru for X"). If it consistently narrates
rather than acts, flag it.

**Renn claims it cannot identify you.** Your identity is supplied to it
automatically. If it says it does not know who you are, check that your
operator email is set under Settings → Connections, then flag it if the
setting is present.

**Renn invents a document or task that does not exist.** Every claim should be
grounded in a tool result from that turn. Fabricated names are a bug — flag it
with the question you asked.
