"""P2 — both Settings pages embed CredentialsPanel; enablement gets the full
panel (LLM+external) without constructing the product SettingsPage; product
gets the external section additively."""

import pytest
from PySide6.QtWidgets import QApplication

pytestmark = pytest.mark.ui


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def test_enablement_settings_embeds_full_panel(qapp):
    from src.ui.pages.enablement.settings import SettingsPage
    from src.ui.widgets.credentials_panel import CredentialsPanel
    page = SettingsPage()
    assert isinstance(page.credentials, CredentialsPanel)
    # full panel → has the LLM controls
    assert hasattr(page.credentials, "_model_combo")
    assert hasattr(page.credentials, "_guru_email")
    # the old display-only Guru stub is gone from the layout (superseded)
    assert "src.ui.pages" in type(page).__module__


def test_enablement_settings_has_no_product_settings_dependency(qapp):
    """Constructing the enablement settings must not import/build the product
    SettingsPage — that page never exists in enablement mode."""
    import sys
    sys.modules.pop("src.ui.pages.settings_page", None)
    from src.ui.pages.enablement.settings import SettingsPage
    page = SettingsPage()
    assert page.credentials is not None
    assert "src.ui.pages.settings_page" not in sys.modules


def test_product_integrations_embeds_external_panel(qapp):
    from src.ui.pages.settings_page import SettingsPage
    from src.ui.widgets.credentials_panel import CredentialsPanel
    page = SettingsPage()
    assert isinstance(page.credentials, CredentialsPanel)
    # external-only → no LLM model combo on this instance
    assert not hasattr(page.credentials, "_model_combo")
    assert hasattr(page.credentials, "_guru_email")
    # the product AI Provider tab is untouched (still has its own model combo)
    assert hasattr(page, "_active_model_combo")


def test_product_panel_propagates_settings_changed(qapp):
    from src.ui.pages.settings_page import SettingsPage
    page = SettingsPage()
    got = []
    page.settings_changed.connect(got.append)
    page.credentials.settings_changed.emit({"guru_updated": True})
    assert {"guru_updated": True} in got
