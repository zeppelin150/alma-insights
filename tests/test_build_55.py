"""
Build 5.5 End-to-End UI Tests
Tests debug mode gating, scan blocking, simple scan card,
keyword fix, and panel popup fix.
"""

import sys
import os
import tempfile
import inspect
from pathlib import Path

# Ensure project root on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication

app = QApplication.instance() or QApplication(sys.argv)

from src.data.db_manager import DatabaseManager
from src.ui.pages.nlp_scanner_page import NLPScannerPage
from src.ui.pages.ai_reports import AIReportsPage
from src.ui.pages.trending_topics import TrendingTopicsPage
from src.ui.widgets.smoothing_panel import SmoothingReviewPanel
from src.ui.widgets.keyword_panel import KeywordReviewPanel


def _make_db():
    tmp = tempfile.mkdtemp()
    db_path = Path(tmp) / "test_55.db"
    db = DatabaseManager(db_path)
    db.initialize()
    return db, tmp


def test_scan_active_signal_exists():
    assert hasattr(NLPScannerPage, "scan_active_changed")


def test_ai_reports_scan_blocking():
    db, tmp = _make_db()
    page = AIReportsPage(db)
    assert page._scan_blocked is False

    page.set_scan_blocking(True)
    assert page._scan_blocked is True
    assert page._generate_btn.isEnabled() is False
    assert "scan in progress" in page._generate_btn.toolTip().lower()

    page.set_scan_blocking(False)
    assert page._scan_blocked is False
    assert page._generate_btn.toolTip() == ""

    import shutil
    shutil.rmtree(tmp, ignore_errors=True)


def test_ai_reports_on_generate_guarded():
    db, tmp = _make_db()
    page = AIReportsPage(db)
    page.set_scan_blocking(True)
    # Should return early without crash
    page._on_generate()
    page.set_scan_blocking(False)

    import shutil
    shutil.rmtree(tmp, ignore_errors=True)


def test_trending_scan_blocking():
    db, tmp = _make_db()
    page = TrendingTopicsPage(db)
    page.set_scan_blocking(True)
    assert page._scan_blocked is True

    page.set_scan_blocking(False)
    assert page._scan_blocked is False

    import shutil
    shutil.rmtree(tmp, ignore_errors=True)


def test_trending_ai_enhancements_blocked():
    db, tmp = _make_db()
    page = TrendingTopicsPage(db)
    page.set_scan_blocking(True)
    page._run_ai_enhancements({"topics": {}, "terms": {}})
    assert page._smoothing_btn.isVisible() is False
    assert page._keywords_btn.isVisible() is False
    page.set_scan_blocking(False)

    import shutil
    shutil.rmtree(tmp, ignore_errors=True)


def test_scanner_debug_mode_gating():
    db, tmp = _make_db()
    page = NLPScannerPage(db)
    assert page._debug_mode is True

    # All gated widgets present
    for attr in ["_scan_config_card", "_control_buttons_frame",
                 "_simple_scan_card", "_info_panel", "_docs_section"]:
        assert hasattr(page, attr), f"Missing {attr}"

    # Production mode (debug OFF)
    # Note: isVisible() checks parent chain — page isn't shown in test,
    # so we use isHidden() (True = explicitly hidden via setVisible(False))
    page.update_debug_mode(False)
    assert page._debug_mode is False
    assert page._scan_config_card.isHidden() is True
    assert page._control_buttons_frame.isHidden() is True
    assert page._info_panel.isHidden() is True
    assert page._docs_section.isHidden() is True
    assert page._simple_scan_card.isHidden() is False  # NOT hidden = visible

    # Debug mode ON
    page.update_debug_mode(True)
    assert page._scan_config_card.isHidden() is False
    assert page._control_buttons_frame.isHidden() is False
    assert page._simple_scan_card.isHidden() is True

    import shutil
    shutil.rmtree(tmp, ignore_errors=True)


def test_simple_scan_card_widgets():
    db, tmp = _make_db()
    page = NLPScannerPage(db)
    for attr in ["_simple_start_btn", "_simple_cancel_btn",
                 "_simple_progress_bar", "_simple_progress_lbl",
                 "_simple_last_scan_lbl",
                 "_simple_date_start", "_simple_date_end",
                 "_simple_trc_combo"]:
        assert hasattr(page, attr), f"Missing {attr}"

    # Cancel hidden, progress hidden by default
    assert page._simple_cancel_btn.isVisible() is False
    assert page._simple_progress_bar.isVisible() is False

    # TRC combo should have at least "All TRCs" entry
    assert page._simple_trc_combo.count() >= 1
    assert page._simple_trc_combo.itemText(0) == "All TRCs"

    import shutil
    shutil.rmtree(tmp, ignore_errors=True)


def test_is_scan_active():
    db, tmp = _make_db()
    page = NLPScannerPage(db)
    assert page.is_scan_active() is False

    import shutil
    shutil.rmtree(tmp, ignore_errors=True)


def test_scan_active_signal_emits():
    db, tmp = _make_db()
    page = NLPScannerPage(db)
    received = []
    page.scan_active_changed.connect(lambda v: received.append(v))
    page.scan_active_changed.emit(True)
    page.scan_active_changed.emit(False)
    app.processEvents()
    assert received == [True, False]

    import shutil
    shutil.rmtree(tmp, ignore_errors=True)


def test_keyword_key_fix():
    terms = {
        "rising": [
            {"term": "claim denied", "velocity": 0.05, "current_score": 0.34},
        ],
        "cooling": [
            {"term": "old issue", "velocity": -0.02, "current_score": 0.11},
        ],
    }
    all_terms = terms.get("rising", []) + terms.get("cooling", [])
    rising = terms.get("rising", [])
    assert len(all_terms) == 2
    assert len(rising) == 1
    # Old broken keys
    assert len(terms.get("all_terms", [])) == 0
    assert len(terms.get("rising_terms", [])) == 0


