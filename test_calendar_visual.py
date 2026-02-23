"""
Visual test for the CalendarPopup.
Renders the popup offscreen, checks every button's geometry and style,
saves a screenshot, and prints diagnostics.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QDate, Qt
from PySide6.QtGui import QImage, QPainter

app = QApplication.instance() or QApplication(sys.argv)

# Apply the FULL global stylesheet (this is the root cause environment)
from src.ui.theme import get_stylesheet
app.setStyleSheet(get_stylesheet())

from src.ui.widgets.date_picker import CalendarPopup

# Create popup for January 2025 (31 days, starts on Wednesday)
test_date = QDate(2025, 1, 15)
popup = CalendarPopup(test_date)
popup.setWindowFlags(Qt.Widget)  # render as normal widget for testing
popup.setAttribute(Qt.WA_TranslucentBackground, False)  # disable for offscreen
popup.show()
app.processEvents()

print("=" * 60)
print(f"  CalendarPopup Visual Test — January 2025")
print(f"  Selected: Jan 15  |  Today: {QDate.currentDate().toString('MMM d')}")
print("=" * 60)

# Check the card
card = popup._card
print(f"\n[Card] geometry={card.geometry()}, visible={card.isVisible()}")

# Find all QPushButtons inside the card
from PySide6.QtWidgets import QPushButton
all_btns = card.findChildren(QPushButton)
print(f"\n[Buttons] Total QPushButtons in card: {len(all_btns)}")

day_btns = []
nav_btns = []
other_btns = []
for btn in all_btns:
    text = btn.text().strip()
    if text.isdigit() and 1 <= int(text) <= 31:
        day_btns.append(btn)
    elif text in ("\u25c0", "\u25b6"):
        nav_btns.append(btn)
    else:
        other_btns.append(btn)

print(f"  Day buttons: {len(day_btns)}")
print(f"  Nav buttons: {len(nav_btns)}")
print(f"  Other buttons: {len(other_btns)}  ({[b.text().encode('ascii', 'replace').decode() for b in other_btns]})")

# Check each day button's effective style
print(f"\n{'Day':>3} | {'Size':>9} | {'Visible':>7} | {'SS has padding:0':>17} | {'Background':>12}")
print("-" * 60)
problems = []
for btn in sorted(day_btns, key=lambda b: int(b.text())):
    day = int(btn.text())
    sz = f"{btn.width()}x{btn.height()}"
    vis = btn.isVisible()
    ss = btn.styleSheet()
    has_pad_reset = "padding: 0px" in ss or "padding:0px" in ss
    # Try to read effective background from stylesheet
    bg = "?"
    if "background: #14573F" in ss or "background: " + "#14573F" in ss:
        bg = "GREEN(sel)"
    elif "background: #F3F1EC" in ss:
        bg = "CREAM(today)"
    elif "background: #FFFFFF" in ss:
        bg = "WHITE"
    elif "background: transparent" in ss:
        bg = "TRANSPARENT"
    else:
        # Extract first background value
        import re
        m = re.search(r'background:\s*([^;]+);', ss)
        bg = m.group(1).strip() if m else "UNKNOWN"

    flag = ""
    if not vis:
        flag = " *** INVISIBLE"
        problems.append(f"Day {day} is not visible!")
    if not has_pad_reset:
        flag += " *** NO PADDING RESET"
        problems.append(f"Day {day} missing padding:0px reset!")

    print(f"{day:>3} | {sz:>9} | {str(vis):>7} | {str(has_pad_reset):>17} | {bg:>12}{flag}")

# Render to screenshot
print("\n[Screenshot] Rendering popup to test_calendar_visual.png ...")
from PySide6.QtCore import QPoint

img = QImage(popup.size(), QImage.Format_ARGB32_Premultiplied)
img.fill(Qt.white)
painter = QPainter(img)
popup.render(painter, QPoint(0, 0))
painter.end()
img.save("test_calendar_visual.png")
print(f"  Saved {img.width()}x{img.height()} image.")

# Also render just the card
img2 = QImage(card.size(), QImage.Format_ARGB32_Premultiplied)
img2.fill(Qt.white)
painter2 = QPainter(img2)
card.render(painter2, QPoint(0, 0))
painter2.end()
img2.save("test_calendar_card.png")
print(f"  Saved card-only {img2.width()}x{img2.height()} image.")

# Summary
print("\n" + "=" * 60)
if problems:
    print(f"  PROBLEMS FOUND: {len(problems)}")
    for p in problems:
        print(f"    - {p}")
else:
    print("  ALL CHECKS PASSED!")
    print(f"  - {len(day_btns)} day buttons, all visible, all have padding reset")
    print(f"  - {len(nav_btns)} nav buttons")
    print(f"  - Screenshots saved")
print("=" * 60)

popup.close()
app.quit()
