---
id: settings-ai-provider
title: Choosing your AI provider
section: settings
section_title: Settings and connections
section_order: 8
order: 7
status: available
features: [en_settings, provider_routing]
summary: A provider choice on the Connections tab scopes only your enablement work; a separate routing override in Providers affects everything.
last_verified: 2026-07-20
---

There are two different provider controls in Settings, in two different tabs,
and they do not mean the same thing. Mixing them up is the usual cause of "I
switched provider and nothing changed".

## How it works

**The Connections tab has a provider choice offering Gemini or Claude.** This one
is scoped to enablement work only — card generation, card revision, chat with
Renn, extraction, triage, decks, articles. It has no effect on the product side
of the app.

**The Providers tab has a routing choice** offering automatic per-task defaults,
or forcing everything to one provider. This applies across the whole app.

The enablement choice is checked first, so it wins for enablement work even when
the app-wide routing says something else. Outside enablement, the routing choice
decides.

Two behaviours follow from picking Claude. Enablement work always goes through
the command-line bridge rather than a direct API call, because that is the path
covered by the organisation's agreement. And if a Claude client cannot be built
at all, the app falls back to Gemini instead of returning nothing.

That fallback is narrower than it sounds, and it is easy to over-rely on. It fires
only when no Claude client object can be constructed. Picking Claude when the
command-line tool is present but not logged in does not trigger it: the app builds
the client, tries the call, and the call fails. You get an error at that point,
not a silent switch to Gemini. So a misconfigured Claude usually surfaces as a
failed request, not as invisible Gemini output.

Entering a Claude API key requires acknowledging three statements about routing
support data to Anthropic first. The key field stays disabled until all three are
checked. That gate is intentional.

Enablement work also turns off the aggressive name-redaction pass. Base
redaction — emails, phone numbers, identifiers — always runs regardless, and
cannot be turned off. The aggressive pass rewrites ordinary product terms into
placeholders and ruins card quality, so it is disabled for enablement content,
which is product documentation rather than ticket data.

The Providers tab also carries the active model choice and a toggle for that
aggressive redaction pass on the rest of the app.

## How it should work

Changing either control should take effect on your next request without a
relaunch, and should persist.

Picking Claude for enablement and then generating a card should produce a card.
If the command-line tool is genuinely missing, the fallback covers you and you
still get one from Gemini. But if the tool is installed and simply not signed in,
expect the generation to fail rather than quietly fall back — so a provider change
that produces an error, not silent Gemini output, is the normal signal that Claude
is present but not configured.

The three acknowledgements should stay checked once set, and the key field should
become usable as soon as the last one is checked.

## If it doesn't

**You switched provider and the output seems identical.** This happens only when
Claude could not be built at all and Gemini quietly took over — the switch is
real but has no effect. If instead the command-line tool is installed but not
signed in, you will see a failed request rather than identical output; see the
next entry.

**Generation fails right after switching to Claude.** The most common cause is a
command-line tool that is present but not logged in. The app builds the Claude
client, the call fails, and you get an error instead of a fallback. Sign the tool
in, or switch back to Gemini, then flag it with both provider settings.

**You changed the app-wide routing and Renn did not change.** Expected. The
enablement choice on the Connections tab takes precedence for enablement work.
Change that one instead.

**The Claude key field will not accept input.** All three acknowledgements must
be checked. Look for a hint below the field saying so.

**You expected redaction on enablement content and see product terms passing
through.** Deliberate. Base redaction still runs on emails, phone numbers and
identifiers. If you see a genuine personal identifier reaching a provider, that
is a serious bug — flag it immediately.
