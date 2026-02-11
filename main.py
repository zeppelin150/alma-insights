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

    # Default font
    font = QFont("Segoe UI", 10)
    font.setStyleStrategy(QFont.PreferAntialias)
    app.setFont(font)

    # App icon
    icon_path = os.path.join(os.path.dirname(__file__), "assets", "alma_insights.ico")
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))

    # Launch main window
    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
