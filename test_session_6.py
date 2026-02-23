"""
Session 6 Tests -- UI Integration
Verify NLP Scanner page imports, instantiation, and wiring.
Tests are non-interactive (headless) -- no Qt event loop required
for import and construction checks.
"""

import sys
import os
import json
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# Must set QT_QPA_PLATFORM before importing any Qt modules
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtWidgets import QApplication

# Create minimal Qt application for widget tests
app = QApplication.instance() or QApplication(sys.argv)


def test_nlp_scanner_page_import():
    """Verify NLP Scanner page can be imported."""
    print("=" * 60)
    print("TEST 1: NLP Scanner page import")
    print("=" * 60)

    from src.ui.pages.nlp_scanner_page import NLPScannerPage
    print("  NLPScannerPage imported OK")
    print("  OK TEST 1 PASSED")


def test_nlp_scanner_page_instantiation():
    """Verify NLP Scanner page can be instantiated."""
    print("\n" + "=" * 60)
    print("TEST 2: NLP Scanner page instantiation")
    print("=" * 60)

    from src.ui.pages.nlp_scanner_page import NLPScannerPage
    from src.data.db_manager import DatabaseManager

    db_path = Path(tempfile.gettempdir()) / "alma_test_session_6.db"
    if db_path.exists():
        db_path.unlink()

    db = DatabaseManager(db_path)
    db.initialize()

    page = NLPScannerPage(db)

    # Verify signals exist
    assert hasattr(page, 'deep_dive_requested'), "FAIL: Missing deep_dive_requested signal"
    assert hasattr(page, 'view_tickets_requested'), "FAIL: Missing view_tickets_requested signal"

    # Verify key methods exist
    assert hasattr(page, '_start_scan'), "FAIL: Missing _start_scan"
    assert hasattr(page, '_pause_scan'), "FAIL: Missing _pause_scan"
    assert hasattr(page, '_resume_scan'), "FAIL: Missing _resume_scan"
    assert hasattr(page, '_cancel_scan'), "FAIL: Missing _cancel_scan"
    assert hasattr(page, '_update_cost_estimate'), "FAIL: Missing _update_cost_estimate"
    assert hasattr(page, '_show_taxonomy_dialog'), "FAIL: Missing _show_taxonomy_dialog"
    assert hasattr(page, 'set_drilldown_panel'), "FAIL: Missing set_drilldown_panel"

    db.close()
    if db_path.exists():
        db_path.unlink()

    print("  NLPScannerPage instantiated OK")
    print("  All signals and methods present")
    print("  OK TEST 2 PASSED")


def test_main_window_page_constants():
    """Verify main_window page constants are updated correctly."""
    print("\n" + "=" * 60)
    print("TEST 3: Main window page constants")
    print("=" * 60)

    from src.ui.main_window import MainWindow

    assert MainWindow.PAGE_CONVERSATIONS == 0
    assert MainWindow.PAGE_DASHBOARD == 1
    assert MainWindow.PAGE_TRENDING == 2
    assert MainWindow.PAGE_INCIDENTS == 3
    assert MainWindow.PAGE_NLP_SCANNER == 4
    assert MainWindow.PAGE_REPORTS == 5
    assert MainWindow.PAGE_AB_COMPARE == 6
    assert MainWindow.PAGE_SMART_REPORTING == 7
    assert MainWindow.PAGE_SETTINGS == 8

    print("  Page constants verified:")
    print(f"    NLP_SCANNER = {MainWindow.PAGE_NLP_SCANNER}")
    print(f"    REPORTS = {MainWindow.PAGE_REPORTS}")
    print(f"    SETTINGS = {MainWindow.PAGE_SETTINGS}")
    print("  OK TEST 3 PASSED")


def test_nlp_scanner_import_in_main_window():
    """Verify NLPScannerPage is imported in main_window."""
    print("\n" + "=" * 60)
    print("TEST 4: NLP Scanner imported in main_window")
    print("=" * 60)

    # Check the import exists
    from src.ui.main_window import MainWindow
    assert hasattr(MainWindow, 'PAGE_NLP_SCANNER'), "FAIL: PAGE_NLP_SCANNER not defined"
    assert hasattr(MainWindow, '_on_nlp_deep_dive'), "FAIL: _on_nlp_deep_dive not defined"
    assert hasattr(MainWindow, '_on_nlp_view_tickets'), "FAIL: _on_nlp_view_tickets not defined"

    print("  MainWindow has NLP Scanner integration methods")
    print("  OK TEST 4 PASSED")


