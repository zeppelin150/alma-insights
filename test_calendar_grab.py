"""
Visual test for CalendarPopup using grab() method.
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QPushButton
from PySide6.QtCore import QDate, Qt, QTimer
from PySide6.QtGui import QPixmap

app = QApplication.instance() or QApplication(sys.argv)

# Apply FULL global stylesheet — this is the real environment
from src.ui.theme import get_stylesheet
app.setStyleSheet(get_stylesheet())

from src.ui.widgets.date_picker import CalendarPopup

# Create a host window
host = QWidget()
host.setWindowTitle("Calendar Test")
host.setFixedSize(400, 450)
layout = QVBoxLayout(host)

# Embed the popup as a normal child widget (not as popup) for testing
popup = CalendarPopup(QDate(2025, 1, 15))
# Override the popup flags so it renders as a normal widget
popup.setWindowFlags(Qt.Widget)
popup.setAttribute(Qt.WA_TranslucentBackground, False)
popup.setFixedSize(304, 350)

layout.addWidget(popup)
host.show()
app.processEvents()

# Wait a bit then grab
def do_grab():
    # Grab the popup
    pix = popup.grab()
    pix.save("test_calendar_grab_popup.png")
    print(f"Saved popup grab: {pix.width()}x{pix.height()}")

    # Grab just the card
    card = popup._card
    pix2 = card.grab()
    pix2.save("test_calendar_grab_card.png")
    print(f"Saved card grab: {pix2.width()}x{pix2.height()}")

    # Grab a single day button to check it renders text
    btns = card.findChildren(QPushButton)
    day_btns = [b for b in btns if b.text().strip().isdigit()]
    if day_btns:
        # Grab day 15 (selected) and day 20 (normal)
        for btn in day_btns:
            d = int(btn.text())
            if d in (15, 19, 20):
                pix3 = btn.grab()
                pix3 = pix3.scaled(pix3.width() * 4, pix3.height() * 4)  # upscale for visibility
                pix3.save(f"test_calendar_grab_day{d}.png")
                print(f"Saved day {d} button grab (4x upscaled): {pix3.width()}x{pix3.height()}")

    host.close()
    app.quit()

QTimer.singleShot(500, do_grab)
app.exec()
