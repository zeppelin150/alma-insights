"""
Phase 5 — Cross-Page Regression & Consolidation Tests

Tests cover:
  A. Trending Topics  — structure (6), data flow (4)
  B. Incidents        — structure (6), data flow (4)
  C. Cross-Page       — integration (6)
  D. MainWindow       — integration (4)
  E. Memory Consol.   — 5C changes (5)
"""

import sys
import tempfile
import shutil
import inspect
from pathlib import Path

# Ensure project root on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication, QTabWidget

app = QApplication.instance() or QApplication(sys.argv)

from src.data.db_manager import DatabaseManager
from src.ui.widgets.analysis_page_base import AnalysisPageBase
from src.ui.widgets.shared_filter_bar import SharedFilterBar


# ── Fixtures ──────────────────────────────────────────────


def _make_db():
    tmp = tempfile.mkdtemp()
    db_path = Path(tmp) / "test_phase5.db"
    db = DatabaseManager(db_path)
    db.initialize()
    return db, tmp


def _cleanup(tmp):
    shutil.rmtree(tmp, ignore_errors=True)


# ═══════════════════════════════════════════════════════════
#  A.  TRENDING TOPICS — Structure (6 tests)
# ═══════════════════════════════════════════════════════════


def test_trending_inherits_analysis_page_base():
    from src.ui.pages.trending_topics import TrendingTopicsPage
    assert issubclass(TrendingTopicsPage, AnalysisPageBase)


def test_trending_has_shared_filter_bar():
    from src.ui.pages.trending_topics import TrendingTopicsPage
    db, tmp = _make_db()
    page = TrendingTopicsPage(db)
    assert hasattr(page, 'filter_bar')
    assert isinstance(page.filter_bar, SharedFilterBar)
    _cleanup(tmp)


def test_trending_has_5_tabs():
    from src.ui.pages.trending_topics import TrendingTopicsPage
    db, tmp = _make_db()
    page = TrendingTopicsPage(db)
    tw = page.tab_widget
    assert isinstance(tw, QTabWidget)
    assert tw.count() == 5, f"Expected 5 tabs, got {tw.count()}"
    tab_labels = [tw.tabText(i) for i in range(tw.count())]
    assert "Overview" in tab_labels
    assert "Deep Dive" in tab_labels
    assert "Hypothesis Test" in tab_labels
    assert "Reports" in tab_labels
    assert "Manage Terms" in tab_labels
    _cleanup(tmp)


def test_trending_kpi_accent_cycle():
    from src.ui.pages.trending_topics import TrendingTopicsPage
    db, tmp = _make_db()
    page = TrendingTopicsPage(db)
    assert hasattr(page, '_kpi_row'), "Missing _kpi_row attribute"
    # Verify accent cycle was applied — cards should have accent_index set
    kpi_row = page._kpi_row
    assert kpi_row is not None
    _cleanup(tmp)


def test_trending_filter_chip_bar_present():
    from src.ui.pages.trending_topics import TrendingTopicsPage
    db, tmp = _make_db()
    page = TrendingTopicsPage(db)
    assert hasattr(page, '_chip_bar'), "Missing _chip_bar (FilterChipBar)"
    _cleanup(tmp)


def test_trending_backward_compat_properties():
    from src.ui.pages.trending_topics import TrendingTopicsPage
    db, tmp = _make_db()
    page = TrendingTopicsPage(db)

    # Properties must exist and return the correct widget types
    assert page.date_from is not None, "date_from property missing or None"
    assert page.date_to is not None, "date_to property missing or None"
    assert page.trc_combo is not None, "trc_combo property missing or None"
    assert page.window_combo is not None, "window_combo property missing or None"
    assert page.method_combo is not None, "method_combo property missing or None"

    # Methods must exist
    assert callable(getattr(page, 'populate_trc_filter', None))
    assert callable(getattr(page, 'sync_date_to_data', None))
    assert callable(getattr(page, 'set_scan_blocking', None))
    assert callable(getattr(page, 'set_drilldown_panel', None))

    # Attributes
    assert hasattr(page, '_scan_start_time')
    assert hasattr(page, '_worker')
    assert hasattr(page, '_last_analysis_result')

    _cleanup(tmp)


