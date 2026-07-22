"""
Alma Insights — Main Application Window
Top bar, sidebar navigation, stacked content pages.

Build 10.0: T10 (collapsible sidebar), T16 (page transitions), T17 (toast wiring)
"""

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QFrame, QStackedWidget, QStatusBar, QSizePolicy,
    QSpacerItem, QApplication, QGraphicsOpacityEffect, QScrollArea,
)
from PySide6.QtCore import (
    Qt, QSize, QTimer, QPropertyAnimation, QEasingCurve,
    QAbstractAnimation,
)
from PySide6.QtGui import QIcon, QFont

from src.ui.theme import *
from src.ui import app_modes
from src.ui.design.anim import DUR, EASE
from src.ui.dialogs.help_dialog import HelpDialog

_SIDEBAR_ICON_COLOR = "#E7E4DC"  # light glyphs on the dark green sidebar
from src.data.db_manager import DatabaseManager
from src.data.settings_manager import get_section
from src.data.job_queue import JobQueue, JobDescriptor
from src.ui.widgets.job_overlay import JobOverlay
from src.ui.widgets.drilldown_panel import DrilldownPanel
from src.ui.widgets.toast import ToastManager


class MainWindow(QMainWindow):
    """Top-level application window.

    Hosts the sidebar navigation, stacked content pages, status bar,
    and overlays (job overlay, drilldown panel, toast manager). Owns
    the singleton `DatabaseManager` and central `JobQueue`.

    Pages and background services are mode-gated: the registry in
    `src.ui.app_modes` declares which pages exist per mode ("product" /
    "enablement"); page widgets are constructed by the `_create_*`
    factory methods on first mount of their mode. The legacy integer
    PAGE_* constants are kept as aliases — `_set_active_page` accepts
    both ints and string page ids.
    """

    PAGE_CONVERSATIONS = 0
    PAGE_DASHBOARD = 1
    PAGE_TRENDING = 2
    PAGE_INCIDENTS = 3
    PAGE_REPORTS = 4
    PAGE_AB_COMPARE = 5
    PAGE_SMART_REPORTING = 6
    PAGE_SETTINGS = 7
    PAGE_SOURCE_MONITOR = 8
    PAGE_GURU = 9
    PAGE_GEMINI_CHATS = 10  # Build 11.0
    PAGE_DATA_WAREHOUSE = 11  # Session 4: Data Warehouse

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Alma Insights")
        self.setMinimumSize(1200, 750)
        self.resize(1400, 850)

        # Database
        self.db = DatabaseManager()
        self.db.initialize()

        # Track sidebar buttons
        self._sidebar_buttons = []
        self._active_page = None
        self._sidebar_collapsed = False   # Build 10.0: T10
        self._sidebar_text_widgets = []   # labels/sections hidden when collapsed
        self._page_anim = None            # Build 10.0: T16 — page fade ref
        self._page_widgets = {}           # page_id → widget for setCurrentWidget
        self._factory_widgets = {}        # factory name → widget (dedups shared hosts)
        self._mounted_modes = set()
        self._product_wiring_done = False
        self._source_warehouse = None
        self._watchlist_engine = None
        self._zendesk_monitor = None

        self._mode = app_modes.resolve_startup_mode()
        app_modes.set_current_mode(self._mode)

        self._build_ui()

        # Unified job queue and loading overlay
        self._job_queue = JobQueue(self)
        self._job_overlay = JobOverlay(self.content_stack)
        self.content_stack.installEventFilter(self._job_overlay)
        self._wire_job_queue()

        # Toast notification manager (Build 10.0: T9/T17)
        self._toasts = ToastManager(self)

        # Universal drill-down panel (overlay drawer) — created before any
        # page mounts so factories can attach it. Parented to the ContentArea
        # frame (NOT the QStackedWidget) so it overlays the content area cleanly
        # in every mode instead of popping out as a stray top-level window.
        self._drilldown = DrilldownPanel(self._content_area)

        self._mount_mode_pages(self._mode)
        self._populate_sidebar(self._mode)
        self._start_services_for_mode(self._mode)
        if self._mode == app_modes.MODE_PRODUCT:
            self._after_product_pages_mounted()
        self._set_active_page(app_modes.first_page_id(self._mode))

    def _build_ui(self):
        # Central widget
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # ── Top Bar ──
        main_layout.addWidget(self._build_top_bar())

        # ── Body: Sidebar + Content ──
        # Build content area FIRST so content_stack exists when sidebar calls _set_active_page
        content_widget = self._build_content_area()

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        body.addWidget(self._build_sidebar())
        body.addWidget(content_widget, 1)

        body_widget = QWidget()
        body_widget.setLayout(body)
        main_layout.addWidget(body_widget, 1)

        # ── Status Bar ──
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_label = QLabel("Ready")
        self.status_bar.addWidget(self.status_label)

        self.layman_mode_indicator = QLabel("")
        self.layman_mode_indicator.setStyleSheet(
            "font-size: 11px; color: #7A7A7A; padding: 0 8px;"
        )
        self.status_bar.addPermanentWidget(self.layman_mode_indicator)

        self.ticket_count_label = QLabel("")
        self.status_bar.addPermanentWidget(self.ticket_count_label)

        # Qt error guard indicator (hidden until errors occur, click to view)
        self.qt_error_label = QLabel("")
        self.qt_error_label.setStyleSheet("font-size: 11px; padding: 0 8px;")
        self.qt_error_label.setCursor(Qt.PointingHandCursor)
        self.qt_error_label.mousePressEvent = self._show_qt_errors
        self.status_bar.addPermanentWidget(self.qt_error_label)

    # ═══════════════════════════════════════════
    #  TOP BAR
    # ═══════════════════════════════════════════

    def _build_top_bar(self):
        bar = QFrame()
        bar.setObjectName("TopBar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(20, 0, 20, 0)
        layout.setSpacing(12)

        # Logo / Title
        title = QLabel("Alma Insights")
        title.setObjectName("TopBarTitle")
        layout.addWidget(title)

        # Mode-aware: "RCM Issue Analysis" in product, the Content Command
        # Center in enablement. Kept as an attribute so switch_mode can
        # retext it without rebuilding the bar.
        self._top_bar_subtitle = QLabel(self._chrome_subtitle(self._mode))
        self._top_bar_subtitle.setObjectName("TopBarSubtitle")
        layout.addWidget(self._top_bar_subtitle)

        layout.addStretch()

        # Help button
        help_btn = QPushButton("Help & Docs")
        help_btn.setObjectName("TopBarButton")
        help_btn.setToolTip("Documentation, feedback, and tool overview")
        help_btn.setCursor(Qt.PointingHandCursor)
        help_btn.clicked.connect(self._show_help)
        layout.addWidget(help_btn)

        # Feedback button
        feedback_btn = QPushButton("Feedback")
        feedback_btn.setObjectName("TopBarButton")
        feedback_btn.setToolTip("Submit feedback via Typeform")
        feedback_btn.setCursor(Qt.PointingHandCursor)
        feedback_btn.clicked.connect(self._show_feedback)
        layout.addWidget(feedback_btn)

        return bar

    # ── Mode-aware chrome (top-bar subtitle + sidebar footer) ──

    def _chrome_subtitle(self, mode):
        from src.branding import CONTENT_COMMAND_CENTER
        if mode == app_modes.MODE_ENABLEMENT:
            return CONTENT_COMMAND_CENTER
        return "RCM Issue Analysis"

    def _sidebar_footer_text(self, mode):
        from src import VERSION
        from src.branding import CONTENT_COMMAND_CENTER
        label = (CONTENT_COMMAND_CENTER
                 if mode == app_modes.MODE_ENABLEMENT else "RCM Operations")
        return f"v{VERSION} — {label} · Drive + local search"

    def _apply_mode_chrome(self, mode):
        """Retext the static chrome after a mode switch."""
        if getattr(self, "_top_bar_subtitle", None) is not None:
            self._top_bar_subtitle.setText(self._chrome_subtitle(mode))
        if getattr(self, "_sidebar_footer", None) is not None:
            self._sidebar_footer.setText(self._sidebar_footer_text(mode))

    # ═══════════════════════════════════════════
    #  SIDEBAR
    # ═══════════════════════════════════════════

    def _build_sidebar(self):
        self._sidebar = QFrame()
        self._sidebar.setObjectName("Sidebar")
        layout = QVBoxLayout(self._sidebar)
        layout.setContentsMargins(0, 12, 0, 12)
        layout.setSpacing(0)

        self._sidebar_text_widgets = []   # Section labels + footer (hidden on collapse)
        self._sidebar_dividers = []        # Dividers (stay visible, just shrink margins)

        # Nav entries live in a sub-layout so a mode switch can rebuild
        # them without touching the collapse button / footer chrome.
        # Populated per mode by `_populate_sidebar`.
        #
        # The sub-layout sits in a QScrollArea: the nav list keeps growing
        # (eleven entries in enablement as of the Help Center), and without a
        # scroll area the only way to fit a long list into a short window is
        # to squeeze or clip the entries — which is exactly what truncated the
        # labels. Overflow now scrolls and every entry keeps its full height.
        nav_host = QWidget()
        nav_host.setObjectName("SidebarNavHost")
        self._sidebar_nav = QVBoxLayout(nav_host)
        self._sidebar_nav.setContentsMargins(0, 0, 0, 0)
        self._sidebar_nav.setSpacing(0)

        self._sidebar_scroll = QScrollArea()
        self._sidebar_scroll.setObjectName("SidebarScroll")
        self._sidebar_scroll.setWidget(nav_host)
        self._sidebar_scroll.setWidgetResizable(True)
        self._sidebar_scroll.setFrameShape(QFrame.NoFrame)
        self._sidebar_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarAlwaysOff)
        self._sidebar_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        layout.addWidget(self._sidebar_scroll, 1)

        # ── Collapse toggle (Build 10.0: T10) ──
        from src.ui.design.icons import icon as design_icon
        self._collapse_btn = QPushButton("Collapse")
        self._collapse_btn.setObjectName("SidebarCollapseBtn")
        self._collapse_btn.setCursor(Qt.PointingHandCursor)
        self._collapse_btn.setIcon(
            design_icon("chevron-left", 16, _SIDEBAR_ICON_COLOR)
        )
        self._collapse_btn.setIconSize(QSize(16, 16))
        self._collapse_btn.clicked.connect(self._toggle_sidebar)
        layout.addWidget(self._collapse_btn)

        # Footer (mode-aware — see _sidebar_footer_text)
        self._sidebar_footer = QLabel(self._sidebar_footer_text(self._mode))
        self._sidebar_footer.setObjectName("SidebarFooter")
        layout.addWidget(self._sidebar_footer)
        self._sidebar_text_widgets.append(self._sidebar_footer)

        return self._sidebar

    def _populate_sidebar(self, mode):
        """(Re)build the sidebar nav entries for `mode` from the registry."""
        nav = self._sidebar_nav
        while nav.count():
            item = nav.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()
                w.deleteLater()

        self._sidebar_buttons = []
        self._sidebar_dividers = []
        self._sidebar_text_widgets = [self._sidebar_footer]

        current_section = None
        for spec in app_modes.pages_for_mode(mode):
            if not self._spec_enabled(spec):
                continue
            if spec.section != current_section:
                if current_section is not None:
                    div = QFrame()
                    div.setObjectName("SidebarDivider")
                    nav.addWidget(div)
                    self._sidebar_dividers.append(div)
                if spec.section:  # empty section (Home) renders no header
                    section_label = QLabel(spec.section)
                    section_label.setObjectName("SidebarSection")
                    nav.addWidget(section_label)
                    self._sidebar_text_widgets.append(section_label)
                current_section = spec.section
            btn = self._sidebar_btn(spec.title, spec.page_id, spec.icon)
            if spec.hidden:
                btn.setVisible(False)
            nav.addWidget(btn)

        # Trailing stretch keeps the entries top-aligned inside the scroll
        # area's resizable host (added here, not at construction, because the
        # clear loop above drops it on every rebuild).
        nav.addStretch()

        # Re-apply collapsed presentation to freshly built entries
        if self._sidebar_collapsed:
            for btn, _pid in self._sidebar_buttons:
                btn.setText(btn.property("icon_char") or "")
                btn.setProperty("collapsed", "true")
                btn.style().unpolish(btn)
                btn.style().polish(btn)
            for w in self._sidebar_text_widgets:
                w.setVisible(False)
            for div in self._sidebar_dividers:
                div.setProperty("collapsed", "true")
                div.style().unpolish(div)
                div.style().polish(div)

        # Re-assert the active highlight after a rebuild
        if self._active_page:
            for btn, pid in self._sidebar_buttons:
                btn.setProperty(
                    "active", "true" if pid == self._active_page else "false"
                )
                btn.style().unpolish(btn)
                btn.style().polish(btn)

    def _sidebar_btn(self, text, page_id, icon_name=None):
        btn = QPushButton(text)
        btn.setObjectName("SidebarButton")
        btn.setCursor(Qt.PointingHandCursor)
        btn.clicked.connect(lambda: self._set_active_page(page_id))
        if icon_name:
            from src.ui.design.icons import icon as design_icon
            btn.setIcon(design_icon(icon_name, 18, _SIDEBAR_ICON_COLOR))
            btn.setIconSize(QSize(18, 18))
        # full_text restores the label after collapse/expand; icon_char is
        # "" since glyphs are QIcons now (collapsed buttons show icon only).
        btn.setProperty("full_text", text)
        btn.setProperty("icon_char", "")
        self._sidebar_buttons.append((btn, page_id))
        return btn

    def _set_active_page(self, page):
        """Show a page. Accepts a string page id or a legacy PAGE_* int."""
        page_id = app_modes.normalize_page_id(page)
        widget = self._page_widgets.get(page_id)
        if widget is None:
            return
        spec = app_modes.spec_for(page_id)
        same_widget = self.content_stack.currentWidget() is widget

        self._active_page = page_id
        self.content_stack.setCurrentWidget(widget)
        # The native HomePage re-queries on showEvent; a WebHost has no such
        # hook, so returning to Home must re-push or stats/activity go stale.
        if page_id == "home" and getattr(self, "_home_web", None) is not None:
            try:
                self._home_web[0].refresh()
            except Exception:
                pass
        # Pages are built once into the stack, so returning to one shows a
        # possibly-stale surface. Give each page an opt-in refresh hook that
        # fires when it becomes active — pages with live data (the enablement
        # page) re-read on return instead of sitting stale. Skipped when the
        # same widget was already showing (a tab-select within it).
        if not same_widget:
            hook = getattr(widget, "on_page_shown", None)
            if callable(hook):
                try:
                    hook()
                except Exception:  # noqa: BLE001 — a refresh must never break nav
                    pass
        if spec is not None and spec.tab_key:
            try:
                widget.select_tab(spec.tab_key)
            except Exception:
                pass

        # Re-raise job overlay after page switch (QStackedWidget repaints
        # the new page on top, which can obscure the overlay)
        if hasattr(self, '_job_overlay') and self._job_overlay.isVisible():
            self._job_overlay.raise_()

        # Close drill-down panel on page navigation — but not when switching
        # tabs on the same host widget (keeps the enablement chat open).
        if (not same_widget and hasattr(self, '_drilldown')
                and self._drilldown.is_open()):
            self._drilldown.close_panel()

        # Update button states — force full stylesheet re-evaluation
        # PySide6 unpolish/polish can miss dynamic property changes, so we
        # also poke setStyleSheet("") to clear any stale inline cache.
        for btn, pid in self._sidebar_buttons:
            btn.setProperty("active", "true" if pid == page_id else "false")
            btn.setStyleSheet("")          # clear any inline overrides
            btn.style().unpolish(btn)
            btn.style().polish(btn)
            btn.update()

        # ── Page transition fade-in (Build 10.0: T16) ── skipped when only
        # switching tabs on the same host widget (no full-page change).
        if not same_widget:
            from src.ui.design.anim import fade_in
            self._page_anim = fade_in(widget)

    # ── Sidebar Collapse / Expand (Build 10.0: T10) ──────────

    def _toggle_sidebar(self):
        """Animate sidebar between expanded (220px) and collapsed (56px)."""
        self._sidebar_collapsed = not self._sidebar_collapsed
        target_width = 56 if self._sidebar_collapsed else 220

        anim = QPropertyAnimation(self._sidebar, b"maximumWidth")
        anim.setDuration(DUR["slow"])
        anim.setStartValue(self._sidebar.maximumWidth())
        anim.setEndValue(target_width)
        anim.setEasingCurve(EASE["out"])

        # Also animate minimumWidth to keep them in sync
        anim2 = QPropertyAnimation(self._sidebar, b"minimumWidth")
        anim2.setDuration(DUR["slow"])
        anim2.setStartValue(self._sidebar.minimumWidth())
        anim2.setEndValue(target_width)
        anim2.setEasingCurve(EASE["out"])

        # Store refs to prevent GC
        self._sidebar_anim = anim
        self._sidebar_anim2 = anim2

        if self._sidebar_collapsed:
            # Collapse: switch to icon-only, centered.
            # We must call unpolish/polish PER BUTTON — the bulk
            # restyle on self._sidebar does not cascade into children,
            # and setStyleSheet("") alone does not trigger re-evaluation
            # of dynamic property selectors like [collapsed="true"].
            # 12 buttons × (unpolish + polish) is well under 1ms total.
            for btn, _ in self._sidebar_buttons:
                icon = btn.property("icon_char") or ""
                btn.setText(icon)
                btn.setProperty("collapsed", "true")
                btn.style().unpolish(btn)
                btn.style().polish(btn)
            for w in self._sidebar_text_widgets:
                w.setVisible(False)
            for div in self._sidebar_dividers:
                div.setProperty("collapsed", "true")
                div.style().unpolish(div)
                div.style().polish(div)
            from src.ui.design.icons import icon as design_icon
            self._collapse_btn.setText("")
            self._collapse_btn.setIcon(
                design_icon("chevron-right", 16, _SIDEBAR_ICON_COLOR)
            )
            self._collapse_btn.setProperty("collapsed", "true")
            self._collapse_btn.style().unpolish(self._collapse_btn)
            self._collapse_btn.style().polish(self._collapse_btn)
        else:
            # Expand: restore full text after animation finishes
            from src.ui.design.icons import icon as design_icon
            anim.finished.connect(self._restore_sidebar_text)
            self._collapse_btn.setText("Collapse")
            self._collapse_btn.setIcon(
                design_icon("chevron-left", 16, _SIDEBAR_ICON_COLOR)
            )
            self._collapse_btn.setProperty("collapsed", "false")

        # One bulk re-style instead of per-widget unpolish/polish
        self._sidebar.style().unpolish(self._sidebar)
        self._sidebar.style().polish(self._sidebar)

        anim.start(QAbstractAnimation.KeepWhenStopped)
        anim2.start(QAbstractAnimation.KeepWhenStopped)

    def _restore_sidebar_text(self):
        """Restore full sidebar button text after expand animation."""
        for btn, _ in self._sidebar_buttons:
            full = btn.property("full_text") or ""
            btn.setText(full)
            btn.setProperty("collapsed", "false")
            btn.style().unpolish(btn)
            btn.style().polish(btn)
        for w in self._sidebar_text_widgets:
            w.setVisible(True)
        for div in self._sidebar_dividers:
            div.setProperty("collapsed", "false")
            div.style().unpolish(div)
            div.style().polish(div)
        self._collapse_btn.setProperty("collapsed", "false")
        self._collapse_btn.style().unpolish(self._collapse_btn)
        self._collapse_btn.style().polish(self._collapse_btn)
        # One bulk re-style on the container for good measure
        self._sidebar.style().unpolish(self._sidebar)
        self._sidebar.style().polish(self._sidebar)

    # ═══════════════════════════════════════════
    #  CONTENT AREA
    # ═══════════════════════════════════════════

    def _build_content_area(self):
        content = QFrame()
        content.setObjectName("ContentArea")
        # Stable, always-visible parent for the drill-down overlay. Parenting the
        # overlay to the QStackedWidget below made it misbehave (a QStackedWidget
        # manages its children's visibility, so a non-page overlay child could
        # render as a stray top-level window in some modes). The frame is the
        # correct, layout-stable host.
        self._content_area = content
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.content_stack = QStackedWidget()
        layout.addWidget(self.content_stack)
        return content

    # ═══════════════════════════════════════════
    #  MODE-GATED PAGE MOUNTING + SERVICES
    # ═══════════════════════════════════════════

    def _spec_enabled(self, spec) -> bool:
        """A spec may be gated behind a truthy settings flag."""
        if spec.settings_flag:
            try:
                section, key = spec.settings_flag
                cfg = get_section(section, {}) or {}
                return bool(cfg.get(key, False))
            except Exception:
                return False
        return True

    def _mount_mode_pages(self, mode):
        """Construct and stack the pages of `mode` (once per mode).

        Several specs may share one factory (the enablement tabs all host
        on EnablementPage); `_factory_widgets` dedups so the widget is
        built once and registered under every page id that targets it.
        """
        if mode in self._mounted_modes:
            return
        for spec in app_modes.pages_for_mode(mode):
            if not self._spec_enabled(spec):
                continue
            widget = self._factory_widgets.get(spec.factory)
            if widget is None:
                widget = getattr(self, spec.factory)()
                self._factory_widgets[spec.factory] = widget
                self.content_stack.addWidget(widget)
                if spec.wants_drilldown:
                    try:
                        widget.set_drilldown_panel(self._drilldown)
                    except Exception:
                        pass
            self._page_widgets[spec.page_id] = widget
        self._mounted_modes.add(mode)

    def _start_services_for_mode(self, mode):
        for svc in app_modes.services_for_mode(mode):
            try:
                getattr(self, svc.start)()
            except Exception as exc:
                import logging
                logging.getLogger("alma.main").warning(
                    "service %s start failed: %s", svc.service_id, exc
                )

    def _stop_services_for_mode(self, outgoing, incoming):
        """Stop stoppable services exclusive to the outgoing mode."""
        for svc in app_modes.services_for_mode(outgoing):
            if incoming in svc.modes or not svc.stop:
                continue
            try:
                getattr(self, svc.stop)()
            except Exception as exc:
                import logging
                logging.getLogger("alma.main").warning(
                    "service %s stop failed: %s", svc.service_id, exc
                )

    def switch_mode(self, mode):
        """Runtime mode toggle: lazy-mounts the target mode's pages on
        first use, swaps the sidebar, and starts/stops mode-exclusive
        services. Already-built pages persist for the session."""
        if mode not in app_modes.MODES or mode == self._mode:
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            outgoing = self._mode
            self._stop_services_for_mode(outgoing, mode)
            self._mode = mode
            app_modes.set_current_mode(mode)
            self._mount_mode_pages(mode)
            self._populate_sidebar(mode)
            self._apply_mode_chrome(mode)
            self._start_services_for_mode(mode)
            if getattr(self, "home_page", None) is not None:
                self.home_page.set_mode(mode)
            if mode == app_modes.MODE_PRODUCT:
                self._after_product_pages_mounted()
            try:
                from src.data.settings_manager import update_section
                update_section("app", {"last_mode": mode})
            except Exception:
                pass
            self._set_active_page(app_modes.first_page_id(mode))
        finally:
            QApplication.restoreOverrideCursor()

    def _after_product_pages_mounted(self):
        """One-time product wiring: cross-page sync, settings restore,
        initial data load (incl. the launch auto-search)."""
        if self._product_wiring_done:
            return
        self._product_wiring_done = True
        self._setup_calendar_sync()
        self._restore_settings()
        self._load_data()

    # ── Page factories ──────────────────────────────────────────────
    # One per page; imports are deferred so a mode never loads the other
    # mode's page modules.

    def _create_home_page(self):
        """Home: the React/WebHost build behind ``ui.web_home``, else the
        native Qt HomePage.

        The controller mirrors HomePage's surface (``set_mode`` + the same
        three signals), so every connect below and every route in
        ``_on_home_quick_action`` / ``_on_home_activity`` works identically
        against either. Home is the BOOT page in both modes, so the web branch
        is belt-and-braces: any construction failure falls through to native,
        and a load failure or a page that never mounts swaps back at runtime —
        a broken tab is a nuisance, a broken Home looks like a dead app.
        """
        self._home_web = None
        if self._web_home_wanted():
            host = self._build_web_home()
            if host is not None:
                return host
        return self._build_native_home()

    def _web_home_wanted(self) -> bool:
        # Imported lazily: a settings/WebEngine problem must never break launch.
        try:
            from src.ui.web.web_flags import web_home_enabled
            return web_home_enabled()
        except Exception:
            return False

    def _build_native_home(self):
        from src.ui.pages.home_page import HomePage
        self.home_page = HomePage(self.db, current_mode=self._mode)
        self.home_page.mode_selected.connect(self.switch_mode)
        self.home_page.quick_action.connect(self._on_home_quick_action)
        self.home_page.activity_activated.connect(self._on_home_activity)
        return self.home_page

    def _build_web_home(self):
        """Construct the WebHost Home, or return None to fall back to native."""
        try:
            from src.services.home_web import HomeWebController
            from src.ui.web.home_bridge import HomeBridge
            from src.ui.web.web_host import WebHost
        except Exception:            # WebEngine absent → native page
            return None
        try:
            ctrl = HomeWebController(
                self.db, current_mode=self._mode,
                # The NATIVE human gate for the one authority-bearing slot.
                confirm_fn=self._web_home_mode_confirm,
            )
            ctrl.mode_selected.connect(self.switch_mode)
            ctrl.quick_action.connect(self._on_home_quick_action)
            ctrl.activity_activated.connect(self._on_home_activity)
            bridge = HomeBridge(
                data_signal=ctrl.home_data,
                refresh_fn=ctrl.request_refresh,
                quick_action_fn=ctrl.js_quick_action,
                activity_fn=ctrl.js_activity_activated,
                mode_switch_fn=ctrl.js_request_mode_switch,
            )
            host = WebHost(bridge=bridge, channel_name="homeBridge",
                           route="/home", log_name="alma.home.web")
        except Exception as exc:
            import logging
            logging.getLogger("alma.home.web").warning(
                "web Home construction failed (%s) — using the native page", exc)
            return None
        # Held so neither is garbage-collected; the bridge is additionally
        # parented by WebHost (QWebChannel does not take ownership).
        self.home_page = ctrl
        self._home_web = (ctrl, bridge, host)
        self._arm_home_watchdog(host)
        return host

    def _web_home_mode_confirm(self, mode: str) -> bool:
        """The human gate for a web-Home mode switch: a NATIVE QMessageBox no
        page script can reach or click. Defaults to No."""
        from PySide6.QtWidgets import QMessageBox
        label = "Enablement" if mode == app_modes.MODE_ENABLEMENT else "Product"
        return QMessageBox.question(
            self, "Switch mode",
            f"Switch to {label} mode?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        ) == QMessageBox.Yes

    def _arm_home_watchdog(self, host, timeout_ms: int = 8000):
        """Fall back to the native Home if the web one never renders.

        Two triggers: an outright load failure, and a page that loads but never
        sets ``window.__almaHomeMounted`` (the macOS blank-render class — the
        renderer dies with the load already reported ok).
        """
        from PySide6.QtCore import QTimer

        def on_load(ok: bool):
            if not ok:
                self._fallback_to_native_home("load failed")

        try:
            host.view.loadFinished.connect(on_load)
        except Exception:
            pass

        def check_mounted():
            if self._home_web is None:
                return
            try:
                host.view.page().runJavaScript(
                    "!!window.__almaHomeMounted",
                    lambda mounted: (None if mounted
                                     else self._fallback_to_native_home(
                                         "page never mounted")))
            except Exception:
                self._fallback_to_native_home("mount probe failed")

        QTimer.singleShot(timeout_ms, check_mounted)

    def _fallback_to_native_home(self, reason: str):
        """Swap a dead web Home for the native widget, in place."""
        if self._home_web is None:
            return
        import logging
        logging.getLogger("alma.home.web").error(
            "web Home unusable (%s) — falling back to the native page", reason)
        _ctrl, _bridge, host = self._home_web
        self._home_web = None
        was_current = self.content_stack.currentWidget() is host
        native = self._build_native_home()
        self.content_stack.addWidget(native)
        self._page_widgets["home"] = native
        self._factory_widgets["_create_home_page"] = native
        if was_current:
            self.content_stack.setCurrentWidget(native)
        try:
            self.content_stack.removeWidget(host)
            host.deleteLater()
        except Exception:
            pass

    def _on_home_quick_action(self, action: str):
        """Route a Home quick-action chip to its page (+ optional call)."""
        routes = {
            "search": ("conversations",
                       lambda: self.conversations_page.run_search()),
            "reports": ("reports", None),
            "workbench": ("en_workbench", None),
            "calendar": ("en_calendar", None),
            "renn": ("en_workbench",
                     lambda: self.guru_page._open_chat()),
        }
        page_id, follow_up = routes.get(action, (None, None))
        if page_id is None:
            return
        self._set_active_page(page_id)
        if follow_up is not None:
            try:
                follow_up()
            except Exception:
                pass

    def _on_home_activity(self, kind: str):
        """Route a clicked recent-activity row to its page (mode-aware)."""
        if self._mode == app_modes.MODE_ENABLEMENT:
            dest = {"Chat": "en_workbench", "Report": "en_analytics",
                    "Task": "en_tasks"}.get(kind)
        else:
            dest = {"Chat": "gemini_chats", "Report": "reports"}.get(kind)
        if not dest:
            return
        self._set_active_page(dest)
        if kind == "Chat" and self._mode == app_modes.MODE_ENABLEMENT:
            try:
                self.guru_page._open_chat()
            except Exception:
                pass

    def _create_conversations_page(self):
        from src.ui.pages.conversation_search import ConversationSearchPage
        self.conversations_page = ConversationSearchPage(self.db)
        self.conversations_page.data_loaded.connect(self._on_data_loaded)
        return self.conversations_page

    def _create_agent_page(self):
        """The standalone Agent chat — an embedded web UI (QWebEngineView) bridged
        to a live ChatEngine over QWebChannel. Degrades to a placeholder if
        QtWebEngine is absent (shipped build before the installer adds it)."""
        try:
            from src.data.settings_manager import get_section
            from src.services.agent_chat import AgentChatController
            from src.ui.web.agent_page import AgentPage
            en_cfg = get_section("enablement", {}) or {}
            self._agent_controller = AgentChatController(
                self.db, demo=en_cfg.get("demo_mode", True))
            if self._agent_controller.engine is None:
                raise RuntimeError("chat engine unavailable")
            self.agent_page = AgentPage(self._agent_controller.engine,
                                        send_fn=self._agent_controller.send,
                                        tool_poll=self._agent_controller.recent_tool_calls,
                                        session_api=self._agent_controller,
                                        job_poll=self._agent_controller.recent_jobs,
                                        draft_api=self._agent_controller,
                                        voice=self._agent_controller.voice,
                                        action_poll=self._agent_controller.poll_action_requests,
                                        connect_fn=self._agent_controller.start_google_connect,
                                        google_state_signal=self._agent_controller.googleAuthState,
                                        list_fn=self._agent_controller.list_drive_folders,
                                        resolve_fn=self._agent_controller.resolve_drive_folder,
                                        drive_folders_signal=self._agent_controller.driveFoldersListed,
                                        action_resolved_signal=self._agent_controller.actionResolved,
                                        asana_list_fn=self._agent_controller.list_asana_projects_for_picker,
                                        asana_resolve_fn=self._agent_controller.resolve_asana_board,
                                        asana_projects_signal=self._agent_controller.asanaProjectsListed,
                                        guru_list_fn=self._agent_controller.list_guru_targets,
                                        guru_resolve_fn=self._agent_controller.resolve_guru_target,
                                        guru_targets_signal=self._agent_controller.guruTargetsListed,
                                        confirm_fn=self._agent_controller.execute_write,
                                        cancel_fn=self._agent_controller.cancel_write)
            return self.agent_page
        except Exception as exc:  # noqa: BLE001 — never break the mode
            import logging
            logging.getLogger("alma.main").warning("Agent page unavailable: %s", exc)
            return self._native_agent_fallback()

    def _native_agent_fallback(self):
        """Fallback when the embedded Agent web view can't be built.

        When only QtWebEngine is missing, the chat ENGINE is still fine, so we
        hand back a real native chat panel wired to the same controller — a
        usable page, matching how Calendar/Workbench fall back to native tabs —
        rather than a dead label. Only when the engine itself is unavailable do
        we show an explanatory message."""
        controller = getattr(self, "_agent_controller", None)
        engine = getattr(controller, "engine", None) if controller else None
        if controller is None or engine is None:
            ph = QLabel("The Agent chat is unavailable in this build. The rest "
                        "of the app works normally; reinstalling restores it.")
            ph.setWordWrap(True)
            ph.setAlignment(Qt.AlignCenter)
            return ph

        from src.ui.pages.enablement.chat_panel import ChatPanel
        panel = ChatPanel()
        panel.set_chat([("a", "Hi, I'm Renn. The rich chat view isn't available "
                              "in this build, so this is a plain fallback — it "
                              "works, just without streaming or the side "
                              "panels.")])

        def _on_submit(text: str):
            panel.add_message("u", text)
            try:
                controller.send(text)
            except Exception as exc:  # noqa: BLE001
                panel.add_message("a", f"Something went wrong: {exc}")

        panel.chat_submitted.connect(_on_submit)
        try:
            engine.response_ready.connect(lambda t: panel.add_message("a", t))
            engine.error_occurred.connect(
                lambda e: panel.add_message("a", f"Error: {e}"))
        except Exception:  # noqa: BLE001 — engine wiring is best-effort
            pass
        self.agent_page = panel
        return panel

    def _create_dashboard_page(self):
        from src.ui.pages.trc_analytics import TRCAnalyticsPage
        self.dashboard_page = TRCAnalyticsPage(self.db)
        # NLP Scanner signals wired through TRC Analytics (NLP Scanner absorbed)
        self.dashboard_page.deep_dive_requested.connect(self._on_nlp_deep_dive)
        self.dashboard_page.view_tickets_requested.connect(self._on_nlp_view_tickets)
        self.dashboard_page.scan_active_changed.connect(self._on_scan_active_changed)
        return self.dashboard_page

    def _create_trending_page(self):
        from src.ui.pages.trending_topics import TrendingTopicsPage
        self.trending_page = TrendingTopicsPage(self.db)
        return self.trending_page

    def _create_incidents_page(self):
        from src.ui.pages.incidents_page import IncidentsPage
        self.incidents_page = IncidentsPage(self.db)
        self.incidents_page.scan_complete.connect(self._update_incident_badge)
        return self.incidents_page

    def _create_reports_page(self):
        from src.ui.pages.ai_reports import AIReportsPage
        self.reports_page = AIReportsPage(self.db)
        return self.reports_page

    def _create_ab_compare_page(self):
        from src.ui.pages.ab_compare import ABComparePage
        self.ab_compare_page = ABComparePage(self.db)
        return self.ab_compare_page

    def _create_smart_reporting_page(self):
        from src.ui.pages.smart_reporting import SmartReportingPage
        self.smart_reporting_page = SmartReportingPage(self.db)
        return self.smart_reporting_page

    def _create_settings_page(self):
        from src.ui.pages.settings_page import SettingsPage
        self.settings_page = SettingsPage()
        self.settings_page.set_db_manager(self.db)
        self.settings_page.datasets_changed.connect(self._on_datasets_changed)
        self.settings_page.test_data_changed.connect(self._on_test_data_toggled)
        self.settings_page.debug_mode_changed.connect(self._on_debug_mode_toggled)
        self.settings_page.api_toggle.toggled.connect(self._on_api_toggled)
        self.settings_page.settings_changed.connect(self._on_settings_changed)
        return self.settings_page

    def _create_source_monitor_page(self):
        from src.ui.pages.source_monitor_page import SourceMonitorPage
        self.source_monitor_page = SourceMonitorPage(self.db)
        return self.source_monitor_page

    def _create_legacy_guru_page(self):
        self.guru_page = self._make_guru_page()
        return self.guru_page

    def _create_enablement_page(self):
        from src.ui.pages.enablement import EnablementPage
        en_cfg = get_section("enablement", {}) or {}
        page = EnablementPage(self.db, demo=en_cfg.get("demo_mode", True))
        try:
            # Sidebar entries drive tab selection in enablement mode
            page.set_tab_bar_visible(False)
        except Exception:
            pass
        self.guru_page = page
        return page

    def _create_gemini_chats_page(self):
        from src.ui.pages.gemini_chats_page import GeminiChatsPage
        self.gemini_chats_page = GeminiChatsPage(self.db)
        return self.gemini_chats_page

    def _create_data_warehouse_page(self):
        from src.ui.pages.data_warehouse_page import DataWarehousePage
        self.data_warehouse_page = DataWarehousePage(self.db)
        return self.data_warehouse_page

    # ── Mode-gated service start/stop wrappers ──────────────────────

    def _start_schedule_manager(self):
        """Schedule Manager (persistent DB-backed scheduling for Smart
        Reporting). Product mode only."""
        if getattr(self, "_schedule_manager", None) is None:
            try:
                from src.data.schedule_manager import ScheduleManager
                self._schedule_manager = ScheduleManager(self.db, parent=self)
                self.smart_reporting_page.set_schedule_manager(self._schedule_manager)
            except Exception:
                self._schedule_manager = None
                return
        if self._schedule_manager is not None and not self._schedule_manager.is_running():
            self._schedule_manager.start()

    def _stop_schedule_manager(self):
        if getattr(self, "_schedule_manager", None) is not None:
            try:
                self._schedule_manager.stop()
            except Exception:
                pass

    def _stop_zendesk_monitor(self):
        if self._zendesk_monitor is not None:
            try:
                self._zendesk_monitor.stop()
            except Exception:
                pass
            self._zendesk_monitor = None

    def _stop_enablement_monitor(self):
        if getattr(self, "_enablement_monitor", None) is not None:
            try:
                self._enablement_monitor.stop()
            except Exception:
                pass
            self._enablement_monitor = None

    # ═══════════════════════════════════════════
    #  RESTORE PERSISTED SETTINGS
    # ═══════════════════════════════════════════

    def _restore_settings(self):
        """Push persisted settings state to conversations page on startup."""
        api_enabled = self.settings_page.is_api_enabled()
        self.conversations_page.update_api_state(api_enabled)
        self.conversations_page.update_pat(self.settings_page.get_pat())
        self.conversations_page.update_test_mode(self.settings_page.is_test_data_enabled())
        debug_on = self.settings_page.is_debug_mode_enabled()
        self.conversations_page.update_debug_mode(debug_on)
        if api_enabled:
            self.conversations_page.update_datasets(self.settings_page.get_datasets())

    # ═══════════════════════════════════════════
    #  DATA LOADING
    # ═══════════════════════════════════════════

    def _load_data(self):
        """Load demo data on startup if test mode is enabled."""
        count = self.db.get_ticket_count()
        if self.settings_page.is_test_data_enabled():
            if count == 0:
                self.status_label.setText("Loading demo data...")
                QApplication.processEvents()
                self.db.load_demo_data()
                count = self.db.get_ticket_count()
                self.status_label.setText("Demo data loaded")
            self.conversations_page.set_data_source_label("Testing data")
        else:
            label = "No data loaded" if count == 0 else f"{count:,} conversations"
            self.conversations_page.set_data_source_label(label)

        self.ticket_count_label.setText(f"{count} tickets in database")

        # Populate TRC filters on all pages
        self.conversations_page.populate_trc_filter()
        self.dashboard_page.populate_trc_filter()
        self.trending_page.populate_trc_filter()
        self.incidents_page.populate_trc_filter()
        self.reports_page.populate_trc_filter()

        # Rebuild count tables for incident monitoring
        self.db.populate_daily_counts()
        self.db.populate_hourly_counts()

        # Sync date pickers on all pages to match data range
        self.dashboard_page.sync_date_to_data()
        self.trending_page.sync_date_to_data()
        self.incidents_page.sync_date_to_data()
        self.reports_page.sync_date_to_data()

        # Auto-search on launch
        QTimer.singleShot(100, self.conversations_page.run_search)

        # Initialize incident badge
        self._update_incident_badge(0)

    # ═══════════════════════════════════════════
    #  SOURCE WAREHOUSE + WATCHLIST (Phase 3.5)
    # ═══════════════════════════════════════════

    def _setup_warehouse_and_watchlist(self):
        """Initialize SourceWarehouse and WatchlistEngine.

        These are source-agnostic — they work with any source monitor.
        Created once on app start; passed to monitors via constructor.
        """
        if not hasattr(self, "source_monitor_page"):
            return  # product pages not mounted (enablement mode)
        if self._source_warehouse is not None:
            return  # already initialized (mode switch round trip)
        try:
            from src.data.source_warehouse import SourceWarehouse
            from src.data.watchlist_engine import WatchlistEngine

            self._source_warehouse = SourceWarehouse(self.db)
            self._watchlist_engine = WatchlistEngine(
                self.db, warehouse=self._source_warehouse
            )

            # Wire watchlist to Source Monitor Page
            self.source_monitor_page.set_watchlist(self._watchlist_engine)

            # Run maintenance on start (prune old rollups, expire old alerts)
            self._source_warehouse.rollup_maintenance()

        except Exception as exc:
            import logging
            logging.getLogger("alma.main").warning(
                "Warehouse/watchlist setup skipped: %s", exc
            )

    # ═══════════════════════════════════════════
    #  GURU INTEGRATION (Phase 4)
    # ═══════════════════════════════════════════

    def _make_guru_page(self):
        """Legacy Guru page routing (product mode only).

        The Enablement Workbench now lives in enablement mode (see
        `_create_enablement_page`); the product sidebar carries no Guru
        entry unless ``guru.experimental_ui_enabled`` is set, in which
        case the legacy interactive ``GuruPage`` mounts. The WIP
        placeholder remains the safe fallback if that construction
        fails.
        """
        try:
            from src.data.settings_manager import get_section
            guru_cfg = get_section("guru", {}) or {}
            if guru_cfg.get("experimental_ui_enabled", False):
                from src.ui.pages.guru_page import GuruPage
                return GuruPage(self.db)
        except Exception:
            # If settings load fails, default to the safer WIP page.
            pass
        from src.ui.pages.guru_wip_page import GuruWipPage
        return GuruWipPage(self.db)

    def _wire_enablement_monitor(self):
        """Build + start the Enablement source monitor (live mode only).

        Demo mode never polls real Asana/Drive. The monitor's ``changed`` signal
        refreshes the four pages; its background timers poll each configured source.
        """
        if getattr(self, "_enablement_monitor", None):
            return
        try:
            from src.data.settings_manager import get_section
            en = get_section("enablement", {}) or {}
            if en.get("demo_mode", True):
                return
            from src.data.enablement_monitor import EnablementMonitor
            self._enablement_monitor = EnablementMonitor(self.db)
            self.guru_page.set_monitor(self._enablement_monitor)
            asana_cfg = en.get("asana") or {}
            self._enablement_monitor.start(
                int(en.get("poll_interval_seconds", 300)),
                asana_interval_seconds=int(asana_cfg.get("poll_interval_seconds", 60)),
            )
        except Exception as exc:  # noqa: BLE001
            import logging
            logging.getLogger("alma.main").debug("enablement monitor skipped: %s", exc)

    def _setup_guru(self):
        """Initialize Guru client and pipelines if credentials are configured.

        When the WIP placeholder is mounted (default), pipelines are
        skipped — the placeholder accepts the setter calls as no-ops
        so the rest of the wiring stays simple.
        """
        # The WIP placeholder defines the same setter API as GuruPage
        # but ignores everything. Skip the pipeline construction entirely
        # to avoid running unnecessary DB queries at startup.
        if not hasattr(self, "guru_page"):
            return  # no guru surface mounted in this mode
        if type(self.guru_page).__name__ == "EnablementPage":
            # Enablement Workbench publishes via guru_content_drafts; it needs only
            # the Guru client, not the friction/content/effectiveness pipelines.
            try:
                from src.data.guru_client import GuruClient
                email, token = GuruClient.load_credentials()
                if email and token:
                    self.guru_page.set_guru_client(GuruClient(email, token))
            except Exception:
                pass
            self._wire_enablement_monitor()
            try:
                self.guru_page.check_connections()
            except Exception:
                pass
            if not getattr(self, "_guru_signal_wired", False):
                try:
                    self.guru_page.connection_changed.connect(self._setup_guru)
                    self._guru_signal_wired = True
                except Exception:
                    pass
            return
        if type(self.guru_page).__name__ == "GuruWipPage":
            return
        try:
            from src.data.guru_client import GuruClient
            from src.data.guru_friction_pipeline import GuruFrictionPipeline
            from src.data.guru_content_pipeline import GuruContentPipeline
            from src.data.guru_effectiveness import GuruEffectivenessTracker

            email, token = GuruClient.load_credentials()
            if not email or not token:
                return  # Not configured yet

            client = GuruClient(email, token)
            friction = GuruFrictionPipeline(self.db, client)
            content = GuruContentPipeline(self.db, client)
            effectiveness = GuruEffectivenessTracker(self.db)

            self.guru_page.set_guru_client(client)
            self.guru_page.set_friction_pipeline(friction)
            self.guru_page.set_content_pipeline(content)
            self.guru_page.set_effectiveness_tracker(effectiveness)

            # Wire connection_changed to re-setup
            if not getattr(self, "_guru_signal_wired", False):
                self.guru_page.connection_changed.connect(self._setup_guru)
                self._guru_signal_wired = True

            # Run periodic effectiveness measurement on start
            try:
                effectiveness.measure_effectiveness()
            except Exception:
                pass

        except Exception as exc:
            import logging
            logging.getLogger("alma.main").warning(
                "Guru setup skipped: %s", exc
            )

    # ═══════════════════════════════════════════
    #  ZENDESK MONITOR (Phase 3)
    # ═══════════════════════════════════════════

    def _setup_zendesk_monitor(self):
        """Create and start the Zendesk monitor if credentials are configured."""
        if not hasattr(self, "source_monitor_page"):
            return  # product pages not mounted (enablement mode)
        # Wire connection_changed ONCE (idempotent via _zd_signal_wired flag)
        if not getattr(self, "_zd_signal_wired", False):
            self.source_monitor_page.connection_changed.connect(
                self._on_zendesk_connection_changed
            )
            self._zd_signal_wired = True

        try:
            from src.data.zendesk_client import ZendeskClient
            from src.data.zendesk_monitor import ZendeskMonitor

            subdomain, email, api_key, _view_id = ZendeskClient.load_credentials()
            if not subdomain or not api_key:
                # No Zendesk configured — leave monitor as None
                return

            self._zendesk_monitor = ZendeskMonitor(
                self.db,
                warehouse=self._source_warehouse,
                watchlist=self._watchlist_engine,
                parent=self
            )
            self.source_monitor_page.set_monitor(self._zendesk_monitor)

            # Start polling
            self._zendesk_monitor.start(interval_seconds=120)

        except Exception as exc:
            import logging
            logging.getLogger("alma.main").warning(
                "Zendesk monitor setup skipped: %s", exc
            )

    def _on_zendesk_connection_changed(self):
        """Restart Zendesk monitor when connection settings change."""
        # Stop existing monitor
        if self._zendesk_monitor:
            self._zendesk_monitor.stop()
            self._zendesk_monitor = None

        # Re-setup with new credentials
        self._setup_zendesk_monitor()

    # ═══════════════════════════════════════════
    #  SETTINGS WIRING
    # ═══════════════════════════════════════════

    def _on_datasets_changed(self, datasets):
        """Settings: dataset list changed."""
        self.conversations_page.update_datasets(datasets)

    def _on_api_toggled(self, enabled):
        """Settings: API toggle changed."""
        self.conversations_page.update_api_state(enabled)
        # Push current PAT to conversations page
        self.conversations_page.update_pat(self.settings_page.get_pat())

    def _on_test_data_toggled(self, enabled):
        """Settings: test data toggle changed."""
        self.conversations_page.update_test_mode(enabled)
        if enabled:
            # Load demo data
            count = self.db.get_ticket_count()
            if count == 0:
                self.status_label.setText("Loading demo data...")
                QApplication.processEvents()
                self.db.load_demo_data()
                self.status_label.setText("Demo data loaded")

            count = self.db.get_ticket_count()
            self.ticket_count_label.setText(f"{count} tickets in database")
            self.conversations_page.set_data_source_label("Testing data")
            self.conversations_page.populate_trc_filter()
            self.dashboard_page.populate_trc_filter()
            self.trending_page.populate_trc_filter()
            self.incidents_page.populate_trc_filter()
            self.reports_page.populate_trc_filter()
            self.conversations_page.run_search()
            # Rebuild count tables for incident monitoring
            self.db.populate_daily_counts()
            self.db.populate_hourly_counts()
        else:
            # Clear all data — live mode
            self._clear_all_data()
            self.ticket_count_label.setText("0 tickets in database")
            self.conversations_page.set_data_source_label("No data loaded")
            self.conversations_page.populate_trc_filter()
            self.dashboard_page.populate_trc_filter()
            self.trending_page.populate_trc_filter()
            self.incidents_page.populate_trc_filter()
            self.conversations_page.clear_filters()
            self.status_label.setText("Test data cleared — import or pull live data")

    def _on_data_loaded(self, stats):
        """Conversations page: CSV import or API ingestion completed."""
        count = self.db.get_ticket_count()
        self.ticket_count_label.setText(f"{count} tickets in database")
        source = "CSV" if "total_csv_rows" in stats else "Lightdash"
        self.status_label.setText(f"Imported {count:,} conversations from {source}")

        # Refresh TRC filters on analytics pages
        self.dashboard_page.populate_trc_filter()
        self.trending_page.populate_trc_filter()
        self.incidents_page.populate_trc_filter()
        self.reports_page.populate_trc_filter()

        # Sync date pickers on ALL pages to match imported data range
        self.dashboard_page.sync_date_to_data()
        self.trending_page.sync_date_to_data()
        self.incidents_page.sync_date_to_data()
        self.reports_page.sync_date_to_data()
        self.conversations_page._sync_date_filters_to_data()

        # Rebuild count tables for incident monitoring
        self.db.populate_daily_counts()
        self.db.populate_hourly_counts()

        # Queue analysis jobs sequentially (gated by behavior settings)
        self._queue_auto_analysis()

    def _on_debug_mode_toggled(self, enabled):
        """Settings: debug canary toggle changed."""
        self.conversations_page.update_debug_mode(enabled)

    def _on_scan_active_changed(self, active: bool):
        """Block/unblock Gemini-dependent features during NLP scans."""
        self.reports_page.set_scan_blocking(active)
        self.trending_page.set_scan_blocking(active)

    def _on_settings_changed(self, changes: dict):
        """Settings: generic change notification (e.g. Gemini config updated)."""
        if changes.get("gemini_updated"):
            self.reports_page.refresh_gemini_status()
            self.ab_compare_page.refresh_gemini_status()
            self.smart_reporting_page.refresh_gemini_status()
        if changes.get("interventions_updated"):
            # Refresh intervention markers on incident chart
            try:
                interventions = self.db.get_interventions()
                self.incidents_page.control_chart.set_interventions(interventions)
            except Exception:
                pass
        if changes.get("layman_mode_updated"):
            self._update_layman_indicator()
        if changes.get("behavior_updated"):
            self._apply_section_defaults()

    def _update_layman_indicator(self):
        """Update the status bar layman mode indicator."""
        try:
            enabled = self.settings_page.is_layman_mode_enabled()
            if enabled:
                self.layman_mode_indicator.setText("\U0001F4DD Simplified mode")
            else:
                self.layman_mode_indicator.setText("")
        except Exception:
            pass

    # ═══════════════════════════════════════════
    #  JOB QUEUE WIRING
    # ═══════════════════════════════════════════

    def _wire_job_queue(self):
        """Connect JobQueue signals to JobOverlay and status bar."""
        q = self._job_queue
        o = self._job_overlay

        q.job_started.connect(lambda jid, desc: o.update_status(desc))
        q.job_started.connect(lambda jid, desc: o.show_overlay())
        q.job_progress.connect(lambda jid, msg, pct: o.update_status(msg))
        q.queue_changed.connect(lambda jobs: o.update_job_list(jobs))
        q.queue_empty.connect(lambda: o.hide_overlay())

        # Deferred import summary (after all jobs complete, so it doesn't block)
        q.queue_empty.connect(self._show_deferred_import_summary)

        # Status bar (persistent state) + Toast notifications (Build 10.0: T17)
        q.job_started.connect(lambda jid, desc: self.status_label.setText(desc))
        q.job_finished.connect(lambda jid, name: self.status_label.setText(f"✓ {name} complete"))
        q.job_finished.connect(
            lambda jid, name: self._toasts.show_toast(f"{name} complete", toast_type="success")
        )
        q.job_failed.connect(lambda jid, name, err: self.status_label.setText(f"✗ {name} failed"))
        q.job_failed.connect(
            lambda jid, name, err: self._toasts.show_toast(f"{name} failed", toast_type="error")
        )
        q.queue_empty.connect(lambda: self.status_label.setText("Ready"))

    def _show_deferred_import_summary(self):
        """Show CSV import summary after all jobs have finished."""
        try:
            self.conversations_page.show_deferred_import_summary()
        except Exception:
            pass

    def _show_qt_errors(self, event=None):
        """Show Qt error details in a dialog (clicked from status bar label)."""
        try:
            guard = self.qt_error_label.property("_guard")
            if guard is None:
                # Find guard from app
                app = QApplication.instance()
                for child in app.children():
                    if hasattr(child, 'get_error_summary'):
                        guard = child
                        break
            if guard and guard.get_error_count() > 0:
                from PySide6.QtWidgets import QMessageBox
                msg = QMessageBox(self)
                msg.setWindowTitle("Qt Error Report")
                msg.setIcon(QMessageBox.Warning)
                msg.setText(f"{guard.get_error_count()} Qt errors detected")
                msg.setDetailedText(guard.get_error_summary())
                msg.exec()
        except Exception:
            pass

    def _queue_auto_analysis(self):
        """Queue sequential analysis jobs gated by behavior settings."""
        aa = self._get_auto_analysis_config()
        jobs = []

        # TRC Analytics
        trc = aa.get("trc_analytics", {})
        if trc.get("enabled", True):
            jobs.append(self._make_analytics_job())

        # Incidents
        inc = aa.get("incidents", {})
        if inc.get("enabled", True):
            if (inc.get("trc_status_grid", True) or inc.get("control_chart", True)
                    or inc.get("open_incidents", True)):
                jobs.append(self._make_incident_scan_job())
            if inc.get("theta_anomaly_scan", True):
                jobs.append(self._make_theta_scan_job())

        # Trending
        trn = aa.get("trending", {})
        if trn.get("enabled", True):
            jobs.append(self._make_trending_job())

        if jobs:
            self._job_queue.submit_batch(jobs)

    # ── Job Factories ────────────────────────────────────────────────

    def _make_analytics_job(self) -> JobDescriptor:
        """Create a JobDescriptor for TRC Analytics refresh."""
        page = self.dashboard_page
        queue = self._job_queue

        def create_worker():
            page.sync_date_to_data()
            page.populate_trc_filter()

            date_start = page.date_from.date().toString("yyyy-MM-dd")
            date_end = page.date_to.date().toString("yyyy-MM-dd") + "T23:59:59"
            trc_filter = page.trc_combo.currentData() or None
            status_filter = page.status_combo.currentText()
            if status_filter == "All":
                status_filter = None

            from src.ui.pages.trc_analytics import AnalyticsWorker
            worker = AnalyticsWorker(
                self.db.db_path, date_start, date_end, trc_filter, status_filter
            )
            page._worker = worker

            worker.finished.connect(page._on_results)
            worker.error.connect(page._on_error)
            return worker

        return JobDescriptor(
            job_id="trc_analytics_refresh",
            name="TRC Analytics",
            description="Computing TRC analytics",
            create_worker=create_worker,
        )

    def _make_incident_scan_job(self) -> JobDescriptor:
        """Create a JobDescriptor for incident anomaly scan."""
        page = self.incidents_page
        queue = self._job_queue

        def create_worker():
            import time
            page.sync_date_to_data()

            try:
                self.db.populate_daily_counts()
                self.db.populate_hourly_counts()
            except Exception:
                pass

            page._scan_start_time = time.time()
            date_from = page.date_from.date().toString("yyyy-MM-dd")
            date_to = page.date_to.date().toString("yyyy-MM-dd")

            from src.ui.pages.incidents_page import IncidentWorker
            worker = IncidentWorker(self.db.db_path, date_to, date_from)
            page._worker = worker

            worker.finished.connect(page._on_scan_results)
            worker.error.connect(page._on_scan_error)
            worker.progress.connect(
                lambda msg, pct: queue.job_progress.emit("incident_scan", msg, pct)
            )
            return worker

        return JobDescriptor(
            job_id="incident_scan",
            name="Incident Scan",
            description="Running incident anomaly scan",
            create_worker=create_worker,
        )

    def _make_theta_scan_job(self) -> JobDescriptor:
        """Create a JobDescriptor for theta (EWMA) anomaly scan."""
        page = self.incidents_page
        queue = self._job_queue

        def create_worker():
            from src.ui.pages.incidents_page import ThetaWorker
            worker = ThetaWorker(self.db.db_path)
            page._theta_worker = worker

            worker.finished.connect(page._on_theta_results)
            worker.error.connect(lambda err: None)  # silent on error for auto-scan
            worker.progress.connect(
                lambda msg: queue.job_progress.emit("theta_scan", msg, -1)
            )
            return worker

        return JobDescriptor(
            job_id="theta_scan",
            name="Theta Anomaly Scan",
            description="Running EWMA anomaly detection",
            create_worker=create_worker,
        )

    def _make_trending_job(self) -> JobDescriptor:
        """Create a JobDescriptor for trending topics analysis."""
        page = self.trending_page
        queue = self._job_queue

        def create_worker():
            import time
            page.sync_date_to_data()
            page.populate_trc_filter()

            page._scan_start_time = time.time()
            date_start = page.date_from.date().toString("yyyy-MM-dd")
            date_end = page.date_to.date().toString("yyyy-MM-dd") + "T23:59:59"
            trc_filter = page.trc_combo.currentData() or None
            window_size = page.window_combo.currentText()
            topic_method = page.method_combo.currentData()

            from src.ui.pages.trending_topics import TrendingWorker
            worker = TrendingWorker(
                self.db.db_path, date_start, date_end, trc_filter,
                window_size, topic_method,
            )
            page._worker = worker

            worker.finished.connect(page._on_results)
            worker.error.connect(page._on_error)
            worker.progress.connect(
                lambda msg: queue.job_progress.emit("trending_analysis", msg, -1)
            )
            return worker

        return JobDescriptor(
            job_id="trending_analysis",
            name="Trending Topics",
            description="Analyzing trending topics",
            create_worker=create_worker,
        )

    def _make_voc_report_job(self, date_start, date_end, trc_filter=None):
        """Create a JobDescriptor for VOC report (via JobQueue)."""
        from src.data.job_queue import JobDescriptor, CallableWorker

        # Read model name for overlay display
        model = "gemini-2.5-flash"
        try:
            gemini_cfg = get_section("gemini")
            model = gemini_cfg.get("model", model)
        except Exception:
            pass

        def create_worker():
            from src.ui.pages.ai_reports import VOCReportWorker
            gc = self.reports_page._get_gemini_client()
            worker = VOCReportWorker(
                self.db.db_path, date_start, date_end, trc_filter, gc,
            )
            worker.finished.connect(self.ai_reports_page._on_voc_finished)
            worker.error.connect(self.ai_reports_page._on_voc_error)
            worker.progress.connect(self.ai_reports_page._on_voc_progress)
            return worker

        trc_str = f" ({trc_filter})" if trc_filter else ""
        return JobDescriptor(
            job_id="voc_report",
            name="VOC Report",
            description=f"Generating VOC root cause analysis with {model}{trc_str}",
            create_worker=create_worker,
        )

    # ═══════════════════════════════════════════
    #  BEHAVIOR HELPERS
    # ═══════════════════════════════════════════

    def _get_auto_analysis_config(self):
        """Read behavior.auto_analysis from config/settings.yaml."""
        try:
            behavior = get_section("behavior")
            return behavior.get("auto_analysis", {})
        except Exception:
            pass
        return {}

    def _is_calendar_sync_enabled(self):
        """Check if analysis page calendar sync is enabled."""
        try:
            behavior = get_section("behavior")
            return behavior.get("calendar_sync", {}).get("analysis_pages", False)
        except Exception:
            pass
        return False

    def _is_ai_reports_sync_enabled(self):
        """Check if AI Reports calendar sync is enabled."""
        try:
            behavior = get_section("behavior")
            cs = behavior.get("calendar_sync", {})
            return cs.get("analysis_pages", False) and cs.get("ai_reports", False)
        except Exception:
            pass
        return False

    # ═══════════════════════════════════════════
    #  CALENDAR SYNC
    # ═══════════════════════════════════════════

    def _setup_calendar_sync(self):
        """Connect date_changed signals from all analysis pages for cross-page sync."""
        self._syncing_dates = False  # Guard to prevent re-entry
        self._reanalysis_timer = QTimer(self)
        self._reanalysis_timer.setSingleShot(True)
        self._reanalysis_timer.setInterval(500)
        self._reanalysis_pages_to_refresh = set()

        self._reanalysis_timer.timeout.connect(self._do_reanalysis)

        # TRC Analytics date pickers
        self.dashboard_page.date_from.date_changed.connect(
            lambda d: self._on_analysis_date_changed("trc_analytics", "from", d)
        )
        self.dashboard_page.date_to.date_changed.connect(
            lambda d: self._on_analysis_date_changed("trc_analytics", "to", d)
        )

        # Trending Topics date pickers
        self.trending_page.date_from.date_changed.connect(
            lambda d: self._on_analysis_date_changed("trending", "from", d)
        )
        self.trending_page.date_to.date_changed.connect(
            lambda d: self._on_analysis_date_changed("trending", "to", d)
        )

        # Incidents date pickers
        self.incidents_page.date_from.date_changed.connect(
            lambda d: self._on_analysis_date_changed("incidents", "from", d)
        )
        self.incidents_page.date_to.date_changed.connect(
            lambda d: self._on_analysis_date_changed("incidents", "to", d)
        )

        # AI Reports date pickers
        self.reports_page._date_from.date_changed.connect(
            lambda d: self._on_reports_date_changed("from", d)
        )
        self.reports_page._date_to.date_changed.connect(
            lambda d: self._on_reports_date_changed("to", d)
        )

    def _on_analysis_date_changed(self, source: str, which: str, date):
        """An analysis page date picker was changed by user click."""
        if self._syncing_dates or not self._is_calendar_sync_enabled():
            return

        self._syncing_dates = True
        try:
            # Map of page_key → (page_object, date_from_attr, date_to_attr)
            pages = {
                "trc_analytics": self.dashboard_page,
                "trending": self.trending_page,
                "incidents": self.incidents_page,
            }

            # Propagate date to other analysis pages
            for key, page in pages.items():
                if key == source:
                    continue
                picker = page.date_from if which == "from" else page.date_to
                picker.setDate(date)  # setDate() does NOT emit date_changed

            # Also sync to AI Reports if enabled
            if self._is_ai_reports_sync_enabled():
                rp = self.reports_page
                picker = rp._date_from if which == "from" else rp._date_to
                picker.setDate(date)

            # Schedule re-analysis on synced pages
            self._schedule_reanalysis(exclude=source)
        finally:
            self._syncing_dates = False

    def _on_reports_date_changed(self, which: str, date):
        """AI Reports page date picker was changed by user click."""
        if self._syncing_dates or not self._is_ai_reports_sync_enabled():
            return

        self._syncing_dates = True
        try:
            # Propagate to all analysis pages
            for page in (self.dashboard_page, self.trending_page, self.incidents_page):
                picker = page.date_from if which == "from" else page.date_to
                picker.setDate(date)

            # Schedule re-analysis on all analysis pages
            self._schedule_reanalysis(exclude=None)
        finally:
            self._syncing_dates = False

    def _schedule_reanalysis(self, exclude=None):
        """Debounced re-run of analysis on synced pages.

        exclude: page_key to skip (the page that initiated the change)
        """
        pages_to_refresh = {"trc_analytics", "trending", "incidents"}
        if exclude:
            pages_to_refresh.discard(exclude)
        self._reanalysis_pages_to_refresh |= pages_to_refresh
        self._reanalysis_timer.start()  # restart the 500ms debounce

    def _do_reanalysis(self):
        """Execute deferred re-analysis on synced pages via job queue."""
        pages = self._reanalysis_pages_to_refresh.copy()
        self._reanalysis_pages_to_refresh.clear()

        # Cancel any stale pending analysis jobs
        for pid in ("trc_analytics_refresh", "incident_scan", "theta_scan", "trending_analysis"):
            self._job_queue.cancel_pending(pid)

        jobs = []
        if "trc_analytics" in pages:
            jobs.append(self._make_analytics_job())
        if "incidents" in pages:
            jobs.append(self._make_incident_scan_job())
            jobs.append(self._make_theta_scan_job())
        if "trending" in pages:
            jobs.append(self._make_trending_job())

        if jobs:
            self._job_queue.submit_batch(jobs)

    # ═══════════════════════════════════════════
    #  SECTION DEFAULTS
    # ═══════════════════════════════════════════

    def _apply_section_defaults(self):
        """Apply current section_defaults from settings to all CollapsibleSections."""
        from src.ui.widgets.collapsible_section import CollapsibleSection

        for page in (self.dashboard_page, self.trending_page, self.incidents_page):
            sections = page.findChildren(CollapsibleSection)
            for section in sections:
                key = section.section_key
                if key:
                    should_collapse = CollapsibleSection.get_default_collapsed(key)
                    section.set_collapsed(should_collapse)

    # ═══════════════════════════════════════════
    #  INCIDENT BADGE
    # ═══════════════════════════════════════════

    def _update_incident_badge(self, count_2theta: int = 0):
        """Update sidebar badge for Incidents with 2θ flag count."""
        for btn, pid in self._sidebar_buttons:
            if pid == "incidents":
                text = (f"Incidents ({count_2theta})"
                        if count_2theta > 0 else "Incidents")
                btn.setText(text)
                btn.setProperty("full_text", text)
                break

    # ═══════════════════════════════════════════
    #  NLP SCANNER NAVIGATION
    # ═══════════════════════════════════════════

    def _on_nlp_deep_dive(self, finding_id, finding_title):
        """Navigate to AI Reports and trigger drilldown for a finding."""
        self._set_active_page(self.PAGE_REPORTS)
        self.reports_page.load_nlp_finding(finding_id, finding_title)

    def _on_nlp_view_tickets(self, ticket_ids):
        """Navigate to Conversations page filtered to specific tickets."""
        self._set_active_page(self.PAGE_CONVERSATIONS)
        self.conversations_page.filter_by_ticket_ids(ticket_ids)

    # ═══════════════════════════════════════════
    #  DIALOGS
    # ═══════════════════════════════════════════

    def _show_help(self):
        """Enablement mode has a real Help Center; product mode keeps the
        dialog. Both buttons render in both modes, and in enablement they
        previously opened product-only content with placeholder URLs."""
        if self._mode == app_modes.MODE_ENABLEMENT:
            self._set_active_page("en_help")
            return
        dlg = HelpDialog(self)
        dlg.exec()

    def _show_feedback(self):
        """In enablement, route to the configured bug-report form (the same
        one the Help Center's 'Flag a bug' uses)."""
        if self._mode == app_modes.MODE_ENABLEMENT:
            page = self._page_widgets.get("en_help")
            opener = getattr(page, "_open_bug_form", None)
            if callable(opener):
                self._set_active_page("en_help")
                opener("")
                return
        dlg = HelpDialog(self)
        from PySide6.QtWidgets import QTabWidget
        for child in dlg.findChildren(QTabWidget):
            child.setCurrentIndex(2)  # Feedback tab
            break
        dlg.exec()

    def _stop_all_workers(self):
        """Stop any running background worker threads before closing."""
        # Cancel the unified job queue
        if hasattr(self, '_job_queue'):
            self._job_queue.cancel_all()

        # Also stop any workers not managed by the queue (HypothesisWorker)
        workers = []
        if hasattr(self, 'trending_page'):
            if getattr(self.trending_page, '_hyp_worker', None):
                workers.append(self.trending_page._hyp_worker)

        for w in workers:
            if w and w.isRunning():
                w.quit()
                w.wait(2000)

        # Build 7.0: Shutdown bridge-backed report client
        if hasattr(self, 'reports_page'):
            try:
                self.reports_page.cleanup()
            except Exception:
                pass

    def _clear_all_data(self):
        """Delete imported ticket data and derived analytics, but preserve
        NLP scan results (scan history, findings, sub-taxonomy, classifications).

        NLP scan results represent expensive Gemini API calls that should
        not be lost on session close. They remain useful across imports.

        More reliable than file deletion on Windows where SQLite WAL/SHM
        files may be locked by other processes or lingering handles.
        """
        # Tables to preserve across clear operations
        PRESERVE_TABLES = {
            'nlp_scan_runs',           # Scan history metadata
            'nlp_batches',             # Batch details (raw_response for recovery)
            'nlp_ticket_classifications',  # Classification results
            'nlp_findings',            # Meta-analysis findings
            'sub_patterns',            # Learned sub-taxonomy
            'sub_pattern_ngrams',      # Sub-pattern n-gram indexes
            'sub_pattern_snapshots',   # Sub-pattern history
            'provisional_classifications',  # Provisional classifications
            'prompt_library',          # User-customized prompts
            'source_registry',         # Source metadata survives reset for re-import
        }

        try:
            conn = self.db.conn
            conn.execute("PRAGMA foreign_keys = OFF")

            # 1. Drop ALL FTS virtual tables (shared + per-source)
            #    FTS5 virtual tables cannot be DELETEd, only DROPped.
            try:
                fts_tables = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%_fts'"
                ).fetchall()
                for (fts_name,) in fts_tables:
                    conn.execute(f"DROP TABLE IF EXISTS [{fts_name}]")
                conn.execute("DROP TABLE IF EXISTS conversations_fts")
            except Exception:
                pass

            # 2. Get remaining user tables (skip sqlite internals + FTS shadow leftovers)
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' "
                "AND name NOT LIKE '%_fts%'"
            ).fetchall()

            # 3. Delete from each table individually, skipping preserved NLP tables
            for (table_name,) in tables:
                if table_name in PRESERVE_TABLES:
                    continue
                try:
                    conn.execute(f"DELETE FROM [{table_name}]")
                except Exception:
                    pass

            conn.commit()
            conn.execute("PRAGMA foreign_keys = ON")

            # 4. VACUUM to reclaim disk space (must be outside transaction)
            try:
                conn.execute("VACUUM")
            except Exception:
                pass
        except Exception:
            pass

    def closeEvent(self, event):
        """Prompt to clear session data on close."""
        if not hasattr(self, "settings_page"):
            # Enablement-only session — the product settings page (and its
            # staging-clear semantics) never mounted; keep everything.
            self.db.close()
            event.accept()
            return
        # Only prompt if there's real (non-test) data loaded
        is_test = self.settings_page.is_test_data_enabled()
        count = self.db.get_ticket_count()

        if count > 0 and not is_test:
            from PySide6.QtWidgets import QMessageBox
            msg = QMessageBox(self)
            msg.setWindowTitle("Close Alma Insights")
            msg.setText("Clear session data?")
            msg.setInformativeText(
                "Staging data (raw import rows) will be cleared.\n"
                "All tickets, enrichments, and reports are preserved in the database.\n\n"
                "Choose 'Keep & Close' to retain everything as-is."
            )
            msg.setStandardButtons(
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel
            )
            msg.setDefaultButton(QMessageBox.Yes)
            msg.button(QMessageBox.Yes).setText("Clear && Close")
            msg.button(QMessageBox.No).setText("Keep && Close")
            msg.button(QMessageBox.Cancel).setText("Cancel")

            result = msg.exec()

            if result == QMessageBox.Cancel:
                event.ignore()
                return
            elif result == QMessageBox.Yes:
                # 1. Stop all background workers (they hold DB connections)
                self._stop_all_workers()

                # 2. Build 11.0: Clear ephemeral data only (preserve persistent)
                try:
                    from src.services.clear_session import clear_session_data
                    clear_session_data(str(self.db.db_path))
                except Exception as e:
                    import logging
                    logging.getLogger("alma.main").warning(
                        "Session clear failed: %s (data preserved)", e
                    )

                # 3. Close the main connection
                self.db.close()

                event.accept()
                return

        self.db.close()
        event.accept()
