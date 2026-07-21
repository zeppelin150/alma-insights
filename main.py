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

# ── EAGER: air-gap env vars must be set BEFORE any HF/torch import ──
# The HF stack reads HF_HUB_OFFLINE / TRANSFORMERS_OFFLINE etc. exactly
# once at import time; setting them after PySide6/sentence_transformers
# have loaded is a silent no-op. Keep this block above every other import.
from src.startup.env_guard import enforce as _enforce_env
_enforce_env()

# ── Global Python crash handler (before QApplication so background-thread
#    exceptions outside Qt slots get recorded too) ──
from src.core.crash_handler import install as _install_crash_handler
_install_crash_handler()

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QIcon

# ── QtWebEngine init order (must precede QApplication) ──
# Chromium requires AA_ShareOpenGLContexts before the app object exists, and
# PySide6 warns (and on some platforms misrenders) when QtWebEngineWidgets is
# first imported after QApplication. The web surfaces (Agent chat, enablement
# web tabs) import lazily later; this eager import + attribute make that safe.
QApplication.setAttribute(Qt.AA_ShareOpenGLContexts, True)
try:
    from PySide6 import QtWebEngineWidgets as _qtwebengine  # noqa: F401
except ImportError:  # WebEngine not installed (Essentials-only env) — the
    pass             # Agent page already degrades to its placeholder.

from src.ui.theme import get_stylesheet
from src.ui.main_window import MainWindow


def main():
    # ── Memory profiler (opt-in: python main.py --profile) ──
    if "--profile" in sys.argv:
        sys.argv.remove("--profile")
        from src.data.memory_profiler import MemoryProfiler
        MemoryProfiler.start()

    # ── App mode override (python main.py --mode enablement) ──
    mode_override = None
    if "--mode" in sys.argv:
        i = sys.argv.index("--mode")
        if i + 1 < len(sys.argv):
            mode_override = sys.argv[i + 1]
            del sys.argv[i:i + 2]
        else:
            sys.argv.remove("--mode")
    for arg in list(sys.argv):
        if arg.startswith("--mode="):
            mode_override = arg.split("=", 1)[1]
            sys.argv.remove(arg)
    if mode_override:
        from src.ui import app_modes
        app_modes.set_cli_override(mode_override)

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

    # Rollback guard: if the newly applied update crash-looped (≥3 crashes
    # within its grace window) the previous version is restored here.
    from src.updater.rollback import needs_rollback, perform_rollback
    should_rollback, reason = needs_rollback()
    if should_rollback:
        ok, message = perform_rollback()
        print(f"[startup] Auto-rollback ({reason}): {message}")
        if ok:
            importlib.invalidate_caches()

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

    # ── First-run EULA (before the splash, after QApplication exists) ──
    # Uses the native QDialog so modality works correctly on both platforms.
    # If the user declines, we exit with status 1.
    from src.ui.dialogs.eula_dialog import ensure_accepted as ensure_eula_accepted
    if not ensure_eula_accepted():
        print("[startup] EULA declined; exiting.")
        sys.exit(1)

    # ── Startup splash with 10 health checks ──
    # --no-splash skips the UI (dev convenience: straight to MainWindow)
    if "--no-splash" in sys.argv:
        sys.argv.remove("--no-splash")
        ok = _run_checks_headless(app.applicationVersion())
        if not ok:
            print("[startup] Critical check failed; aborting (see output above).")
            sys.exit(1)
        failed_check_ids: list[str] = []
    else:
        accepted, failed_check_ids = _run_splash(app)
        if not accepted:
            # User closed splash without Continue, or a critical check blocked it
            sys.exit(1)

    # Launch main window
    window = MainWindow()
    window.show()

    # If the Gemini OAuth startup check failed (CLI missing, timed out, or
    # reports unauthenticated) surface a non-blocking notice pointing at
    # Settings → AI Provider. The app always lands on Home — never
    # auto-navigate away from it.
    # Only relevant when Gemini is actually the active provider. In enablement
    # mode on Claude, Gemini is never used — warning about its sign-in is noise.
    _show_gemini_notice = "gemini_oauth" in failed_check_ids
    if _show_gemini_notice:
        try:
            from src.data.settings_manager import get_section
            from src.ui import app_modes
            # The load-bearing signal: is Gemini actually the ACTIVE LLM? If the
            # active model is Claude, Gemini is never called and the sign-in notice
            # is pure noise — suppress it regardless of mode (this runs once at
            # startup, before the user may switch to enablement, so keying only off
            # the mode missed the common "active model = Claude" case).
            provider_is_gemini = True
            try:
                from src.llm.model_registry import ModelRegistry
                active = ModelRegistry.instance().active()
                provider_is_gemini = bool(active) and active.provider == "gemini"
            except Exception:  # noqa: BLE001 — registry unreadable → keep the notice
                pass
            enablement_on_claude = (
                app_modes.current_mode() == app_modes.MODE_ENABLEMENT
                and str((get_section("enablement", {}) or {}).get("provider", "")).lower() == "claude"
            )
            if (not provider_is_gemini) or enablement_on_claude:
                _show_gemini_notice = False
        except Exception:  # noqa: BLE001 — default to showing the notice
            pass
    if _show_gemini_notice:
        try:
            window._toasts.show_toast(
                "Gemini sign-in needs attention — open Settings to re-authenticate.",
                duration_ms=8000, toast_type="warning")
            window.status_label.setText(
                "Gemini sign-in needs attention — see Settings")
        except Exception as exc:  # noqa: BLE001 — the notice is non-fatal
            print(f"[startup] Could not surface OAuth notice: {exc}")

    # Wire guard to status bar if available
    if hasattr(window, 'qt_error_label'):
        guard.attach_to_status_bar(window.qt_error_label)

    # Once MainWindow is up, schedule a rollback-state cleanup after the
    # grace window expires. If the app hasn't crashed by then, the update
    # is considered stable and the _*_previous directories are removed.
    from PySide6.QtCore import QTimer
    from src.updater.rollback import clear_state_if_stable
    QTimer.singleShot(65_000, lambda: clear_state_if_stable())

    sys.exit(app.exec())


def _run_splash(app) -> tuple[bool, list[str]]:
    """Show the splash, run all checks, and return (accepted, failed_check_ids).

    `accepted` is True if the user clicked Continue. `failed_check_ids` is
    the list of check IDs that did not pass — used by main() to route the
    user to the appropriate Settings page (e.g. AI Provider when
    gemini_oauth failed)."""
    from src.startup.checker import Checker
    from src.startup.checks import DEFAULT_CHECKS
    from src.startup.splash_window import SplashWindow
    from PySide6.QtWidgets import QDialog

    splash = SplashWindow(app_version=app.applicationVersion())
    splash.show()
    app.processEvents()  # force first paint before any check runs

    checker = Checker(DEFAULT_CHECKS)
    splash.run(checker)

    accepted = splash.exec() == QDialog.Accepted
    return accepted, checker.failed_check_ids


def _run_checks_headless(app_version: str) -> bool:
    """Run checks synchronously; print results; return passed_critical."""
    from src.startup.checker import Checker
    from src.startup.checks import DEFAULT_CHECKS

    checker = Checker(DEFAULT_CHECKS)
    for result in checker.run_all():
        icon = {"pass": "OK", "warn": "!!", "fail": "XX"}[result.status]
        print(f"[{icon}] {result.name}: {result.message}")
    return checker.passed_critical


if __name__ == "__main__":
    main()
