"""
Alma Insights — Application Entry Point
Launch with: python main.py
"""

import sys
import os

# Ensure the project root is in the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ── Clear stale bytecode on every launch ──
import importlib
sys.dont_write_bytecode = True  # Prevent new .pyc creation during dev
for root, dirs, files in os.walk(os.path.join(os.path.dirname(__file__), "src")):
    if "__pycache__" in dirs:
        import shutil
        shutil.rmtree(os.path.join(root, "__pycache__"), ignore_errors=True)
        dirs.remove("__pycache__")

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QIcon

from src.ui.theme import get_stylesheet
from src.ui.main_window import MainWindow


def main():
    # ── Memory profiler (opt-in: python main.py --profile) ──
    if "--profile" in sys.argv:
        sys.argv.remove("--profile")
        from src.data.memory_profiler import MemoryProfiler
        MemoryProfiler.start()

    # High DPI support
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv)
    app.setApplicationName("Alma Insights")
    app.setOrganizationName("Alma Health")
    app.setApplicationVersion("1.0.0")

    # Apply theme
    app.setStyleSheet(get_stylesheet())

    # ── Qt Error Guard (catches silent delegate/paint errors) ──
    from src.ui.qt_error_guard import QtErrorGuard
    guard = QtErrorGuard(app)

    # Default font
    font = QFont("Segoe UI", 10)
    font.setStyleStrategy(QFont.PreferAntialias)
    app.setFont(font)

    # App icon
    icon_path = os.path.join(os.path.dirname(__file__), "assets", "alma_insights.ico")
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))

    # Apply any staged update (before importing src modules that hold file locks)
    from src.updater.updater import apply_staged_update
    if apply_staged_update():
        # Re-import after file swap to pick up new code
        importlib.invalidate_caches()

    # Migrate settings from config/ → data/ (one-time, survives auto-updates)
    from src.data.settings_manager import get_settings_path
    get_settings_path()

    # WAL health check: auto-checkpoint if WAL grew beyond threshold while app
    # was closed (e.g. after an aborted scan). Bounded, non-blocking.
    try:
        from src.data.connection_factory import wal_health_check
        wal_result = wal_health_check()
        if wal_result["action_taken"] != "none":
            print(f"[startup] WAL {wal_result['action_taken']}: "
                  f"{wal_result['wal_size_mb']}MB, "
                  f"checkpointed {wal_result['checkpointed_pages']} pages")
    except Exception as exc:  # noqa: BLE001 — non-fatal at startup
        print(f"[startup] WAL health check failed: {exc}")

    # Launch main window
    window = MainWindow()
    window.show()

    # Wire guard to status bar if available
    if hasattr(window, 'qt_error_label'):
        guard.attach_to_status_bar(window.qt_error_label)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
