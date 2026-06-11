"""Phase-1 boot smoke: mode-gated mounting, service matrix, runtime switch.

Run:  python scripts/_p1_boot_smoke.py enablement
      python scripts/_p1_boot_smoke.py product
Offscreen; asserts page/service expectations and prints boot timing.
"""

import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")  # cp1252 console can't print arrows

mode = sys.argv[1] if len(sys.argv) > 1 else "enablement"

from PySide6.QtWidgets import QApplication  # noqa: E402

from src.ui import app_modes  # noqa: E402

app_modes.set_cli_override(mode)
app = QApplication([])

t0 = time.time()
from src.ui.main_window import MainWindow  # noqa: E402

w = MainWindow()
boot = time.time() - t0

assert w._mode == mode, f"mode={w._mode}"
sidebar_ids = [pid for _, pid in w._sidebar_buttons]

if mode == "enablement":
    assert "torch" not in sys.modules, "torch must stay unloaded"
    assert not hasattr(w, "conversations_page"), "product pages must not build"
    assert not hasattr(w, "settings_page")
    assert getattr(w, "_schedule_manager", None) is None
    assert w._zendesk_monitor is None
    assert type(w.guru_page).__name__ == "EnablementPage"
    assert hasattr(w, "home_page")
    assert set(w._page_widgets) == {
        "home", "en_calendar", "en_tasks", "en_workbench",
        "en_analytics", "en_settings"
    }, w._page_widgets.keys()
    assert w._active_page == "home"
    assert sidebar_ids[0] == "home"
    print(f"[enablement] boot {boot:.2f}s sidebar={sidebar_ids}")

    # Runtime switch round trip
    w.switch_mode("product")
    assert w._mode == "product"
    assert hasattr(w, "conversations_page") and hasattr(w, "settings_page")
    assert w._schedule_manager is not None and w._schedule_manager.is_running()
    assert "conversations" in w._page_widgets
    assert w._active_page == "home"
    print(f"[switch→product] sidebar={[p for _, p in w._sidebar_buttons]}")

    w.switch_mode("enablement")
    assert w._mode == "enablement"
    assert not w._schedule_manager.is_running(), "schedule mgr must stop"
    assert w._active_page == "home"
    print("[switch→enablement] OK — services stopped, pages persisted")
else:
    assert hasattr(w, "conversations_page") and hasattr(w, "settings_page")
    assert hasattr(w, "dashboard_page") and hasattr(w, "data_warehouse_page")
    assert hasattr(w, "home_page")
    assert not hasattr(w, "guru_page"), "no guru page without the flag"
    assert "en_workbench" not in w._page_widgets
    assert w._schedule_manager is not None
    assert w._active_page == "home"
    assert sidebar_ids[0] == "home"
    assert w._product_wiring_done
    print(f"[product] boot {boot:.2f}s sidebar={sidebar_ids}")

w.db.close()
print("SMOKE OK")
