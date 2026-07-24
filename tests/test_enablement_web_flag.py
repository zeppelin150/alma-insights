"""The web rollout flags must degrade safely to their native default.

``enablement.web_tabs`` gates the Calendar/Workbench tabs; ``ui.web_home``
gates the app-level Home page. They are deliberately independent — Home is the
BOOT surface in both modes, so its rollout must be revertible without touching
the enablement one.
"""

import pytest

import src.ui.web.web_flags as wf


def _patch_section(monkeypatch, value):
    import src.data.settings_manager as sm
    monkeypatch.setattr(sm, "get_section",
                        lambda name, default=None: value if name == "enablement" else default)


def _patch_ui(monkeypatch, value):
    import src.data.settings_manager as sm
    monkeypatch.setattr(sm, "get_section",
                        lambda name, default=None: value if name == "ui" else default)


def test_default_is_off(monkeypatch):
    _patch_section(monkeypatch, {})
    assert wf.web_tabs_mode() == "off"


def test_missing_section_is_off(monkeypatch):
    _patch_section(monkeypatch, None)
    assert wf.web_tabs_mode() == "off"


@pytest.mark.parametrize("raw,expected", [
    ("off", "off"),
    ("calendar", "calendar"),
    ("all", "all"),
    ("zendesk", "zendesk"),
    ("  CALENDAR  ", "calendar"),   # whitespace + case tolerated
    ("ALL", "all"),
    ("  Zendesk ", "zendesk"),
])
def test_valid_modes_pass_through(monkeypatch, raw, expected):
    _patch_section(monkeypatch, {"web_tabs": raw})
    assert wf.web_tabs_mode() == expected


@pytest.mark.parametrize("raw", ["on", "true", "workbench", 1, "", "yes", ["all"]])
def test_unrecognized_values_degrade_to_off(monkeypatch, raw):
    _patch_section(monkeypatch, {"web_tabs": raw})
    assert wf.web_tabs_mode() == "off"


def test_settings_error_degrades_to_off(monkeypatch):
    import src.data.settings_manager as sm

    def boom(*_a, **_k):
        raise RuntimeError("settings unavailable")

    monkeypatch.setattr(sm, "get_section", boom)
    assert wf.web_tabs_mode() == "off"


# ── zendesk_web_enabled (the Zendesk tab gate) ───────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("off", False),
    ("calendar", False),           # solo calendar rollout keeps zendesk native
    ("zendesk", True),             # solo zendesk rollout
    ("all", True),
    ("  ZENDESK ", True),
    ("workbench", False),          # unknown degrades to off → native
    ("", False),
    (["zendesk"], False),
])
def test_zendesk_web_enabled_semantics(monkeypatch, raw, expected):
    _patch_section(monkeypatch, {"web_tabs": raw})
    assert wf.zendesk_web_enabled() is expected


def test_zendesk_web_enabled_default_off(monkeypatch):
    _patch_section(monkeypatch, {})
    assert wf.zendesk_web_enabled() is False


def test_zendesk_web_enabled_settings_error_fails_closed(monkeypatch):
    import src.data.settings_manager as sm

    def boom(*_a, **_k):
        raise RuntimeError("settings unavailable")

    monkeypatch.setattr(sm, "get_section", boom)
    assert wf.zendesk_web_enabled() is False


def test_zendesk_web_enabled_tracks_web_tabs_mode(monkeypatch):
    """The helper is a pure view over web_tabs_mode — a module-attr patch of
    web_tabs_mode (the pattern the @ui page tests use) must steer it too."""
    monkeypatch.setattr(wf, "web_tabs_mode", lambda: "zendesk")
    assert wf.zendesk_web_enabled() is True
    monkeypatch.setattr(wf, "web_tabs_mode", lambda: "off")
    assert wf.zendesk_web_enabled() is False


# ── ui.web_home (the Home boot surface) ──────────────────────────────

def test_home_default_is_off(monkeypatch):
    _patch_ui(monkeypatch, {})
    assert wf.web_home_enabled() is False


def test_home_missing_section_is_off(monkeypatch):
    _patch_ui(monkeypatch, None)
    assert wf.web_home_enabled() is False


@pytest.mark.parametrize("raw", ["on", "ON", "  on  ", "true", "TRUE", "yes",
                                 "1", True, 1])
def test_home_truthy_values_enable(monkeypatch, raw):
    _patch_ui(monkeypatch, {"web_home": raw})
    assert wf.web_home_enabled() is True


@pytest.mark.parametrize("raw", ["off", "", "no", "false", False, 0, 2,
                                 ["on"], {"a": 1}, None, "calendar", "onn"])
def test_home_everything_else_is_off(monkeypatch, raw):
    _patch_ui(monkeypatch, {"web_home": raw})
    assert wf.web_home_enabled() is False


def test_home_settings_error_degrades_to_off(monkeypatch):
    import src.data.settings_manager as sm

    def boom(*_a, **_k):
        raise RuntimeError("settings unavailable")

    monkeypatch.setattr(sm, "get_section", boom)
    assert wf.web_home_enabled() is False


def test_flags_are_independent(monkeypatch):
    """web_tabs must not switch Home on, and vice versa — Home is the boot
    surface and its rollout has to be revertible on its own."""
    import src.data.settings_manager as sm
    monkeypatch.setattr(
        sm, "get_section",
        lambda name, default=None: (
            {"web_tabs": "all"} if name == "enablement"
            else ({} if name == "ui" else default)))
    assert wf.web_tabs_mode() == "all"
    assert wf.web_home_enabled() is False


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
