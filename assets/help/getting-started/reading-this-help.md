---
id: getting-started-reading-this-help
title: How to read this Help Center
section: getting-started
section_title: Getting started
section_order: 1
order: 3
status: available
features: [en_help]
summary: Badges tell you what actually works; when a badge and the prose disagree, believe the badge.
last_verified: 2026-07-20
---

This Help Center documents a build in progress. Some of what it describes is
finished, some is half-built, some is switched off, and some does not exist
yet. Rather than quietly omit the unfinished parts, every article carries a
status badge that says which it is.

## How it works

Open Help from the sidebar. The left side is a table of contents grouped by
section. Select an article and it renders on the right, with a badge and a
banner above it when the feature is not fully available.

There are three badges. An article with no badge is fully available.

| Badge | What it means |
|---|---|
| PARTIAL | Some of what the article describes works and some does not. The article says which. |
| OFF BY DEFAULT | Built, but behind a setting that ships disabled. You will not see it unless someone turns it on. |
| NOT AVAILABLE YET | Documented so you know it is not there. Nothing in the article is reachable today. |

Articles that carry a badge are also greyed in the table of contents, so you
can see at a glance which parts of a section are real before you read them.

The badge comes from the article's own metadata, not from a sentence someone
remembered to write. That is deliberate: it is what stops this Help Center from
confidently instructing you to click something that cannot work.

**Search** is the box at the top. It is built for whole questions — type "how
do I publish a card to Guru" rather than guessing a keyword. It breaks your
question into words and ranks articles by title, summary, feature name and
body, so word order and phrasing do not have to match. It also matches on the
start of a word, so a truncated query like "publ card guru" still finds the
publishing article. When a search finds nothing, the table of contents stays
exactly where it was and a short line under the box tells you nothing matched;
the article you were reading is left open.

**Flag a bug** is the button beside the search box. It opens your team's bug
report form in your normal browser, outside the app, and remembers which
article you were reading.

## How it should work

**When a badge and the prose disagree, believe the badge.** The prose describes
intent; the badge describes reality. An article can accurately explain how a
feature is supposed to behave and still be marked NOT AVAILABLE YET, and that
combination is not a contradiction — it is the point. It tells you what to
expect when the feature lands, and that today is not that day.

Read the "If it doesn't" section before you conclude something is broken. A
good half of the symptoms documented there are expected behaviour with a known
cause, and knowing that saves you from chasing a setting that was never wrong.

Bug reports should be filled in by you, in your own browser. The app does not
attach logs, screenshots, or document text to a report, and that is on purpose
— it keeps anything sensitive out of the report entirely. Describe what you
did and what happened instead.

## If it doesn't

**Flag a bug says no form has been configured.** Expected on a fresh install.
The form address is a setting an administrator fills in. Until then, report
bugs the way your team normally does.

**The Help Center says no articles are loaded.** The content ships with the
app and loads itself into the database on first view, so an empty Help Center
means that load failed. Restarting is worth a try; if it stays empty,
reinstalling restores the bundled content. Persistent emptiness is worth
flagging.

**Search returns nothing for a phrase you can see in an article.** Search
matches each word from the start, so a typed word or a partial word finds the
articles that begin a word the same way — but a fragment from the *middle* of a
word (searching "lish" hoping to reach "publish") matches nothing, and an
unusual hyphenation can split a word in a way you did not expect. Try fewer,
more distinctive words, typed from their beginning. Repeated misses on obvious
phrases are worth flagging with the exact query.

**An article describes a button you cannot find.** Check the badge first — a
PARTIAL or OFF BY DEFAULT article routinely describes controls that are not on
your screen. If the article has no badge and the control genuinely is not
there, that is a documentation bug worth flagging with the article title.

**Something works that an article says does not.** Also worth flagging. A
stale NOT AVAILABLE YET badge costs people real work, because they stop looking
for a feature they actually have.