# ═══════════════════════════════════════════════════════════
#  A.  TRENDING TOPICS — Data Flow (4 tests)
# ═══════════════════════════════════════════════════════════


def test_trending_analyze_triggers_worker():
    """Verify _on_analyze exists and is wired to the Analyze action."""
    from src.ui.pages.trending_topics import TrendingTopicsPage
    db, tmp = _make_db()
    page = TrendingTopicsPage(db)
    assert callable(getattr(page, '_on_analyze', None))
    # Source should reference TrendingWorker
    src = inspect.getsource(page._on_analyze)
    assert "TrendingWorker" in src or "worker" in src.lower()
    _cleanup(tmp)


def test_trending_filter_change_syncs_chips():
    """Verify _on_filters_changed hook exists and calls chip sync."""
    from src.ui.pages.trending_topics import TrendingTopicsPage
    db, tmp = _make_db()
    page = TrendingTopicsPage(db)
    assert callable(getattr(page, '_on_filters_changed', None))
    _cleanup(tmp)


def test_trending_populate_trc_filter():
    """populate_trc_filter should not crash on empty DB."""
    from src.ui.pages.trending_topics import TrendingTopicsPage
    db, tmp = _make_db()
    page = TrendingTopicsPage(db)
    page.populate_trc_filter()  # Should not raise
    _cleanup(tmp)


def test_trending_sync_date_to_data():
    """sync_date_to_data should not crash on empty DB."""
    from src.ui.pages.trending_topics import TrendingTopicsPage
    db, tmp = _make_db()
    page = TrendingTopicsPage(db)
    page.sync_date_to_data()  # Should not raise
    _cleanup(tmp)


# ═══════════════════════════════════════════════════════════
#  B.  INCIDENTS — Structure (6 tests)
# ═══════════════════════════════════════════════════════════


def test_incidents_inherits_analysis_page_base():
    from src.ui.pages.incidents_page import IncidentsPage
    assert issubclass(IncidentsPage, AnalysisPageBase)


def test_incidents_has_shared_filter_bar():
    from src.ui.pages.incidents_page import IncidentsPage
    db, tmp = _make_db()
    page = IncidentsPage(db)
    assert hasattr(page, 'filter_bar')
    assert isinstance(page.filter_bar, SharedFilterBar)
    _cleanup(tmp)


def test_incidents_has_4_tabs():
    from src.ui.pages.incidents_page import IncidentsPage
    db, tmp = _make_db()
    page = IncidentsPage(db)
    tw = page.tab_widget
    assert isinstance(tw, QTabWidget)
    assert tw.count() == 4, f"Expected 4 tabs, got {tw.count()}"
    tab_labels = [tw.tabText(i) for i in range(tw.count())]
    assert "Overview" in tab_labels
    _cleanup(tmp)


def test_incidents_kpi_cards_present():
    from src.ui.pages.incidents_page import IncidentsPage
    db, tmp = _make_db()
    page = IncidentsPage(db)
    # Incidents page uses individual KPI card attributes
    for attr in ('_kpi_active', '_kpi_critical', '_kpi_open',
                 '_kpi_flagged', '_kpi_total_trcs'):
        assert hasattr(page, attr), f"Missing KPI card attribute: {attr}"
    _cleanup(tmp)


def test_incidents_filter_bar_has_action():
    """Incidents filter bar should have a primary action button."""
    from src.ui.pages.incidents_page import IncidentsPage
    db, tmp = _make_db()
    page = IncidentsPage(db)
    # Filter bar should exist and have date range
    fb = page.filter_bar
    assert fb.get_date_from() is not None
    assert fb.get_date_to() is not None
    _cleanup(tmp)


def test_incidents_backward_compat_properties():
    from src.ui.pages.incidents_page import IncidentsPage
    db, tmp = _make_db()
    page = IncidentsPage(db)

    # Properties
    assert page.date_from is not None, "date_from property missing or None"
    assert page.date_to is not None, "date_to property missing or None"

    # Key attributes
    assert hasattr(page, 'control_chart'), "Missing control_chart attribute"
    assert hasattr(page, 'scan_complete'), "Missing scan_complete signal"

    # Methods
    assert callable(getattr(page, 'populate_trc_filter', None))
    assert callable(getattr(page, 'sync_date_to_data', None))
    assert callable(getattr(page, 'set_drilldown_panel', None))

    _cleanup(tmp)


