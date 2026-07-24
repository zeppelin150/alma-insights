"""Rollout flags for the web surfaces (WebHost-rendered SPA routes).

``enablement.web_tabs`` in ``data/settings.yaml`` — the enablement tabs:

- ``"off"``      — (default) the native Qt tabs render, zero WebEngine cost
- ``"calendar"`` — the Calendar tab hosts the SPA route ``#/calendar``
- ``"zendesk"``  — the Zendesk tab hosts the SPA route ``#/zendesk``
- ``"all"``      — Calendar + Workbench + Zendesk all host their SPA routes

Per-tab semantics:

===========  ========  =========  =======
value        calendar  workbench  zendesk
===========  ========  =========  =======
``off``      native    native     native
``calendar`` web       native     native
``zendesk``  native    native     web
``all``      web       web        web
===========  ========  =========  =======

``ui.web_home`` — the app-level Home page, deliberately a SEPARATE flag:

- ``"off"``  — (default) the native ``HomePage`` widget renders
- ``"on"``   — Home hosts the SPA route ``#/home``

Home is kept off the ``web_tabs`` enum on purpose. It is the *boot* page in
BOTH modes (``app_modes._FIRST_PAGE``), so turning it on starts Chromium at
launch and a blank render is the first thing the user sees — a materially
different risk from a broken tab. Keeping the two rollouts independent means
either can be reverted by a one-line settings change without touching the
other.

Anything unrecognized (or any settings error) degrades to the safe default —
a flag can never take the native surface away by accident. Qt-free so tests
import it headlessly.
"""

from __future__ import annotations

VALID_MODES = ("off", "calendar", "all", "zendesk")

_TRUTHY = ("on", "true", "yes", "1")


def web_tabs_mode() -> str:
    """The active ``enablement.web_tabs`` mode, degraded safely to ``"off"``."""
    try:
        from src.data.settings_manager import get_section
        section = get_section("enablement", {}) or {}
    except Exception:  # noqa: BLE001 — a settings failure must not break launch
        return "off"
    mode = str(section.get("web_tabs", "off")).strip().lower()
    return mode if mode in VALID_MODES else "off"


def zendesk_web_enabled() -> bool:
    """True when the Zendesk tab should render the SPA ``#/zendesk`` route.

    One-liner gate so page.py's branch stays an expression and the semantics
    are testable here: ``zendesk`` (solo rollout) and ``all`` enable it;
    everything else — including every degrade path — keeps the native tab.
    """
    return web_tabs_mode() in ("zendesk", "all")


def web_home_enabled() -> bool:
    """True when Home should render the SPA ``#/home`` route.

    Fails closed: any settings error, any unrecognized value, and every
    non-scalar degrade to ``False`` (the native ``HomePage``). Home is the
    boot surface — an ambiguous flag must never be the reason the first
    screen is a blank Chromium view.
    """
    try:
        from src.data.settings_manager import get_section
        section = get_section("ui", {}) or {}
    except Exception:  # noqa: BLE001 — a settings failure must not break launch
        return False
    raw = section.get("web_home", False)
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return int(raw) == 1
    if isinstance(raw, str):
        return raw.strip().lower() in _TRUTHY
    return False
