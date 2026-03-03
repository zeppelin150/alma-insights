"""
Alma Insights — Qt Layer Regression Tests

Boots the real app, exercises every page and widget, and catches
any silent Qt errors (delegate overrides, paint failures, signal
mismatches, missing attributes).

Usage:  python tests/test_qt_regression.py
"""

import sys
import os
import time
import threading

sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)
os.environ["PYTHONUNBUFFERED"] = "1"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Hard kill after 60s (covers total test time)
def _watchdog():
    time.sleep(60)
    print("\n  TIMEOUT — force exit after 60s", flush=True)
    os._exit(2)
threading.Thread(target=_watchdog, daemon=True).start()

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt

# ── Captured errors ──
errors = []

def capture_unraisable(unraisable):
    exc = unraisable.exc_value
    obj = unraisable.object
    errors.append({
        "type": type(exc).__name__,
        "msg": str(exc)[:300],
        "object": repr(obj)[:100] if obj else "None",
    })

sys.unraisablehook = capture_unraisable


def pump(app, ms=200):
    """Non-blocking event pump with wall-clock timeout."""
    deadline = time.time() + ms / 1000
    while time.time() < deadline:
        app.processEvents()
        time.sleep(0.01)


def main():
    print("=" * 70)
    print("  Qt LAYER REGRESSION TESTS")
    print("=" * 70, flush=True)

    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv[:1])

    from src.ui.theme import get_stylesheet
    app.setStyleSheet(get_stylesheet())

    print("  Booting MainWindow...", flush=True)
    from src.ui.main_window import MainWindow
    window = MainWindow()
    window.resize(1200, 800)
    window.show()
    pump(app, 500)
    print("  Ready.\n", flush=True)

    passed = 0
    failed = 0
    results = []

    def run_test(name, fn):
        nonlocal passed, failed
        print(f"  Running: {name}...", end="", flush=True)
        errors.clear()
        try:
            fn()
            pump(app, 300)
        except Exception as e:
            results.append(("CRASH", name, f"{type(e).__name__}: {e}"))
            failed += 1
            print(f" CRASH", flush=True)
            return

        if errors:
            unique = {}
            for e in errors:
                key = f"{e['type']}: {e['msg'][:80]}"
                unique[key] = unique.get(key, 0) + 1
            detail_parts = []
            for key, count in unique.items():
                detail_parts.append(f"[{count}x] {key}")
            results.append(("FAIL", name, "; ".join(detail_parts)))
            failed += 1
            print(f" FAIL ({len(errors)} errors)", flush=True)
        else:
            results.append(("PASS", name, ""))
            passed += 1
            print(f" PASS", flush=True)

    # ═══════════════════════════════════════════
    #  1. _NoFocusDelegate — the exact bug we fixed
    # ═══════════════════════════════════════════

    def test_delegate_standalone():
        from src.ui.theme import _NoFocusDelegate
        from PySide6.QtWidgets import QTableWidget, QTableWidgetItem

        table = QTableWidget(5, 3, window)  # parent to window so it paints
        delegate = _NoFocusDelegate(table)
        table.setItemDelegate(delegate)
        for r in range(5):
            for c in range(3):
                table.setItem(r, c, QTableWidgetItem(f"cell {r},{c}"))
        table.setCurrentCell(2, 1)
        table.setVisible(True)
        pump(app, 200)
        table.setVisible(False)
        table.deleteLater()

    run_test("1. _NoFocusDelegate paint cycle", test_delegate_standalone)

    # ═══════════════════════════════════════════
    #  2. configure_table + selection
    # ═══════════════════════════════════════════

    def test_configure_table():
        from src.ui.theme import configure_table
        from PySide6.QtWidgets import QTableWidget, QTableWidgetItem

        table = QTableWidget(20, 6, window)
        configure_table(table)
        for r in range(20):
            for c in range(6):
                table.setItem(r, c, QTableWidgetItem(f"d{r}{c}"))
        table.setCurrentCell(5, 0)
        table.setVisible(True)
        pump(app, 200)
        table.setCurrentCell(15, 2)
        pump(app, 100)
        table.setVisible(False)
        table.deleteLater()

    run_test("2. configure_table with row selection", test_configure_table)

    # ═══════════════════════════════════════════
    #  3. configure_tree + selection
    # ═══════════════════════════════════════════

    def test_configure_tree():
        from src.ui.theme import configure_tree
        from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem

        tree = QTreeWidget(window)
        tree.setColumnCount(2)
        configure_tree(tree)
        for i in range(10):
            tree.addTopLevelItem(QTreeWidgetItem([f"item {i}", f"val {i}"]))
        tree.setCurrentItem(tree.topLevelItem(3))
        tree.setVisible(True)
        pump(app, 200)
        tree.setVisible(False)
        tree.deleteLater()

    run_test("3. configure_tree with selection", test_configure_tree)

    # ═══════════════════════════════════════════
    #  4-10. Each page renders without Qt errors
    # ═══════════════════════════════════════════

    page_map = [
        (0, "Conversation Search"),
        (1, "TRC Analytics"),
        (2, "Trending Topics"),
        (3, "Incidents"),
        (4, "AI Reports"),
        (5, "A/B Compare"),
        (6, "Smart Reporting"),
        (7, "Settings"),
    ]

    for test_num, (idx, name) in enumerate(page_map, start=4):
        def make_fn(i):
            def fn():
                if i < window.content_stack.count():
                    window.content_stack.setCurrentIndex(i)
                    pump(app, 300)
            return fn
        run_test(f"{test_num}. Page render: {name}", make_fn(idx))

    next_num = 4 + len(page_map)

    # ═══════════════════════════════════════════
    #  Search results table with fake data
    # ═══════════════════════════════════════════

    def test_search_results():
        window.content_stack.setCurrentIndex(0)
        pump(app, 100)
        page = window.conversations_page
        fake = [{
            "ticket_id": str(10000 + i),
            "subject": f"Test ticket {i}",
            "trc_code": "Test TRC",
            "trc_label": "Test TRC",
            "status": "solved",
            "csat_score": 3,
            "created_at": "2025-01-15",
            "message_count": 5,
            "client_messages": 2,
            "agent_messages": 3,
            "thread_preview": f"Preview {i}...",
        } for i in range(50)]
        page._current_results = fake
        page._populate_table(fake)
        pump(app, 200)
        page.results_table.setCurrentCell(10, 0)
        pump(app, 200)

    run_test(f"{next_num}. Search results table (50 rows)", test_search_results)

    # ═══════════════════════════════════════════
    #  12. Toast notifications
    # ═══════════════════════════════════════════

    def test_toasts():
        if hasattr(window, '_toasts'):
            window._toasts.show_toast("Test success", toast_type="success")
            pump(app, 200)
            window._toasts.show_toast("Test error", toast_type="error")
            pump(app, 200)

    run_test(f"{next_num + 1}. Toast notifications", test_toasts)

    # ═══════════════════════════════════════════
    #  13. Empty state
    # ═══════════════════════════════════════════

    def test_empty_state():
        from src.ui.widgets.empty_state import EmptyState
        es = EmptyState(
            message="Test", icon="search", heading="None",
            description="Nothing", action_label="Retry", parent=window,
        )
        es.setVisible(True)
        pump(app, 200)
        es.setVisible(False)
        es.deleteLater()

    run_test(f"{next_num + 2}. EmptyState widget", test_empty_state)

    # ═══════════════════════════════════════════
    #  14. Filter chip bar
    # ═══════════════════════════════════════════

    def test_chips():
        from src.ui.widgets.filter_chip_bar import FilterChipBar
        bar = FilterChipBar(window)
        bar.set_filters({"TRC": "Billing", "CSAT": "3", "Keyword": "refund"})
        bar.setVisible(True)
        pump(app, 200)
        bar.setVisible(False)
        bar.deleteLater()

    run_test(f"{next_num + 3}. FilterChipBar", test_chips)

    # ═══════════════════════════════════════════
    #  15. Skeleton widget
    # ═══════════════════════════════════════════

    def test_skeleton():
        try:
            from src.ui.widgets.skeleton import SkeletonWidget
            s = SkeletonWidget(parent=window)
            s.setVisible(True)
            pump(app, 300)
            s.setVisible(False)
            s.deleteLater()
        except ImportError:
            pass

    run_test(f"{next_num + 4}. Skeleton shimmer", test_skeleton)

    # ═══════════════════════════════════════════
    #  REPORT
    # ═══════════════════════════════════════════

    window.close()
    pump(app, 100)

    print("\n" + "=" * 70)
    for status, name, detail in results:
        if status == "PASS":
            print(f"  PASS   {name}")
        elif status == "FAIL":
            print(f"  FAIL   {name}")
            print(f"         {detail}")
        elif status == "CRASH":
            print(f"  CRASH  {name}")
            print(f"         {detail}")

    print(f"\n  {passed} passed, {failed} failed")
    print("=" * 70, flush=True)

    os._exit(1 if failed > 0 else 0)


if __name__ == "__main__":
    main()