def test_ai_reports_nlp_integration():
    """Verify AI Reports has NLP scan synthesis integration."""
    print("\n" + "=" * 60)
    print("TEST 5: AI Reports NLP integration")
    print("=" * 60)

    from src.ui.pages.ai_reports import AIReportsPage
    from src.data.db_manager import DatabaseManager

    db_path = Path(tempfile.gettempdir()) / "alma_test_session_6b.db"
    if db_path.exists():
        db_path.unlink()

    db = DatabaseManager(db_path)
    db.initialize()

    page = AIReportsPage(db)

    # Verify methods exist
    assert hasattr(page, '_generate_nlp_synthesis'), "FAIL: Missing _generate_nlp_synthesis"
    assert hasattr(page, 'load_nlp_finding'), "FAIL: Missing load_nlp_finding"

    # Verify NLP option in prompt combo
    found_nlp = False
    for i in range(page._prompt_combo.count()):
        if page._prompt_combo.itemData(i) == "nlp_scan":
            found_nlp = True
            print(f"  Found NLP option at index {i}: '{page._prompt_combo.itemText(i)}'")
            break

    assert found_nlp, "FAIL: 'From NLP Scan' not in prompt combo"

    db.close()
    if db_path.exists():
        db_path.unlink()

    print("  OK TEST 5 PASSED")


def test_chat_widget_finding_aware():
    """Verify chat widget has finding-aware drilldown support."""
    print("\n" + "=" * 60)
    print("TEST 6: Chat widget finding-aware drilldown")
    print("=" * 60)

    from src.ui.widgets.chat_widget import ReportChatWidget

    widget = ReportChatWidget()

    # Verify the finding drilldown method exists
    assert hasattr(widget, '_send_finding_drilldown'), \
        "FAIL: Missing _send_finding_drilldown"

    # After clear, _nlp_finding_id should be None
    widget.clear()
    assert widget._nlp_finding_id is None, "FAIL: _nlp_finding_id not reset on clear"

    # Setting finding context
    widget._nlp_finding_id = "test-finding-123"
    assert widget._nlp_finding_id == "test-finding-123"

    # Clear resets it
    widget.clear()
    assert widget._nlp_finding_id is None

    print("  Finding-aware drilldown methods present")
    print("  _nlp_finding_id lifecycle works correctly")
    print("  OK TEST 6 PASSED")


def test_conversation_search_filter():
    """Verify conversation search has filter_by_ticket_ids."""
    print("\n" + "=" * 60)
    print("TEST 7: Conversation search filter_by_ticket_ids")
    print("=" * 60)

    from src.ui.pages.conversation_search import ConversationSearchPage

    assert hasattr(ConversationSearchPage, 'filter_by_ticket_ids'), \
        "FAIL: Missing filter_by_ticket_ids method"

    print("  filter_by_ticket_ids method exists")
    print("  OK TEST 7 PASSED")


def test_db_manager_new_methods():
    """Verify new DB manager methods added for Session 6."""
    print("\n" + "=" * 60)
    print("TEST 8: DB manager new methods")
    print("=" * 60)

    from src.data.db_manager import DatabaseManager

    db_path = Path(tempfile.gettempdir()) / "alma_test_session_6c.db"
    if db_path.exists():
        db_path.unlink()

    db = DatabaseManager(db_path)
    db.initialize()

    # Test get_latest_completed_scan (should return None on empty db)
    result = db.get_latest_completed_scan()
    assert result is None, "FAIL: Expected None for empty DB"
    print("  get_latest_completed_scan() -> None (empty DB) OK")

    # Test get_trc_list (should return empty list)
    result = db.get_trc_list()
    assert result == [], "FAIL: Expected empty list"
    print("  get_trc_list() -> [] OK")

    # Test get_ticket_count_in_range
    result = db.get_ticket_count_in_range("2025-01-01", "2025-12-31")
    assert result == 0, "FAIL: Expected 0"
    print("  get_ticket_count_in_range() -> 0 OK")

    # Test get_finding_ticket_ids (should return empty)
    result = db.get_finding_ticket_ids("nonexistent")
    assert result == [], "FAIL: Expected empty list"
    print("  get_finding_ticket_ids() -> [] OK")

    db.close()
    if db_path.exists():
        db_path.unlink()

    print("  OK TEST 8 PASSED")


if __name__ == "__main__":
    try:
        test_nlp_scanner_page_import()
        test_nlp_scanner_page_instantiation()
        test_main_window_page_constants()
        test_nlp_scanner_import_in_main_window()
        test_ai_reports_nlp_integration()
        test_chat_widget_finding_aware()
        test_conversation_search_filter()
        test_db_manager_new_methods()

        print("\n" + "=" * 60)
        print("ALL SESSION 6 TESTS PASSED")
        print("=" * 60)

    except Exception as e:
        print(f"\n\nFAILED: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
