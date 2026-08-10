"""Pilot A4 (CARD STYLING) + the G1 Guru scope row on the enablement
SettingsPage.

CARD STYLING lives in the Style Guide tab: an enable toggle, heading/link
color swatches (QColorDialog), a preview, and the approval-invalidation
warning. The persisted shape is EXACTLY
``enablement.guru.card_style = {"default": {"enabled", "heading_color",
"link_color"}}`` with defaults OFF / #0055CC. The Guru scope row lives in
the Sources tab and mirrors ``enablement.guru.search_collections`` ("All
collections" when the key is empty — the scope is never silently narrowed).

Headless: settings and keyring are faked; no network, and QColorDialog never
opens (getColor is monkeypatched).
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QCheckBox, QLabel, QPushButton,
)

pytestmark = pytest.mark.ui

_WARNING = ("Changing card styling invalidates in-flight publish approvals — "
            "re-approve after changing colors.")


# ── fixtures / helpers ────────────────────────────────────────────────

@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def fake_settings(monkeypatch):
    """Dict-backed settings_manager so no test touches settings.yaml."""
    state: dict = {}

    def get_section(name, default=None):
        return state.get(name, default if default is not None else {})

    def set_section(name, value):
        state[name] = dict(value)
        return True

    def update_section(name, updates):
        cur = dict(state.get(name) or {})
        cur.update(updates or {})
        state[name] = cur
        return True

    import src.data.settings_manager as sm
    monkeypatch.setattr(sm, "get_section", get_section)
    monkeypatch.setattr(sm, "set_section", set_section)
    monkeypatch.setattr(sm, "update_section", update_section)
    return state


@pytest.fixture()
def fake_keyring(monkeypatch):
    """Patch pat_store so the CredentialsPanel never reads the real vault."""
    import src.data.pat_store as pat_store
    store: dict = {}
    monkeypatch.setattr(
        pat_store, "load_setting",
        lambda key, default=None: store.get(
            key, default if default is not None else ""))
    monkeypatch.setattr(
        pat_store, "save_setting",
        lambda key, value: (store.__setitem__(key, value), True)[1])
    return store


def make_page(qapp):
    from src.ui.pages.enablement.settings import SettingsPage
    return SettingsPage()


@pytest.fixture()
def page(qapp, fake_settings, fake_keyring):
    return make_page(qapp)


def tab(p, name: str):
    for i in range(p._tabs.count()):
        if p._tabs.tabText(i) == name:
            return p._tabs.widget(i)
    raise AssertionError(f"no sub-tab named {name!r}")


def label_texts(w) -> list:
    return [lbl.text() for lbl in w.findChildren(QLabel)]


def card_style_state(fake_settings) -> dict:
    return ((fake_settings.get("enablement") or {}).get("guru") or {}) \
        .get("card_style") or {}


# ── CARD STYLING: defaults ────────────────────────────────────────────

class TestCardStyleDefaults:
    def test_defaults_are_off_and_brand_blue(self, page, fake_settings):
        assert page._style_enabled.isChecked() is False
        assert page._style_heading_color == "#0055CC"
        assert page._style_link_color == "#0055CC"
        # building the page must not write the preset
        assert card_style_state(fake_settings) == {}

    def test_stored_style_rehydrates(self, qapp, fake_settings, fake_keyring):
        fake_settings["enablement"] = {"guru": {"card_style": {"default": {
            "enabled": True, "heading_color": "#AA1122",
            "link_color": "#00AA55"}}}}
        p = make_page(qapp)
        assert p._style_enabled.isChecked() is True
        assert p._style_heading_color == "#AA1122"
        assert p._style_link_color == "#00AA55"

    def test_malformed_stored_colors_fall_back_to_defaults(
            self, qapp, fake_settings, fake_keyring):
        """A non-hex stored value must not reach the rich-text preview."""
        fake_settings["enablement"] = {"guru": {"card_style": {"default": {
            "enabled": True, "heading_color": "javascript:alert(1)",
            "link_color": "blue"}}}}
        p = make_page(qapp)
        assert p._style_heading_color == "#0055CC"
        assert p._style_link_color == "#0055CC"


# ── CARD STYLING: persistence round-trip ──────────────────────────────

class TestCardStylePersistence:
    def test_toggle_round_trips_the_exact_documented_shape(self, page,
                                                           fake_settings):
        page._style_enabled.setChecked(True)
        assert card_style_state(fake_settings) == {"default": {
            "enabled": True, "heading_color": "#0055CC",
            "link_color": "#0055CC"}}
        page._style_enabled.setChecked(False)
        assert card_style_state(fake_settings)["default"]["enabled"] is False

    def test_color_pick_persists_uppercase_hex_and_previews(
            self, page, fake_settings, monkeypatch):
        from PySide6.QtWidgets import QColorDialog
        monkeypatch.setattr(QColorDialog, "getColor",
                            staticmethod(lambda *a, **k: QColor("#aa1122")))
        page._pick_style_color("heading")
        default = card_style_state(fake_settings)["default"]
        assert default["heading_color"] == "#AA1122"
        assert default["link_color"] == "#0055CC"     # untouched swatch kept
        assert "#AA1122" in page._style_preview.text()
        monkeypatch.setattr(QColorDialog, "getColor",
                            staticmethod(lambda *a, **k: QColor("#00aa55")))
        page._pick_style_color("link")
        default = card_style_state(fake_settings)["default"]
        assert default == {"enabled": False, "heading_color": "#AA1122",
                           "link_color": "#00AA55"}

    def test_cancelled_pick_writes_nothing(self, page, fake_settings,
                                           monkeypatch):
        from PySide6.QtWidgets import QColorDialog
        monkeypatch.setattr(QColorDialog, "getColor",
                            staticmethod(lambda *a, **k: QColor()))  # invalid
        page._pick_style_color("heading")
        assert card_style_state(fake_settings) == {}
        assert page._style_heading_color == "#0055CC"

    def test_save_preserves_sibling_keys(self, qapp, fake_settings,
                                         fake_keyring):
        """Future per-collection presets and the publish target live next
        door — the default-preset write must not clobber them."""
        fake_settings["enablement"] = {"guru": {
            "card_style": {"col-9": {"enabled": True}},
            "publish_collection_id": "PC1"}}
        p = make_page(qapp)
        p._style_enabled.setChecked(True)
        guru = fake_settings["enablement"]["guru"]
        assert guru["card_style"]["col-9"] == {"enabled": True}
        assert guru["card_style"]["default"]["enabled"] is True
        assert guru["publish_collection_id"] == "PC1"


# ── CARD STYLING: surface ─────────────────────────────────────────────

class TestCardStyleSurface:
    def test_section_lives_in_the_style_guide_tab(self, page):
        w = tab(page, "Style Guide")
        assert "CARD STYLING" in label_texts(w)
        boxes = [c for c in w.findChildren(QCheckBox)
                 if c.text() == "Apply card styling on publish"]
        assert len(boxes) == 1
        buttons = {b.text() for b in w.findChildren(QPushButton)}
        assert "Heading color…" in buttons and "Link color…" in buttons

    def test_approval_invalidation_warning_is_present(self, page):
        w = tab(page, "Style Guide")
        assert any(_WARNING in t for t in label_texts(w)), (
            "the fingerprint warning must be stated where colors change")


# ── Guru scope row (G1 surface on the Sources tab) ────────────────────

class TestGuruScopeRow:
    def test_scope_row_shows_all_collections_when_key_is_empty(self, page):
        assert page._guru_scope_lbl.text() == "All collections"

    def test_scope_row_lists_selected_names(self, qapp, fake_settings,
                                            fake_keyring):
        fake_settings["enablement"] = {"guru": {"search_collections": [
            {"id": "1", "name": "A"}, {"id": "2", "name": "B"},
            {"id": "3", "name": "C"}]}}
        p = make_page(qapp)
        assert p._guru_scope_lbl.text() == "3 selected: A, B, C"

    def test_scope_row_elides_beyond_three_names(self, qapp, fake_settings,
                                                 fake_keyring):
        fake_settings["enablement"] = {"guru": {"search_collections": [
            {"id": str(i), "name": n}
            for i, n in enumerate("ABCDE")]}}
        p = make_page(qapp)
        assert p._guru_scope_lbl.text() == "5 selected: A, B, C (+2 more)"

    def test_scope_row_states_that_empty_means_all(self, page):
        texts = label_texts(tab(page, "Sources"))
        assert any(
            "Empty = all collections — the scope is never silently narrowed."
            in t for t in texts)

    def test_edit_scope_button_present_on_sources_tab(self, page):
        w = tab(page, "Sources")
        btns = [b for b in w.findChildren(QPushButton)
                if b.text() == "Edit scope…"]
        assert len(btns) == 1

    def test_edit_scope_opens_a_cached_dialog_and_refreshes_the_row(
            self, page, fake_settings, monkeypatch):
        import src.ui.dialogs.guru_collection_picker_dialog as mod
        made = []

        class FakeDlg:
            def __init__(self, parent=None):
                made.append(self)
                self.reloads = 0

            def reload(self):
                self.reloads += 1

            def exec(self):
                # what the real dialog's OK does: persist, then accept
                fake_settings["enablement"] = {"guru": {"search_collections": [
                    {"id": "c1", "name": "Onboarding"}]}}
                return 1

        monkeypatch.setattr(mod, "GuruCollectionPickerDialog", FakeDlg)
        page._on_edit_guru_scope()
        assert page._guru_scope_lbl.text() == "1 selected: Onboarding"
        page._on_edit_guru_scope()
        assert len(made) == 1, "the dialog must be cached and reused"
        assert made[0].reloads == 1


# ── GURU PUBLISH TARGETS (G3): per-kind destination map ───────────────

def targets_state(fake_settings) -> dict:
    return ((fake_settings.get("enablement") or {}).get("guru") or {}) \
        .get("publish_targets") or {}


_COLS = [{"id": "col-a", "name": "Alpha"}, {"id": "col-b", "name": "Beta"}]


class TestGuruPublishTargets:
    def test_all_kinds_render_disabled_until_collections_arrive(self, page):
        assert set(page._kind_combos) == set(page._TARGET_KINDS)
        for combo in page._kind_combos.values():
            assert combo.isEnabled() is False
            assert combo.itemData(0) is None    # Default row

    def test_selection_persists_override_and_default_removes_it(
            self, page, fake_settings):
        page._on_target_collections("targets", _COLS)   # main-thread landing
        combo = page._kind_combos["quiz"]
        assert combo.isEnabled()
        combo.setCurrentIndex(combo.findData("col-b"))
        assert targets_state(fake_settings)["quiz"] == {"collection_id": "col-b"}
        combo.setCurrentIndex(0)                        # back to Default
        assert "quiz" not in targets_state(fake_settings)
        # other kinds untouched throughout
        assert set(targets_state(fake_settings)) <= {"quiz"}

    def test_stored_override_rehydrates_even_if_unlisted(
            self, qapp, fake_settings, fake_keyring):
        fake_settings["enablement"] = {"guru": {"publish_targets": {
            "diagram": {"collection_id": "col-gone"}}}}
        p = make_page(qapp)
        p._on_target_collections("targets", _COLS)
        combo = p._kind_combos["diagram"]
        assert combo.currentData() == "col-gone"        # kept, marked, visible
        assert "not found" in combo.currentText()
        # rehydration must not have written anything new
        assert targets_state(fake_settings) == {
            "diagram": {"collection_id": "col-gone"}}
