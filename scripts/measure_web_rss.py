"""RSS budget probe for the web pivot: what do THREE live web surfaces
(Agent chat + web Calendar + web Workbench) cost in memory on this machine?

    python scripts/measure_web_rss.py

Offscreen-safe; sums the Qt process AND its Chromium children (renderers/GPU
live out-of-process). Informational — run on both the Windows dev box and the
M1 to fill the M6 budget table. Uses psutil when available, else the Windows
API / a coarse fallback.
"""

import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# The sandbox/GPU overrides are the offscreen TEST recipe — the real app sets
# none of them, so apply them only when actually offscreen (they destabilize
# multi-view windowed runs).
if os.environ["QT_QPA_PLATFORM"] == "offscreen":
    os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")
    os.environ.setdefault(
        "QTWEBENGINE_CHROMIUM_FLAGS",
        "--no-sandbox --disable-gpu --disable-software-rasterizer --in-process-gpu")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _tree_rss_mb() -> tuple[float, int]:
    """(total RSS in MB for this process + children, child count)."""
    try:
        import psutil
        me = psutil.Process()
        procs = [me] + me.children(recursive=True)
        total = 0
        for p in procs:
            try:
                total += p.memory_info().rss
            except Exception:  # noqa: BLE001
                pass
        return total / (1024 * 1024), len(procs) - 1
    except ImportError:
        try:  # coarse main-process-only fallback
            import ctypes
            import ctypes.wintypes as wt

            class PMC(ctypes.Structure):
                _fields_ = [("cb", wt.DWORD), ("PageFaultCount", wt.DWORD),
                            ("PeakWorkingSetSize", ctypes.c_size_t),
                            ("WorkingSetSize", ctypes.c_size_t),
                            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                            ("PagefileUsage", ctypes.c_size_t),
                            ("PeakPagefileUsage", ctypes.c_size_t)]

            pmc = PMC()
            pmc.cb = ctypes.sizeof(PMC)
            ctypes.windll.psapi.GetProcessMemoryInfo(
                ctypes.windll.kernel32.GetCurrentProcess(),
                ctypes.byref(pmc), pmc.cb)
            return pmc.WorkingSetSize / (1024 * 1024), -1
        except Exception:  # noqa: BLE001
            return -1.0, -1


def _pump(app, seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.02)


def main() -> int:
    from PySide6.QtCore import QObject, Signal
    from PySide6.QtWidgets import QApplication
    from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: F401 (pre-app import)

    app = QApplication.instance() or QApplication([])
    base, _ = _tree_rss_mb()
    print(f"baseline (Qt app, no web):        {base:8.1f} MB", flush=True)

    class _FakeEngine(QObject):
        response_ready = Signal(str)
        error_occurred = Signal(str)
        busy_changed = Signal(bool)
        status_update = Signal(str)

        def send(self, text):
            self.response_ready.emit("ok")

    from src.services.enablement_web import (CalendarWebController,
                                             WorkbenchWebController)
    from src.ui.web.agent_page import AgentPage
    from src.ui.web.calendar_bridge import CalendarBridge
    from src.ui.web.web_host import WebHost
    from src.ui.web.workbench_bridge import WorkbenchBridge

    surfaces = []
    surfaces.append(("agent", AgentPage(_FakeEngine())))
    _pump(app, 1.5)
    m1, kids = _tree_rss_mb()
    print(f"+ Agent chat:                     {m1:8.1f} MB  (+{m1 - base:6.1f}, {kids} children)", flush=True)

    cal = CalendarWebController()
    cal_bridge = CalendarBridge(data_signal=cal.calendar_data,
                                refresh_fn=cal.request_refresh)
    surfaces.append(("calendar", WebHost(
        bridge=cal_bridge,
        channel_name="calendarBridge", route="/calendar",
        log_name="rss.cal")))
    _pump(app, 1.5)
    m2, kids = _tree_rss_mb()
    print(f"+ web Calendar:                   {m2:8.1f} MB  (+{m2 - m1:6.1f}, {kids} children)", flush=True)

    wb = WorkbenchWebController(md_to_html_fn=lambda md: f"<p>{md}</p>")
    wb_bridge = WorkbenchBridge(data_signal=wb.workbench_data,
                               refresh_fn=wb.request_refresh)
    surfaces.append(("workbench", WebHost(
        bridge=wb_bridge,
        channel_name="workbenchBridge", route="/workbench",
        log_name="rss.wb")))
    _pump(app, 2.0)
    m3, kids = _tree_rss_mb()
    print(f"+ web Workbench:                  {m3:8.1f} MB  (+{m3 - m2:6.1f}, {kids} children)", flush=True)
    print(f"web stack total over baseline:    {m3 - base:8.1f} MB", flush=True)
    del surfaces
    return 0


if __name__ == "__main__":
    sys.exit(main())
