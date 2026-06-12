"""E2 — the Publish & Tools strip collapsed into a hamburger menu must emit
the exact same signals as before (signal preservation)."""

import pytest
from PySide6.QtWidgets import QApplication, QMenu

pytestmark = pytest.mark.ui


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _wb(qapp):
    from src.ui.pages.enablement.workbench import WorkbenchPage
    return WorkbenchPage()


def _trigger(menu: QMenu, text: str, _keep=None) -> bool:
    """Depth-first find + trigger in one pass. Retains submenu wrappers in
    `_keep` for the call's duration so PySide doesn't collect a transient
    submenu (and its actions) before we trigger — a test-traversal artifact,
    not an app bug (the live menu triggers fine)."""
    keep = _keep if _keep is not None else [menu]
    for act in menu.actions():
        if act.text().replace("&", "") == text:
            act.trigger()
            return True
        sub = act.menu()
        if sub is not None:
            keep.append(sub)
            if _trigger(sub, text, keep):
                return True
    return False


def _find_action(menu: QMenu, text: str, _keep=None):
    """Return the first matching action; caller must keep `menu` alive."""
    keep = _keep if _keep is not None else [menu]
    for act in menu.actions():
        if act.text().replace("&", "") == text:
            return act
        sub = act.menu()
        if sub is not None:
            keep.append(sub)
            found = _find_action(sub, text, keep)
            if found is not None:
                return found
    return None


def test_tools_menu_exists(qapp):
    wb = _wb(qapp)
    assert hasattr(wb, "_tools_menu")
    assert isinstance(wb._tools_menu, QMenu)


@pytest.mark.parametrize("label,signal_name,expected", [
    ("New Guru card", "publish_requested", "guru_new"),
    ("New Google Doc", "publish_requested", "drive_new"),
    ("Update existing doc", "publish_requested", "drive_update"),
    ("Google Doc / Drive URL…", "import_requested", "drive"),
    ("Existing Guru card…", "import_requested", "guru"),
])
def test_menu_action_emits_signal(qapp, label, signal_name, expected):
    wb = _wb(qapp)
    got = []
    getattr(wb, signal_name).connect(got.append)
    assert _trigger(wb._tools_menu, label), f"menu action {label!r} not found"
    assert got == [expected]


def test_existing_cards_submenu_requests_refresh(qapp):
    wb = _wb(qapp)
    fired = []
    wb.existing_cards_requested.connect(lambda: fired.append(True))
    # the dynamic "Existing Guru card" submenu refreshes on show
    wb._existing_menu.aboutToShow.emit()
    assert fired == [True]


def test_existing_card_action_emits_publish(qapp):
    wb = _wb(qapp)
    got = []
    wb.publish_requested.connect(got.append)
    wb.set_existing_cards([{"id": "card-42", "title": "SSO Setup"}])
    act = _find_action(wb._existing_menu, "SSO Setup")
    assert act is not None
    act.trigger()
    assert got == ["guru_existing:card-42"]
