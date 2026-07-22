"""Product-line branding strings shared across layers.

The enablement toolset is presented to users as the **Content Command
Center** — on the startup splash, in the main-window chrome, on the Home
page banner, and in the Help Center article
``assets/help/getting-started/content-command-center.md``.

This module is the single source for those strings. It lives at the top
of ``src`` (like ``src.VERSION``) so every layer may import it:
``src/services`` must not import ``src/ui``, but both may import this.
The Help Center article carries the description as markdown; the claims
test in ``tests/test_help_claims_getting-started.py`` pins it to
``CONTENT_COMMAND_CENTER_DESCRIPTION`` verbatim so the two cannot drift.
"""

CONTENT_COMMAND_CENTER = "Content Command Center"

# Owner-supplied description (2026-07-22). Shown on the enablement Home
# banner and quoted verbatim in the Help Center. Edit it here and the
# claims test will point at every copy that needs to follow.
CONTENT_COMMAND_CENTER_DESCRIPTION = (
    "Content Command Center is an AI-powered workspace that connects "
    "Asana, Guru, and Zendesk, allowing the Content team to review "
    "requests, analyze existing knowledge, generate recommended updates, "
    "and publish content through one centralized workflow."
)