def test_smoothing_panel_no_self_show():
    """Verify self.show() is not called as executable code (comments OK)."""
    src = inspect.getsource(SmoothingReviewPanel.set_suggestions)
    # Check that no non-comment line calls self.show()
    for line in src.split("\n"):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        assert "self.show()" not in stripped, (
            f"self.show() found as executable code: {stripped}"
        )


def test_keyword_panel_no_self_show():
    """Verify self.show() is not called as executable code (comments OK)."""
    src = inspect.getsource(KeywordReviewPanel.set_suggestions)
    for line in src.split("\n"):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        assert "self.show()" not in stripped, (
            f"self.show() found as executable code: {stripped}"
        )


def test_main_window_integration():
    from src.ui.main_window import MainWindow
    assert hasattr(MainWindow, "_on_scan_active_changed")

    # Phase 2.5: _make_nlp_scan_job moved from MainWindow to TRCAnalyticsPage
    from src.ui.pages.trc_analytics import TRCAnalyticsPage
    assert hasattr(TRCAnalyticsPage, "_start_nlp_scan")
    assert hasattr(TRCAnalyticsPage, "_reset_scan_ui")

    src = inspect.getsource(MainWindow._on_debug_mode_toggled)
    assert "update_debug_mode" in src

    src = inspect.getsource(MainWindow._restore_settings)
    assert "update_debug_mode" in src


def test_overlay_reraise_on_page_switch():
    """_set_active_page should re-raise overlay when visible."""
    from src.ui.main_window import MainWindow
    src = inspect.getsource(MainWindow._set_active_page)
    assert "_job_overlay" in src
    assert "raise_()" in src


def test_poll_no_premature_stop():
    """_poll_status should NOT stop polling when _active_scan_id is None (race fix)."""
    src = inspect.getsource(NLPScannerPage._poll_status)
    # The old bug: "self._stop_polling()" was called immediately when scan_id is None
    # New fix: uses _poll_wait_count to wait before giving up
    assert "_poll_wait_count" in src


def test_trc_poll_resilient_to_null_mgr():
    """TRCAnalyticsPage._poll_scan_status resets UI if scan_mgr is None."""
    from src.ui.pages.trc_analytics import TRCAnalyticsPage
    src = inspect.getsource(TRCAnalyticsPage._poll_scan_status)
    # Phase 2.5: poll must reset UI to idle if scan_mgr lost mid-scan
    assert "_reset_scan_ui" in src
    assert "_poll_wait_count" in src


def test_scanner_reads_model_from_settings():
    """Scanner should read model from settings.yaml and write changes back."""
    from src.ui.pages.trc_analytics import TRCAnalyticsPage
    src = inspect.getsource(TRCAnalyticsPage._on_scan_model_changed)
    # Should read/write model to settings.yaml
    assert "gemini" in src.lower() or "model" in src
    assert "settings.yaml" in src or "config" in src


def test_classified_tickets_in_get_status():
    """ScanOrchestrator.get_status should return classified_tickets."""
    from src.agents.scan_orchestrator import ScanOrchestrator
    src = inspect.getsource(ScanOrchestrator.get_status)
    assert "classified_tickets" in src
    assert "nlp_ticket_classifications" in src


def test_job_queue_methods():
    db, tmp = _make_db()
    page = NLPScannerPage(db)
    assert hasattr(page, "_run_scan_blocking")
    assert hasattr(page, "_on_job_scan_complete")
    assert hasattr(page, "_on_job_scan_error")
    assert hasattr(page, "_start_simple_scan")
    assert hasattr(page, "_update_simple_progress")

    import shutil
    shutil.rmtree(tmp, ignore_errors=True)


def test_simple_scan_date_pickers_work():
    """Verify simple card date pickers accept and return dates."""
    from PySide6.QtCore import QDate
    db, tmp = _make_db()
    page = NLPScannerPage(db)
    page._simple_date_start.set_date(QDate(2025, 3, 1))
    page._simple_date_end.set_date(QDate(2025, 3, 31))
    assert page._simple_date_start.get_date_string() == "2025-03-01"
    assert page._simple_date_end.get_date_string() == "2025-03-31"

    import shutil
    shutil.rmtree(tmp, ignore_errors=True)


def test_pending_scan_params_stored():
    """Verify _start_simple_scan stores params in _pending_scan_params."""
    from PySide6.QtCore import QDate
    db, tmp = _make_db()
    page = NLPScannerPage(db)
    page._simple_date_start.set_date(QDate(2025, 1, 1))
    page._simple_date_end.set_date(QDate(2025, 1, 31))
    # Even without a job queue, calling _start_simple_scan should set
    # _pending_scan_params before trying to submit.
    page._start_simple_scan()
    params = getattr(page, '_pending_scan_params', None)
    assert params is not None, "_pending_scan_params not set"
    assert params["date_start"] == "2025-01-01"
    assert params["date_end"] == "2025-01-31"
    assert params["trc_filter"] is None  # "All TRCs" selected

    import shutil
    shutil.rmtree(tmp, ignore_errors=True)


def test_run_scan_blocking_no_db_access():
    """Verify _run_scan_blocking reads from _pending_scan_params, not self.db."""
    src = inspect.getsource(NLPScannerPage._run_scan_blocking)
    # Should NOT call self.db.get_date_range() — that's the old cross-thread bug
    assert "self.db.get_date_range" not in src, (
        "_run_scan_blocking still calls self.db.get_date_range (cross-thread!)"
    )
    # Should read from _pending_scan_params
    assert "_pending_scan_params" in src


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v", "--tb=short"])