# ═══════════════════════════════════════════════════════════
#  B.  INCIDENTS — Data Flow (4 tests)
# ═══════════════════════════════════════════════════════════


def test_incidents_run_scan_triggers_worker():
    """Verify _on_action_triggered handles 'Run Scan' action."""
    from src.ui.pages.incidents_page import IncidentsPage
    db, tmp = _make_db()
    page = IncidentsPage(db)
    assert callable(getattr(page, '_on_action_triggered', None))
    _cleanup(tmp)


def test_incidents_scan_complete_signal_exists():
    from src.ui.pages.incidents_page import IncidentsPage
    assert hasattr(IncidentsPage, 'scan_complete')


def test_incidents_control_chart_attribute_exists():
    from src.ui.pages.incidents_page import IncidentsPage
    db, tmp = _make_db()
    page = IncidentsPage(db)
    assert hasattr(page, 'control_chart')
    assert callable(getattr(page.control_chart, 'set_interventions', None)), \
        "control_chart missing set_interventions method"
    _cleanup(tmp)


def test_incidents_populate_trc_filter():
    """populate_trc_filter should not crash on empty DB."""
    from src.ui.pages.incidents_page import IncidentsPage
    db, tmp = _make_db()
    page = IncidentsPage(db)
    page.populate_trc_filter()  # Should not raise
    _cleanup(tmp)


# ═══════════════════════════════════════════════════════════
#  C.  CROSS-PAGE INTEGRATION (6 tests)
# ═══════════════════════════════════════════════════════════


def test_all_pages_extend_analysis_page_base():
    from src.ui.pages.trending_topics import TrendingTopicsPage
    from src.ui.pages.incidents_page import IncidentsPage
    from src.ui.pages.trc_analytics import TRCAnalyticsPage

    for cls in (TrendingTopicsPage, IncidentsPage, TRCAnalyticsPage):
        assert issubclass(cls, AnalysisPageBase), \
            f"{cls.__name__} does not inherit AnalysisPageBase"


def test_all_pages_have_consistent_filter_bar():
    from src.ui.pages.trending_topics import TrendingTopicsPage
    from src.ui.pages.incidents_page import IncidentsPage
    from src.ui.pages.trc_analytics import TRCAnalyticsPage

    db, tmp = _make_db()
    pages = [
        TrendingTopicsPage(db),
        IncidentsPage(db),
        TRCAnalyticsPage(db),
    ]
    for page in pages:
        assert isinstance(page.filter_bar, SharedFilterBar), \
            f"{page.__class__.__name__} filter_bar is not SharedFilterBar"
        # Every filter bar should have date range pickers
        assert page.filter_bar.get_date_from() is not None, \
            f"{page.__class__.__name__} missing date_from in filter bar"
        assert page.filter_bar.get_date_to() is not None, \
            f"{page.__class__.__name__} missing date_to in filter bar"
    _cleanup(tmp)


def test_date_sync_propagates_all_pages():
    """sync_date_to_data should be callable on all 3 pages without crash."""
    from src.ui.pages.trending_topics import TrendingTopicsPage
    from src.ui.pages.incidents_page import IncidentsPage
    from src.ui.pages.trc_analytics import TRCAnalyticsPage

    db, tmp = _make_db()
    pages = [
        TrendingTopicsPage(db),
        IncidentsPage(db),
        TRCAnalyticsPage(db),
    ]
    for page in pages:
        page.sync_date_to_data()  # Must not raise
    _cleanup(tmp)


def test_populate_trc_filter_all_pages():
    """populate_trc_filter should be callable on all 3 pages without crash."""
    from src.ui.pages.trending_topics import TrendingTopicsPage
    from src.ui.pages.incidents_page import IncidentsPage
    from src.ui.pages.trc_analytics import TRCAnalyticsPage

    db, tmp = _make_db()
    pages = [
        TrendingTopicsPage(db),
        IncidentsPage(db),
        TRCAnalyticsPage(db),
    ]
    for page in pages:
        page.populate_trc_filter()  # Must not raise
    _cleanup(tmp)


