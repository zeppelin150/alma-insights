"""
Alma Insights — Splash "Install & Restart" widget.

Self-contained Qt widget that the splash window mounts only when
:func:`src.startup.checks.updates.get_pending_update` returns a
non-None payload. Drives the install flow end-to-end:

    [Install & Restart]   ← button (initially)
    └→ resolve manifest (manifest_fetcher.resolve_release_artifact)
    └→ Updater.stage(url, sha256, version)
       └→ progress(pct, msg)        → update progress bar
       └→ complete()                → restart_app()
       └→ failed(msg)               → restore button, show error

Why this is its own module
--------------------------

* Keeps :file:`splash_window.py` focused on "render checks + footer".
* Single-purpose: only knows how to install + restart. The splash
  doesn't need to import the manifest fetcher, the updater, or the
  restart helper.
* Trivially testable in isolation.

Public API
----------

* :class:`UpdateActionWidget` — the widget. Mount it; it does the rest.
* :meth:`UpdateActionWidget.is_busy` — splash queries this so it can
  block the Continue / Close buttons during a download.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from src.ui.theme import (
    ALMA_BORDER,
    ALMA_CREAM,
    ALMA_ERROR,
    ALMA_SUCCESS,
    ALMA_TEXT_ON_DARK,
)

logger = logging.getLogger("alma.updater")


class UpdateActionWidget(QWidget):
    """Splash-embedded "Install & Restart" UI for an available update.

    Emits :attr:`busy_changed` so the splash can disable Continue / X
    while a stage is in-flight (no halfway-aborted updates).
    """

    busy_changed = Signal(bool)

    def __init__(self, payload: dict, parent: QWidget | None = None) -> None:
        """``payload`` matches :func:`get_pending_update`'s return shape."""
        super().__init__(parent)
        self._payload = payload
        self._updater = None
        self._busy = False
        self._build_ui()

    # ── public ────────────────────────────────────────────────────

    def is_busy(self) -> bool:
        """True while a download/stage is in progress."""
        return self._busy

    # ── UI construction ───────────────────────────────────────────

    def _build_ui(self) -> None:
        # Always set self as parent on QWidget children — without it,
        # the children are briefly parentless between construction and
        # layout-add, which intermittently crashes on Windows under
        # tests when the widgets get garbage collected before the
        # layout re-parents them.
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(6)

        new_ver = self._payload.get("new_version", "?")
        self._title = QLabel(
            f"Update available: v{new_ver}. "
            "Click below to install and relaunch automatically.",
            self,
        )
        self._title.setAlignment(Qt.AlignCenter)
        self._title.setWordWrap(True)
        self._title.setStyleSheet(
            f"color: {ALMA_TEXT_ON_DARK}; font-size: 12px;"
        )
        layout.addWidget(self._title)

        # Action button — initially shows "Install & Restart"; turns
        # into a disabled label-style status while downloading.
        button_row = QHBoxLayout()
        button_row.addStretch()
        self._action_btn = QPushButton(f"Install v{new_ver} & Restart", self)
        self._action_btn.setCursor(Qt.PointingHandCursor)
        self._action_btn.setStyleSheet(_button_style())
        self._action_btn.clicked.connect(self._on_install_clicked)
        button_row.addWidget(self._action_btn)
        button_row.addStretch()
        layout.addLayout(button_row)

        # Progress bar (hidden until install starts).
        self._progress = QProgressBar(self)
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setVisible(False)
        self._progress.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._progress.setStyleSheet(_progress_style())
        layout.addWidget(self._progress)

        self._status = QLabel("", self)
        self._status.setAlignment(Qt.AlignCenter)
        self._status.setStyleSheet(
            f"color: {ALMA_TEXT_ON_DARK}; opacity: 0.7; font-size: 11px;"
        )
        self._status.setVisible(False)
        layout.addWidget(self._status)

    # ── action: install + restart ─────────────────────────────────

    def _on_install_clicked(self) -> None:
        self._set_busy(True)
        self._action_btn.setEnabled(False)
        self._progress.setVisible(True)
        self._status.setVisible(True)
        self._status.setText("Resolving release manifest…")

        try:
            url, sha = self._resolve_artifact()
        except Exception as exc:  # noqa: BLE001 — user-readable surface
            self._show_error(str(exc))
            return

        self._start_stage(url, sha)

    def _resolve_artifact(self) -> tuple[str, str]:
        """Look up the platform zip + SHA from the cached payload.

        Pulled out so tests can patch the manifest fetcher cleanly.
        """
        from src.updater.manifest_fetcher import resolve_release_artifact
        url, sha, _ = resolve_release_artifact(
            self._payload.get("assets") or [],
            token=self._payload.get("token", ""),
        )
        return url, sha

    def _start_stage(self, url: str, sha: str) -> None:
        from src.updater.updater import Updater
        self._updater = Updater(parent=self)
        self._updater.progress.connect(self._on_progress)
        self._updater.complete.connect(self._on_complete)
        self._updater.failed.connect(self._on_failed)
        self._status.setText("Starting download…")
        new_ver = self._payload.get("new_version", "")
        # The token is REQUIRED for a private repo — the asset 404s without it.
        # (The manifest resolve above already uses it; omitting it here made the
        # download fail with a bare 404 *after* a successful manifest fetch.)
        self._updater.stage(url, expected_sha256=sha, new_version=new_ver,
                            token=self._payload.get("token", ""))

    # ── updater signal handlers ───────────────────────────────────

    def _on_progress(self, pct: int, msg: str) -> None:
        self._progress.setValue(pct)
        self._status.setText(msg)

    def _on_complete(self) -> None:
        self._progress.setValue(100)
        self._status.setText("Update staged. Restarting…")
        self._status.setStyleSheet(
            f"color: {ALMA_SUCCESS}; font-size: 11px; font-weight: 600;"
        )
        # Defer the actual restart so the user can read the success
        # message (and so any pending Qt events flush). 800ms is short
        # enough that nobody clicks anything in between.
        from PySide6.QtCore import QTimer
        QTimer.singleShot(800, self._do_restart)

    def _do_restart(self) -> None:
        # Defence against a stale QTimer firing on a torn-down widget
        # (e.g. when a test exits while the 800ms timer set by
        # _on_complete is still pending). Without this guard, the timer
        # would call restart_app for real after the test's
        # `with patch(...)` block exits — spawning a child process that
        # re-runs the test → death loop.
        try:
            if not self.isVisible():
                logger.info(
                    "Restart skipped: widget no longer visible "
                    "(probably torn down by tests)."
                )
                return
        except RuntimeError:
            # The underlying QObject is already gone (Qt
            # WA_DeleteOnClose fired). Definitely skip.
            return

        from src.updater.restart import restart_app
        try:
            restart_app()
        except Exception as exc:  # noqa: BLE001
            self._show_error(f"Restart failed: {exc}. Please relaunch manually.")

    def _on_failed(self, msg: str) -> None:
        self._show_error(f"Update failed: {msg}")

    # ── error display ─────────────────────────────────────────────

    def _show_error(self, message: str) -> None:
        self._set_busy(False)
        self._progress.setVisible(False)
        self._status.setText(message)
        self._status.setStyleSheet(
            f"color: {ALMA_ERROR}; font-size: 11px; font-weight: 600;"
        )
        self._status.setVisible(True)
        self._action_btn.setEnabled(True)
        self._action_btn.setText("Try again")

    def _set_busy(self, busy: bool) -> None:
        if busy != self._busy:
            self._busy = busy
            self.busy_changed.emit(busy)


# ── Styles (kept at module level so tests can validate them) ─────


def _button_style() -> str:
    """Solid Alma-green button styled for the dark splash background."""
    return (
        f"QPushButton {{ color: {ALMA_CREAM}; background: {ALMA_SUCCESS}; "
        "border: none; border-radius: 4px; padding: 10px 24px; "
        "font-weight: 600; font-size: 12px; }} "
        "QPushButton:hover:enabled { background: #1a8549; } "
        "QPushButton:disabled { background: #3a5a4a; color: #7a8a80; }"
    )


def _progress_style() -> str:
    """Slim progress bar that reads on the dark splash background."""
    return (
        "QProgressBar { background: rgba(255,255,255,0.08); "
        "border: none; border-radius: 4px; height: 6px; text-align: center; "
        "color: transparent; } "
        f"QProgressBar::chunk {{ background: {ALMA_SUCCESS}; "
        "border-radius: 4px; }}"
    )
