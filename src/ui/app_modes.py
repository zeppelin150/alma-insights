"""App mode registry — which pages and services exist in each mode.

Pure logic, no Qt imports: MainWindow resolves `factory` / `start` / `stop`
attribute names against itself at mount time. Adding a page or service to a
mode means adding a spec here plus the factory method on MainWindow.
"""

from __future__ import annotations

from dataclasses import dataclass, field

MODE_PRODUCT = "product"
MODE_ENABLEMENT = "enablement"
MODES = (MODE_PRODUCT, MODE_ENABLEMENT)

_BOTH = frozenset(MODES)
_PRODUCT = frozenset({MODE_PRODUCT})
_ENABLEMENT = frozenset({MODE_ENABLEMENT})


@dataclass(frozen=True)
class PageSpec:
    page_id: str
    title: str
    icon: str                      # emoji until the design-system icon pass
    section: str
    modes: frozenset
    factory: str                   # MainWindow method name
    tab_key: str | None = None     # selects a tab on a host page (enablement)
    wants_drilldown: bool = False
    hidden: bool = False           # mounted but no visible sidebar button
    settings_flag: tuple | None = None   # (section, key) that must be truthy


@dataclass(frozen=True)
class ServiceSpec:
    service_id: str
    modes: frozenset
    start: str                     # MainWindow method name
    stop: str | None = None


PAGES: tuple[PageSpec, ...] = (
    # ── Shared: landing page (empty section → no header label) ──
    PageSpec("home", "Home", "\U0001F3E0", "",
             _BOTH, "_create_home_page"),
    # ── Product: SOURCES ──
    PageSpec("conversations", "Conversations", "\U0001F4AC", "SOURCES",
             _PRODUCT, "_create_conversations_page", wants_drilldown=True),
    PageSpec("source_monitor", "Source Monitor", "\U0001F4E1", "SOURCES",
             _PRODUCT, "_create_source_monitor_page"),
    PageSpec("guru_legacy", "Guru KB", "\U0001F9F0", "SOURCES",
             _PRODUCT, "_create_legacy_guru_page", wants_drilldown=True,
             settings_flag=("guru", "experimental_ui_enabled")),
    # ── Product: ANALYSIS ──
    PageSpec("trc_analytics", "TRC Analytics", "\U0001F4CA", "ANALYSIS",
             _PRODUCT, "_create_dashboard_page", wants_drilldown=True),
    PageSpec("trending", "Trending Topics", "\U0001F4C8", "ANALYSIS",
             _PRODUCT, "_create_trending_page", wants_drilldown=True),
    PageSpec("incidents", "Incidents", "\U0001F6A8", "ANALYSIS",
             _PRODUCT, "_create_incidents_page", wants_drilldown=True),
    # ── Product: REPORTS ──
    PageSpec("reports", "AI Reports", "\U0001F916", "REPORTS",
             _PRODUCT, "_create_reports_page", wants_drilldown=True),
    PageSpec("ab_compare", "A/B Compare", "\U0001F504", "REPORTS",
             _PRODUCT, "_create_ab_compare_page", wants_drilldown=True,
             hidden=True),
    PageSpec("smart_reporting", "Smart Reporting", "⚡", "REPORTS",
             _PRODUCT, "_create_smart_reporting_page"),
    PageSpec("gemini_chats", "Gemini Chats", "\U0001F4AC", "REPORTS",
             _PRODUCT, "_create_gemini_chats_page", wants_drilldown=True),
    # ── Product: SYSTEM ──
    PageSpec("data_warehouse", "Data Warehouse", "\U0001F5C4️", "SYSTEM",
             _PRODUCT, "_create_data_warehouse_page", wants_drilldown=True),
    PageSpec("settings", "Settings", "⚙️", "SYSTEM",
             _PRODUCT, "_create_settings_page"),
    # ── Enablement (one host page, sidebar entries select tabs) ──
    PageSpec("en_calendar", "Calendar", "\U0001F4C5", "ENABLEMENT",
             _ENABLEMENT, "_create_enablement_page", tab_key="calendar",
             wants_drilldown=True),
    PageSpec("en_tasks", "Tasks", "☑️", "ENABLEMENT",
             _ENABLEMENT, "_create_enablement_page", tab_key="tasks",
             wants_drilldown=True),
    PageSpec("en_workbench", "Workbench", "\U0001F9F0", "ENABLEMENT",
             _ENABLEMENT, "_create_enablement_page", tab_key="workbench",
             wants_drilldown=True),
    PageSpec("en_settings", "Settings", "⚙️", "ENABLEMENT",
             _ENABLEMENT, "_create_enablement_page", tab_key="settings",
             wants_drilldown=True),
)

SERVICES: tuple[ServiceSpec, ...] = (
    ServiceSpec("schedule_manager", _PRODUCT,
                "_start_schedule_manager", "_stop_schedule_manager"),
    ServiceSpec("warehouse_watchlist", _PRODUCT,
                "_setup_warehouse_and_watchlist"),
    ServiceSpec("zendesk_monitor", _PRODUCT,
                "_setup_zendesk_monitor", "_stop_zendesk_monitor"),
    ServiceSpec("guru_wiring", _BOTH,
                "_setup_guru"),
    ServiceSpec("enablement_monitor", _ENABLEMENT,
                "_wire_enablement_monitor", "_stop_enablement_monitor"),
)

# Legacy MainWindow.PAGE_* integer constants → page ids (kept so existing
# callers and tests that pass ints keep working).
LEGACY_PAGE_IDS: dict[int, str] = {
    0: "conversations",
    1: "trc_analytics",
    2: "trending",
    3: "incidents",
    4: "reports",
    5: "ab_compare",
    6: "smart_reporting",
    7: "settings",
    8: "source_monitor",
    9: "en_workbench",      # PAGE_GURU → the enablement workbench
    10: "gemini_chats",
    11: "data_warehouse",
}

_FIRST_PAGE = {MODE_PRODUCT: "home", MODE_ENABLEMENT: "home"}

_current_mode = MODE_PRODUCT
_cli_override: str | None = None


def pages_for_mode(mode: str) -> tuple[PageSpec, ...]:
    return tuple(s for s in PAGES if mode in s.modes)


def services_for_mode(mode: str) -> tuple[ServiceSpec, ...]:
    return tuple(s for s in SERVICES if mode in s.modes)


def spec_for(page_id: str) -> PageSpec | None:
    for s in PAGES:
        if s.page_id == page_id:
            return s
    return None


def normalize_page_id(page) -> str:
    """Accept a string page id or a legacy integer PAGE_* constant."""
    if isinstance(page, int):
        return LEGACY_PAGE_IDS.get(page, "")
    return str(page)


def first_page_id(mode: str) -> str:
    return _FIRST_PAGE.get(mode, "conversations")


def current_mode() -> str:
    return _current_mode


def set_current_mode(mode: str) -> None:
    global _current_mode
    if mode in MODES:
        _current_mode = mode


def set_cli_override(mode: str | None) -> None:
    global _cli_override
    _cli_override = mode if mode in MODES else None


def resolve_startup_mode() -> str:
    """CLI --mode wins; else app.default_mode (honoring 'last'); else product."""
    if _cli_override in MODES:
        return _cli_override
    try:
        from src.data.settings_manager import get_section
        app = get_section("app", {}) or {}
        default = app.get("default_mode", MODE_PRODUCT)
        if default == "last":
            last = app.get("last_mode", MODE_PRODUCT)
            return last if last in MODES else MODE_PRODUCT
        return default if default in MODES else MODE_PRODUCT
    except Exception:
        return MODE_PRODUCT