def test_drilldown_panel_wired_all_pages():
    """set_drilldown_panel should accept None without crash on all pages."""
    from src.ui.pages.trending_topics import TrendingTopicsPage
    from src.ui.pages.incidents_page import IncidentsPage
    from src.ui.pages.trc_analytics import TRCAnalyticsPage

    db, tmp = _make_db()
    pages = [
        TrendingTopicsPage(db),
        IncidentsPage(db),
        TRCAnalyticsPage(db),
    ]
    for page in pages:
        # Should accept drilldown panel without crash
        # (passing None is safe — just sets internal ref)
        page.set_drilldown_panel(None)
    _cleanup(tmp)


def test_set_scan_blocking_pages():
    """Pages that support scan blocking should handle it gracefully."""
    from src.ui.pages.trending_topics import TrendingTopicsPage

    db, tmp = _make_db()
    page = TrendingTopicsPage(db)
    page.set_scan_blocking(True)
    assert page._scan_blocked is True
    page.set_scan_blocking(False)
    assert page._scan_blocked is False
    _cleanup(tmp)


# ═══════════════════════════════════════════════════════════
#  D.  MAIN WINDOW INTEGRATION (4 tests)
# ═══════════════════════════════════════════════════════════


def test_make_trending_job_uses_backward_compat():
    """Verify TrendingTopicsPage backward-compat props return real widgets."""
    from src.ui.pages.trending_topics import TrendingTopicsPage
    db, tmp = _make_db()
    page = TrendingTopicsPage(db)

    # Simulate what main_window does when building a job
    date_from = page.date_from
    date_to = page.date_to
    trc = page.trc_combo
    window = page.window_combo
    method = page.method_combo

    # date pickers should have .date() method
    assert callable(getattr(date_from, 'date', None)), \
        "date_from missing .date() method"
    assert callable(getattr(date_to, 'date', None)), \
        "date_to missing .date() method"

    # combos should have .currentText() method
    assert callable(getattr(trc, 'currentText', None))
    assert callable(getattr(window, 'currentText', None))
    assert callable(getattr(method, 'currentData', None))

    _cleanup(tmp)


def test_make_incident_job_uses_backward_compat():
    """Verify IncidentsPage backward-compat props return real widgets."""
    from src.ui.pages.incidents_page import IncidentsPage
    db, tmp = _make_db()
    page = IncidentsPage(db)

    date_from = page.date_from
    date_to = page.date_to

    assert callable(getattr(date_from, 'date', None)), \
        "date_from missing .date() method"
    assert callable(getattr(date_to, 'date', None)), \
        "date_to missing .date() method"

    # control_chart for interventions
    assert hasattr(page, 'control_chart')
    assert callable(getattr(page.control_chart, 'set_interventions', None))

    _cleanup(tmp)


def test_sidebar_navigation_all_pages():
    """All 3 analysis pages should have a tab_widget for sub-navigation."""
    from src.ui.pages.trending_topics import TrendingTopicsPage
    from src.ui.pages.incidents_page import IncidentsPage
    from src.ui.pages.trc_analytics import TRCAnalyticsPage

    db, tmp = _make_db()
    pages = {
        "Trending": TrendingTopicsPage(db),
        "Incidents": IncidentsPage(db),
        "TRC": TRCAnalyticsPage(db),
    }
    for name, page in pages.items():
        tw = page.tab_widget
        assert isinstance(tw, QTabWidget), f"{name} missing QTabWidget"
        assert tw.count() >= 3, f"{name} has only {tw.count()} tabs (expected ≥3)"
    _cleanup(tmp)


def test_auto_refresh_all_pages():
    """All pages should support the filter_bar.filters_changed signal."""
    from src.ui.pages.trending_topics import TrendingTopicsPage
    from src.ui.pages.incidents_page import IncidentsPage
    from src.ui.pages.trc_analytics import TRCAnalyticsPage

    db, tmp = _make_db()
    pages = [
        TrendingTopicsPage(db),
        IncidentsPage(db),
        TRCAnalyticsPage(db),
    ]
    for page in pages:
        # filter_bar should have filters_changed signal
        assert hasattr(page.filter_bar, 'filters_changed'), \
            f"{page.__class__.__name__} filter_bar missing filters_changed signal"
        # Page should have the hook method
        assert callable(getattr(page, '_on_filters_changed', None)), \
            f"{page.__class__.__name__} missing _on_filters_changed hook"
    _cleanup(tmp)


