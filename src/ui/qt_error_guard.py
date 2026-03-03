"""
Alma Insights — Qt Error Guard

Catches silent Qt/PySide6 failures that would otherwise be swallowed:
  - Python exceptions in Qt overrides (paint, delegates, event handlers)
  - Infinite recursion in delegates/paint methods
  - Rapid memory growth indicating a runaway loop

Wired in at app startup. Errors surface in the status bar and logs
instead of silently eating gigabytes of RAM.

Usage:
    from src.ui.qt_error_guard import QtErrorGuard
    guard = QtErrorGuard(app)        # after QApplication()
    guard.attach_to_status_bar(bar)  # optional: show in UI
"""

import sys
import os
import gc
import logging
import traceback
import threading
from collections import defaultdict
from datetime import datetime

from PySide6.QtCore import QObject, QTimer, Signal

logger = logging.getLogger("alma.qt_guard")


class QtErrorGuard(QObject):
    """Intercepts and surfaces silent Qt errors."""

    # Emitted when a new unique error is captured: (summary, count)
    error_detected = Signal(str, int)

    # Emitted when memory growth exceeds threshold
    memory_warning = Signal(str)

    def __init__(self, app=None, parent=None):
        super().__init__(parent or app)

        # ── Error tracking ──
        self._errors = defaultdict(lambda: {"count": 0, "first_seen": None, "last_seen": None, "sample_tb": ""})
        self._error_lock = threading.Lock()
        self._total_errors = 0

        # ── Install hooks ──
        self._install_unraisable_hook()
        self._install_excepthook()

        # ── Memory watchdog (checks every 5s) ──
        self._last_rss = self._get_rss_mb()
        self._watchdog = QTimer(self)
        self._watchdog.setInterval(5000)
        self._watchdog.timeout.connect(self._check_memory)
        self._watchdog.start()

        # ── Status bar reference (optional) ──
        self._status_bar = None

        logger.info("QtErrorGuard installed")

    # ── Public API ──

    def attach_to_status_bar(self, label_widget):
        """Connect to a QLabel in the status bar for visible error alerts."""
        self._status_bar = label_widget

    def get_error_summary(self):
        """Return a formatted string of all captured errors."""
        with self._error_lock:
            if not self._errors:
                return "No Qt errors captured."

            lines = []
            lines.append(f"Qt Error Summary — {self._total_errors} total errors, {len(self._errors)} unique")
            lines.append("=" * 70)
            for key, info in sorted(self._errors.items(), key=lambda kv: kv[1]["count"], reverse=True):
                lines.append(f"\n[{info['count']}x] {key}")
                lines.append(f"  First: {info['first_seen']}  Last: {info['last_seen']}")
                if info["sample_tb"]:
                    # Indent traceback
                    for tb_line in info["sample_tb"].strip().splitlines()[-5:]:
                        lines.append(f"    {tb_line}")
            return "\n".join(lines)

    def get_error_count(self):
        return self._total_errors

    def clear(self):
        with self._error_lock:
            self._errors.clear()
            self._total_errors = 0

    # ── Hooks ──

    def _install_unraisable_hook(self):
        """sys.unraisablehook catches exceptions in Qt overrides (PySide6 paint, delegates, etc.)."""
        original_hook = getattr(sys, "unraisablehook", None)

        def hook(unraisable):
            self._record_error(
                exc=unraisable.exc_value,
                context=f"Qt override: {unraisable.object!r}" if unraisable.object else "Qt override (unknown)",
                tb=unraisable.exc_traceback,
            )
            # Still call original so it prints to stderr
            if original_hook and original_hook is not hook:
                try:
                    original_hook(unraisable)
                except Exception:
                    pass

        sys.unraisablehook = hook

    def _install_excepthook(self):
        """sys.excepthook catches uncaught exceptions on the main thread."""
        original_hook = sys.excepthook

        def hook(exc_type, exc_value, exc_tb):
            self._record_error(
                exc=exc_value,
                context=f"Uncaught {exc_type.__name__}",
                tb=exc_tb,
            )
            # Call original (prints traceback)
            if original_hook and original_hook is not hook:
                original_hook(exc_type, exc_value, exc_tb)

        sys.excepthook = hook

    # ── Error recording ──

    def _record_error(self, exc, context, tb=None):
        """Record a unique error with deduplication."""
        # Build a dedup key from exception type + message + innermost frame
        exc_type = type(exc).__name__
        exc_msg = str(exc)[:200]

        # Get innermost non-Qt frame for attribution
        origin = ""
        if tb:
            try:
                tb_text = "".join(traceback.format_tb(tb))
                # Find last alma source line
                for line in reversed(tb_text.splitlines()):
                    if "alma-insights" in line and "qt_error_guard" not in line:
                        origin = line.strip()
                        break
            except Exception:
                pass

        key = f"{exc_type}: {exc_msg[:100]}"
        if origin:
            key += f" @ {origin[:80]}"

        now = datetime.now().strftime("%H:%M:%S")
        tb_text = ""
        if tb:
            try:
                tb_text = "".join(traceback.format_tb(tb))
            except Exception:
                pass

        with self._error_lock:
            entry = self._errors[key]
            entry["count"] += 1
            if entry["first_seen"] is None:
                entry["first_seen"] = now
                entry["sample_tb"] = tb_text
            entry["last_seen"] = now
            self._total_errors += 1
            count = self._total_errors

        # Log on first occurrence, then every 100th
        if entry["count"] == 1:
            logger.error("Qt error [NEW]: %s\n  Context: %s", key, context)
            if tb_text:
                for line in tb_text.strip().splitlines()[-5:]:
                    logger.error("    %s", line)
        elif entry["count"] % 100 == 0:
            logger.warning("Qt error [x%d]: %s", entry["count"], key)

        # Detect recursion pattern (>10 of same error in rapid succession)
        if entry["count"] == 10:
            msg = f"RECURSION DETECTED: {key[:60]}... ({entry['count']}x)"
            logger.critical(msg)
            self.error_detected.emit(msg, count)

        # Update status bar
        self._update_status(count)

    # ── Memory watchdog ──

    def _check_memory(self):
        """Periodic check for runaway memory growth."""
        rss = self._get_rss_mb()
        if rss is None:
            return

        delta = rss - self._last_rss
        self._last_rss = rss

        # Alert if memory grew by >500 MB in 5 seconds
        if delta > 500:
            msg = f"Memory spike: +{delta:.0f} MB in 5s (now {rss:.0f} MB)"
            logger.critical(msg)
            self.memory_warning.emit(msg)

            # Dump current error state for correlation
            if self._total_errors > 0:
                logger.critical("Active Qt errors at time of spike:\n%s", self.get_error_summary())

    @staticmethod
    def _get_rss_mb():
        """Get process RSS in MB (Windows)."""
        try:
            import ctypes
            from ctypes import wintypes

            class PMC(ctypes.Structure):
                _fields_ = [
                    ("cb", wintypes.DWORD),
                    ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            pmc = PMC()
            pmc.cb = ctypes.sizeof(PMC)
            handle = ctypes.windll.kernel32.GetCurrentProcess()
            k32 = ctypes.windll.kernel32
            # K32GetProcessMemoryInfo is the modern Windows API
            fn = getattr(k32, "K32GetProcessMemoryInfo", None)
            if fn is None:
                fn = ctypes.windll.psapi.GetProcessMemoryInfo
            fn.argtypes = [ctypes.c_void_p, ctypes.POINTER(PMC), wintypes.DWORD]
            fn.restype = wintypes.BOOL
            if fn(handle, ctypes.byref(pmc), pmc.cb):
                return pmc.WorkingSetSize / (1024 * 1024)
        except Exception:
            pass
        return None

    # ── UI update ──

    def _update_status(self, count):
        """Update status bar label if attached."""
        if self._status_bar:
            try:
                self._status_bar.setText(f"Qt errors: {count}")
                self._status_bar.setStyleSheet("color: #DC2626; font-weight: 600;")
                self._status_bar.setToolTip("Click for details (see logs)")
            except (RuntimeError, AttributeError):
                pass  # Widget may be deleted
