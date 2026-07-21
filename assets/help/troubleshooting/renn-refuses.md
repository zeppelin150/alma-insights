---
id: troubleshooting-renn-refuses
title: Renn says it cannot do something
section: troubleshooting
section_title: When something goes wrong
section_order: 9
order: 5
status: available
features: [renn]
summary: How to tell a designed refusal from a real failure, and which ones are worth reporting.
last_verified: 2026-07-20
---

Renn declines things for several different reasons, and they are not equally
interesting. Three are working as intended; two are worth reporting.

## How it works

**Designed refusals — not bugs:**

- **"I need you to confirm that."** Anything that changes Asana, creates or
  renames a Guru folder, or uploads to Drive is a proposal, not an action. Renn
  cannot execute it; your click does. See *Why Renn never writes on its own*.
- **"I can't research that."** Background research does not exist in this
  build. Renn is instructed to say so and offer to search your existing content
  instead. See *Background research is not available yet*.
- **Refusing to name a Drive folder.** Renn refers to "the active folder"
  rather than repeating folder names, because folder names can carry
  identifying information. This is deliberate.

**Real problems — worth reporting:**

- **Renn claims something is done when it is not.** Especially "research is
  running", "I've queued that", or "I'll follow up later" — it has no mechanism
  to come back to you asynchronously.
- **Renn asks you for a raw ID.** A project GID, a folder ID, a card ID. It is
  built to resolve those itself through a picker.

## How it should work

A refusal should say *why*, and offer the nearest thing Renn can actually do.
"I can't run research, but I can search Guru, Zendesk and Drive for what we
already have" is the correct shape.

A refusal should never be silent. If you ask for something and Renn simply
changes the subject or produces an unrelated answer, treat that as a failure
rather than a refusal.

## If it doesn't

**Renn refuses something this Help Center says works.** Check the article's
status badge first — a badge saying PARTIAL, OFF BY DEFAULT or NOT AVAILABLE
YET means the Help Center already agrees with Renn. If the article says
available and Renn refuses, flag it and quote both.

**Renn mentions a capability that does not appear anywhere in this Help
Center** — running code, reading files, browsing the web. Report it with the
transcript.

**Renn invents a document, task or card that does not exist.** Every claim is
supposed to be grounded in a result from that turn. Fabrication is a serious
bug; include the question you asked.

**Renn keeps picking the wrong approach for a phrasing you use often.** Worth
reporting even though nothing is technically broken — the phrasing is the
useful part, because the fix is usually in how a capability is described.
