"""App mode registry — integrity, mode resolution, and legacy id mapping.

Pure-logic tests against src/ui/app_modes.py plus introspection that every
registry factory / service hook actually exists on MainWindow.
"""

from unittest.mock import patch

import pytest

from src.ui import app_modes
from src.ui.app_modes import (
    MODE_ENABLEMENT,
    MODE_PRODUCT,
    MODES,
    PAGES,
    SERVICES,
    first_page_id,
    normalize_page_id,
    pages_for_mode,
    resolve_startup_mode,
    services_for_mode,
    spec_for,
)


class TestRegistryIntegrity:
    def test_page_ids_unique(self):
        ids = [s.page_id for s in PAGES]
        assert len(ids) == len(set(ids))

    def test_modes_valid(self):
        for s in PAGES:
            assert s.modes, f"{s.page_id} has no modes"
            assert s.modes <= set(MODES)
        for s in SERVICES:
            assert s.modes <= set(MODES)

    def test_factories_exist_on_main_window(self):
        from src.ui.main_window import MainWindow
        for s in PAGES:
            assert callable(getattr(MainWindow, s.factory, None)), (
                f"{s.page_id}: factory {s.factory} missing on MainWindow"
            )

    def test_service_hooks_exist_on_main_window(self):
        from src.ui.main_window import MainWindow
        for s in SERVICES:
            assert callable(getattr(MainWindow, s.start, None)), (
                f"{s.service_id}: start {s.start} missing"
            )
            if s.stop:
                assert callable(getattr(MainWindow, s.stop, None)), (
                    f"{s.service_id}: stop {s.stop} missing"
                )

    def test_sections_contiguous_per_mode(self):
        """_populate_sidebar emits a section header on change — specs of a
        mode must keep each section contiguous or headers duplicate."""
        for mode in MODES:
            seen, current = set(), None
            for s in pages_for_mode(mode):
                if s.section != current:
                    assert s.section not in seen, (
                        f"{mode}: section {s.section} is split"
                    )
                    seen.add(s.section)
                    current = s.section

    def test_enablement_tab_keys(self):
        for s in pages_for_mode(MODE_ENABLEMENT):
            if s.page_id == "home":
                continue
            assert s.factory == "_create_enablement_page"
            assert s.tab_key in {"calendar", "tasks", "workbench",
                                 "analytics", "powerpoint", "settings"}

    def test_first_page_ids_resolve(self):
        for mode in MODES:
            assert spec_for(first_page_id(mode)) is not None


class TestModeRouting:
    def test_product_pages(self):
        ids = {s.page_id for s in pages_for_mode(MODE_PRODUCT)}
        assert "home" in ids
        assert "conversations" in ids
        assert "settings" in ids
        assert not any(i.startswith("en_") for i in ids)

    def test_enablement_pages(self):
        ids = {s.page_id for s in pages_for_mode(MODE_ENABLEMENT)}
        assert ids == {"home", "en_calendar", "en_tasks", "en_workbench",
                       "en_analytics", "en_powerpoint", "en_settings"}

    def test_home_shared_and_first(self):
        home = spec_for("home")
        assert home is not None
        assert home.modes == frozenset(MODES)
        assert home.section == ""  # renders without a section header
        for mode in MODES:
            assert first_page_id(mode) == "home"

    def test_product_services(self):
        ids = {s.service_id for s in services_for_mode(MODE_PRODUCT)}
        assert "schedule_manager" in ids
        assert "zendesk_monitor" in ids
        assert "enablement_monitor" not in ids

    def test_enablement_services(self):
        ids = {s.service_id for s in services_for_mode(MODE_ENABLEMENT)}
        assert ids == {"guru_wiring", "enablement_monitor"}


class TestNormalizePageId:
    def test_legacy_ints(self):
        assert normalize_page_id(0) == "conversations"
        assert normalize_page_id(4) == "reports"
        assert normalize_page_id(9) == "en_workbench"
        assert normalize_page_id(11) == "data_warehouse"

    def test_unknown_int(self):
        assert normalize_page_id(99) == ""

    def test_string_passthrough(self):
        assert normalize_page_id("trending") == "trending"

    def test_every_legacy_id_resolves(self):
        for page_id in app_modes.LEGACY_PAGE_IDS.values():
            assert spec_for(page_id) is not None


class TestResolveStartupMode:
    @pytest.fixture(autouse=True)
    def _clear_cli_override(self):
        app_modes.set_cli_override(None)
        yield
        app_modes.set_cli_override(None)

    def _resolve(self, app_section):
        with patch("src.data.settings_manager.get_section",
                   return_value=app_section):
            return resolve_startup_mode()

    def test_default_is_product(self):
        assert self._resolve({}) == MODE_PRODUCT

    def test_default_mode_enablement(self):
        assert self._resolve({"default_mode": "enablement"}) == MODE_ENABLEMENT

    def test_last_used(self):
        assert self._resolve(
            {"default_mode": "last", "last_mode": "enablement"}
        ) == MODE_ENABLEMENT

    def test_last_used_falls_back(self):
        assert self._resolve({"default_mode": "last"}) == MODE_PRODUCT
        assert self._resolve(
            {"default_mode": "last", "last_mode": "bogus"}
        ) == MODE_PRODUCT

    def test_invalid_default_falls_back(self):
        assert self._resolve({"default_mode": "bogus"}) == MODE_PRODUCT

    def test_cli_override_wins(self):
        app_modes.set_cli_override("enablement")
        assert self._resolve({"default_mode": "product"}) == MODE_ENABLEMENT

    def test_invalid_cli_override_ignored(self):
        app_modes.set_cli_override("bogus")
        assert self._resolve({}) == MODE_PRODUCT