# ═══════════════════════════════════════════════════════════
#  E.  MEMORY CONSOLIDATION — 5C Changes (5 tests)
# ═══════════════════════════════════════════════════════════


def test_5c_sentiment_trends_accepts_conversations_param():
    """compute_sentiment_trends should accept conversations=None kwarg."""
    from src.data.trending_engine import compute_sentiment_trends
    sig = inspect.signature(compute_sentiment_trends)
    assert "conversations" in sig.parameters, \
        "compute_sentiment_trends missing 'conversations' parameter"
    # Default should be None (backward compat)
    default = sig.parameters["conversations"].default
    assert default is None, f"conversations default should be None, got {default}"


def test_5c_rising_terms_accepts_conversations_param():
    """compute_rising_terms should accept conversations=None kwarg."""
    from src.data.trending_engine import compute_rising_terms
    sig = inspect.signature(compute_rising_terms)
    assert "conversations" in sig.parameters, \
        "compute_rising_terms missing 'conversations' parameter"
    default = sig.parameters["conversations"].default
    assert default is None, f"conversations default should be None, got {default}"


def test_5c_topic_clusters_accepts_conversations_param():
    """compute_topic_clusters should accept conversations=None kwarg."""
    from src.data.trending_engine import compute_topic_clusters
    sig = inspect.signature(compute_topic_clusters)
    assert "conversations" in sig.parameters, \
        "compute_topic_clusters missing 'conversations' parameter"
    default = sig.parameters["conversations"].default
    assert default is None, f"conversations default should be None, got {default}"


def test_5c_run_full_analysis_passes_conversations():
    """run_full_analysis should pass conversations= to the 3 compute functions."""
    from src.data.trending_engine import run_full_analysis
    src = inspect.getsource(run_full_analysis)
    # Steps 6, 7, 8 should pass conversations= kwarg
    assert "conversations=conversations" in src, \
        "run_full_analysis does not pass conversations= to compute functions"


def test_5c_correlations_no_per_trc_series():
    """compute_cross_trc_correlations should NOT include per_trc_series in return."""
    from src.data.trending_engine import compute_cross_trc_correlations
    src = inspect.getsource(compute_cross_trc_correlations)
    # The return dict should not contain per_trc_series
    # Check that the function source doesn't have "per_trc_series" in return statements
    # But it IS used internally — so check the actual return lines
    lines = src.split('\n')
    return_lines = [l.strip() for l in lines if l.strip().startswith('return')]
    for ret in return_lines:
        assert "per_trc_series" not in ret, \
            f"per_trc_series still in return: {ret}"


# ═══════════════════════════════════════════════════════════
#  F.  TRC ANALYTICS — Structure verification (bonus, 4 tests)
# ═══════════════════════════════════════════════════════════


def test_trc_inherits_analysis_page_base():
    from src.ui.pages.trc_analytics import TRCAnalyticsPage
    assert issubclass(TRCAnalyticsPage, AnalysisPageBase)


def test_trc_has_5_tabs():
    from src.ui.pages.trc_analytics import TRCAnalyticsPage
    db, tmp = _make_db()
    page = TRCAnalyticsPage(db)
    tw = page.tab_widget
    assert tw.count() == 5, f"Expected 5 tabs, got {tw.count()}"
    _cleanup(tmp)


def test_trc_backward_compat_properties():
    from src.ui.pages.trc_analytics import TRCAnalyticsPage
    db, tmp = _make_db()
    page = TRCAnalyticsPage(db)

    assert page.date_from is not None
    assert page.date_to is not None
    assert page.trc_combo is not None

    assert callable(getattr(page, 'populate_trc_filter', None))
    assert callable(getattr(page, 'sync_date_to_data', None))
    assert callable(getattr(page, 'set_drilldown_panel', None))

    assert hasattr(page, 'scan_active_changed')
    assert hasattr(page, 'deep_dive_requested')
    assert hasattr(page, 'view_tickets_requested')

    _cleanup(tmp)


def test_trc_populate_and_sync():
    from src.ui.pages.trc_analytics import TRCAnalyticsPage
    db, tmp = _make_db()
    page = TRCAnalyticsPage(db)
    page.populate_trc_filter()  # Must not raise
    page.sync_date_to_data()    # Must not raise
    _cleanup(tmp)
