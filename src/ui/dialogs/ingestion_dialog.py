"""
Alma Insights — Ingestion Dialog
Modal dialog showing preflight checklist, live log, and progress.
Used for both "Test Connection" and full API pull.
"""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTextEdit, QFrame, QProgressBar, QApplication, QDateEdit,
    QGroupBox, QMessageBox, QSizePolicy
)
from PySide6.QtCore import Qt, QDate, QThread, Signal, QObject
from PySide6.QtGui import QFont, QTextCursor
import sys

from src.ui.theme import *


# ═══════════════════════════════════
#  WORKER THREAD
# ═══════════════════════════════════

class _WorkerSignals(QObject):
    """Signals for the background worker thread."""
    progress = Signal(str, object)   # (message, percent_or_None)
    log_line = Signal(str)           # formatted log line
    finished = Signal(dict)          # result stats
    error = Signal(str)              # error message
    preflight_step = Signal(str, bool, str)  # (step_name, passed, detail)


class _IngestionWorker(QThread):
    """Runs ingestion or preflight in a background thread."""

    def __init__(self, mode, pat, chart_url, date_start, date_end,
                 db_path, trusted_base, is_test_mode):
        super().__init__()
        self.signals = _WorkerSignals()
        self.mode = mode  # "preflight" or "ingest"
        self.pat = pat
        self.chart_url = chart_url
        self.date_start = date_start
        self.date_end = date_end
        self.db_path = db_path  # Path, not the connection — thread-safe
        self.trusted_base = trusted_base
        self.is_test_mode = is_test_mode
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            from src.data.run_logger import RunLogger

            logger = RunLogger(
                dataset_name=self.chart_url[:40] if self.chart_url else "test",
                run_type=self.mode,
            )
            logger.log_event.connect(self.signals.log_line.emit)

            if self.is_test_mode:
                self._run_test_mode(logger)
            elif self.mode == "preflight":
                self._run_preflight(logger)
            elif self.mode == "estimate":
                self._run_estimate(logger)
            else:
                self._run_ingestion(logger)

        except Exception as e:
            import traceback
            self.signals.error.emit(f"{str(e)}\n\n{traceback.format_exc()}")

    def _make_db(self):
        """Create a thread-local DB connection."""
        from src.data.db_manager import DatabaseManager
        db = DatabaseManager(db_path=self.db_path)
        db.initialize()
        return db

    def _run_test_mode(self, logger):
        """Use mock client for test/demo mode."""
        if self.mode == "preflight":
            from src.data.lightdash_mock import run_mock_preflight
            results = run_mock_preflight(logger)
            for r in results:
                self.signals.preflight_step.emit(r.step, r.passed, r.detail)
            self.signals.finished.emit({
                "mode": "preflight",
                "passed": all(r.passed for r in results),
                "steps": len(results),
            })
        elif self.mode == "estimate":
            # Mock estimate — just report a fake row count
            import random, time
            time.sleep(0.5)
            est_rows = random.randint(300, 1500)
            est_chunks = max(1, est_rows // 90000 + 1)
            self.signals.progress.emit(f"Estimate: ~{est_rows:,} rows in {est_chunks} chunk(s)", 100)
            self.signals.finished.emit({
                "mode": "estimate",
                "dry_run": True,
                "estimated_rows": est_rows,
                "chunks": est_chunks,
            })
        else:
            from src.data.lightdash_mock import run_mock_ingestion
            db = self._make_db()
            try:
                stats = run_mock_ingestion(
                    db=db,
                    date_start=self.date_start,
                    date_end=self.date_end,
                    logger=logger,
                    progress_callback=lambda msg, pct: self.signals.progress.emit(msg, pct),
                )
                self.signals.finished.emit(stats)
            finally:
                db.close()

    def _run_preflight(self, logger):
        from src.data.lightdash_client import run_preflight
        results = run_preflight(
            self.pat, self.chart_url, self.trusted_base, logger
        )
        for r in results:
            self.signals.preflight_step.emit(r.step, r.passed, r.detail)
        self.signals.finished.emit({
            "mode": "preflight",
            "passed": all(r.passed for r in results),
            "steps": len(results),
        })

    def _run_estimate(self, logger):
        """Dry run — probe for row count only."""
        from src.data.lightdash_client import run_ingestion
        stats = run_ingestion(
            pat=self.pat,
            chart_url=self.chart_url,
            date_start=self.date_start,
            date_end=self.date_end,
            db=None,  # Not needed for dry run
            trusted_base=self.trusted_base,
            logger=logger,
            progress_callback=lambda msg, pct: self.signals.progress.emit(msg, pct),
            cancel_check=lambda: self._cancelled,
            dry_run=True,
        )
        stats["mode"] = "estimate"
        self.signals.finished.emit(stats)

    def _run_ingestion(self, logger):
        from src.data.lightdash_client import run_ingestion
        db = self._make_db()
        try:
            stats = run_ingestion(
                pat=self.pat,
                chart_url=self.chart_url,
                date_start=self.date_start,
                date_end=self.date_end,
                db=db,
                trusted_base=self.trusted_base,
                logger=logger,
                progress_callback=lambda msg, pct: self.signals.progress.emit(msg, pct),
                cancel_check=lambda: self._cancelled,
            )
            self.signals.finished.emit(stats)
        finally:
            db.close()


# ═══════════════════════════════════
#  INGESTION DIALOG
# ═══════════════════════════════════

class IngestionDialog(QDialog):
    """
    Modal dialog for Lightdash preflight + ingestion.
    Shows checklist, progress bar, and live scrolling log.
    """

    # Emitted when ingestion completes successfully
    ingestion_complete = Signal(dict)

    def __init__(self, db, pat="", chart_url="", trusted_base="",
                 is_test_mode=False, parent=None):
        super().__init__(parent)
        self.db = db
        self.pat = pat
        self.chart_url = chart_url
        self.trusted_base = trusted_base or "https://alma.lightdash.cloud"
        self.is_test_mode = is_test_mode
        self._worker = None

        self.setWindowTitle("Lightdash Data Pull" + (" — TEST MODE" if is_test_mode else ""))
        self.setMinimumSize(700, 620)
        self.resize(780, 680)
        self.setModal(True)

        try:
            self._build_ui()
        except Exception as e:
            import traceback
            # Fallback: show error in a simple layout
            from PySide6.QtWidgets import QVBoxLayout, QLabel
            layout = QVBoxLayout(self)
            err_label = QLabel(f"Dialog build failed:\n\n{traceback.format_exc()}")
            err_label.setWordWrap(True)
            layout.addWidget(err_label)

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        # ── Header ──
        header = QLabel("Lightdash Data Pull")
        header.setStyleSheet(f"font-size: 18px; font-weight: 700; color: {ALMA_TEXT_DARK};")
        layout.addWidget(header)

        if self.is_test_mode:
            test_badge = QLabel("🧪 TEST MODE — using mock data")
            test_badge.setStyleSheet(f"""
                background: {ALMA_WARNING}; color: white; border-radius: 4px;
                padding: 4px 10px; font-size: 11px; font-weight: 600;
            """)
            layout.addWidget(test_badge)

        # ── Date range ──
        date_group = QGroupBox("Date Range")
        date_group.setStyleSheet(f"""
            QGroupBox {{
                font-size: 12px; font-weight: 600; color: {ALMA_TEXT_MID};
                border: 1px solid {ALMA_BORDER_LIGHT}; border-radius: 8px;
                margin-top: 8px; padding-top: 18px;
            }}
            QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 6px; }}
        """)
        date_layout = QHBoxLayout(date_group)
        date_layout.setSpacing(12)

        self.date_from = QDateEdit()
        self.date_from.setCalendarPopup(True)
        self.date_from.setDate(QDate.currentDate().addDays(-7))
        self.date_from.setDisplayFormat("MMM d, yyyy")
        date_layout.addWidget(QLabel("From:"))
        date_layout.addWidget(self.date_from)

        self.date_to = QDateEdit()
        self.date_to.setCalendarPopup(True)
        self.date_to.setDate(QDate.currentDate())
        self.date_to.setDisplayFormat("MMM d, yyyy")
        date_layout.addWidget(QLabel("To:"))
        date_layout.addWidget(self.date_to)
        date_layout.addStretch()

        layout.addWidget(date_group)

        # Date range warning
        self.date_warning = QLabel("")
        self.date_warning.setStyleSheet(f"font-size: 11px; color: {ALMA_WARNING}; padding: 2px 4px;")
        self.date_warning.setWordWrap(True)
        self.date_warning.setVisible(False)
        layout.addWidget(self.date_warning)
        self.date_from.dateChanged.connect(self._check_date_range)
        self.date_to.dateChanged.connect(self._check_date_range)

        # ── Checklist ──
        self.checklist_frame = QFrame()
        self.checklist_frame.setStyleSheet(f"""
            QFrame {{
                background: {ALMA_WHITE}; border: 1px solid {ALMA_BORDER_LIGHT};
                border-radius: 8px;
            }}
        """)
        self.checklist_frame.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.checklist_layout = QVBoxLayout(self.checklist_frame)
        self.checklist_layout.setContentsMargins(16, 12, 16, 12)
        self.checklist_layout.setSpacing(8)

        checklist_title = QLabel("Preflight Checklist")
        checklist_title.setStyleSheet(f"font-size: 12px; font-weight: 700; color: {ALMA_TEXT_MID}; border: none;")
        self.checklist_layout.addWidget(checklist_title)

        self._checklist_items = {}
        for step in ["Token present", "URL valid", "Trusted host", "Server reachable", "Chart access"]:
            row = QHBoxLayout()
            icon = QLabel("○")
            icon.setFixedWidth(20)
            icon.setStyleSheet(f"color: {ALMA_TEXT_LIGHT}; font-size: 14px; border: none;")
            label = QLabel(step)
            label.setFixedWidth(120)
            label.setStyleSheet(f"font-size: 12px; color: {ALMA_TEXT_MID}; border: none;")
            detail = QLabel("")
            detail.setWordWrap(True)
            detail.setStyleSheet(f"font-size: 11px; color: {ALMA_TEXT_LIGHT}; border: none;")
            row.addWidget(icon)
            row.addWidget(label)
            row.addWidget(detail, 1)
            self.checklist_layout.addLayout(row)
            self._checklist_items[step] = (icon, label, detail)

        layout.addWidget(self.checklist_frame)

        # ── Progress bar ──
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setStyleSheet(f"""
            QProgressBar {{
                border: 1px solid {ALMA_BORDER}; border-radius: 6px;
                text-align: center; height: 22px; font-size: 11px;
                background: {ALMA_WHITE};
            }}
            QProgressBar::chunk {{
                background: {ALMA_GREEN_LIGHT}; border-radius: 5px;
            }}
        """)
        layout.addWidget(self.progress_bar)

        self.progress_label = QLabel("Ready")
        self.progress_label.setStyleSheet(f"font-size: 11px; color: {ALMA_TEXT_LIGHT};")
        layout.addWidget(self.progress_label)

        # ── Live log ──
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setFont(QFont("Consolas", 10) if sys.platform == "win32" else QFont("Menlo", 10))
        self.log_view.setStyleSheet(f"""
            QTextEdit {{
                background: #1E1E1E; color: #D4D4D4; border: 1px solid {ALMA_BORDER};
                border-radius: 6px; padding: 8px;
            }}
        """)
        self.log_view.setMinimumHeight(60)
        self.log_view.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout.addWidget(self.log_view, 1)

        # ── Buttons ──
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        self.test_btn = QPushButton("Test Connection")
        self.test_btn.setStyleSheet(f"""
            QPushButton {{
                background: {ALMA_INFO}; color: white; border: none;
                border-radius: 6px; padding: 8px 20px; font-weight: 600;
            }}
            QPushButton:hover {{ background: #1A5F8F; }}
            QPushButton:disabled {{ background: {ALMA_BORDER}; color: {ALMA_TEXT_LIGHT}; }}
        """)
        self.test_btn.setCursor(Qt.PointingHandCursor)
        self.test_btn.clicked.connect(self._run_preflight)
        btn_row.addWidget(self.test_btn)

        self.estimate_btn = QPushButton("Estimate")
        self.estimate_btn.setToolTip("Dry run — probe the API for row count without pulling data")
        self.estimate_btn.setStyleSheet(f"""
            QPushButton {{
                background: {ALMA_WARNING}; color: white; border: none;
                border-radius: 6px; padding: 8px 20px; font-weight: 600;
            }}
            QPushButton:hover {{ background: #C07B00; }}
            QPushButton:disabled {{ background: {ALMA_BORDER}; color: {ALMA_TEXT_LIGHT}; }}
        """)
        self.estimate_btn.setCursor(Qt.PointingHandCursor)
        self.estimate_btn.clicked.connect(self._run_estimate)
        btn_row.addWidget(self.estimate_btn)

        self.pull_btn = QPushButton("Pull Data")
        self.pull_btn.setStyleSheet(f"""
            QPushButton {{
                background: {ALMA_GREEN_LIGHT}; color: {ALMA_TEXT_ON_DARK}; border: none;
                border-radius: 6px; padding: 8px 20px; font-weight: 600;
            }}
            QPushButton:hover {{ background: {ALMA_GREEN_SUBTLE}; }}
            QPushButton:disabled {{ background: {ALMA_BORDER}; color: {ALMA_TEXT_LIGHT}; }}
        """)
        self.pull_btn.setCursor(Qt.PointingHandCursor)
        self.pull_btn.clicked.connect(self._run_ingestion)
        btn_row.addWidget(self.pull_btn)

        btn_row.addStretch()

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {ALMA_TEXT_MID}; border: 1px solid {ALMA_BORDER};
                border-radius: 6px; padding: 8px 20px;
            }}
            QPushButton:hover {{ background: {ALMA_HOVER_LIGHT}; }}
        """)
        self.cancel_btn.setCursor(Qt.PointingHandCursor)
        self.cancel_btn.clicked.connect(self._on_cancel)
        btn_row.addWidget(self.cancel_btn)

        self.close_btn = QPushButton("Close")
        self.close_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {ALMA_TEXT_MID}; border: 1px solid {ALMA_BORDER};
                border-radius: 6px; padding: 8px 20px;
            }}
            QPushButton:hover {{ background: {ALMA_HOVER_LIGHT}; }}
        """)
        self.close_btn.setCursor(Qt.PointingHandCursor)
        self.close_btn.clicked.connect(self.accept)
        self.close_btn.setVisible(False)
        btn_row.addWidget(self.close_btn)

        layout.addLayout(btn_row)

    # ═══════════════════════════════════
    #  ACTIONS
    # ═══════════════════════════════════

    def _run_preflight(self):
        self._reset_checklist()
        self._set_running(True)
        self.log_view.clear()
        self._append_log("Starting preflight checks...")

        self._worker = _IngestionWorker(
            mode="preflight",
            pat=self.pat, chart_url=self.chart_url,
            date_start="", date_end="",
            db_path=self.db.db_path, trusted_base=self.trusted_base,
            is_test_mode=self.is_test_mode,
        )
        self._connect_worker()
        self._worker.start()

    def _run_estimate(self):
        """Dry run — probe the API for estimated row count without pulling data."""
        self._reset_checklist()
        self._set_running(True)
        self.log_view.clear()
        self._append_log("Running dry-run estimate...")

        ds = self.date_from.date().toString("yyyy-MM-dd")
        de = self.date_to.date().toString("yyyy-MM-dd")

        self._worker = _IngestionWorker(
            mode="estimate",
            pat=self.pat, chart_url=self.chart_url,
            date_start=ds, date_end=de,
            db_path=self.db.db_path, trusted_base=self.trusted_base,
            is_test_mode=self.is_test_mode,
        )
        self._connect_worker()
        self._worker.start()

    def _run_ingestion(self):
        ds = self.date_from.date().toString("yyyy-MM-dd")
        de = self.date_to.date().toString("yyyy-MM-dd")

        # ── Guardrail: warn on large date ranges ──
        days = self.date_from.date().daysTo(self.date_to.date())
        if days > 30:
            reply = QMessageBox.warning(
                self, "Large Date Range",
                f"You're about to pull {days} days of data. This may be expensive "
                f"and could take a while.\n\n"
                f"Consider running Estimate first, or narrowing the date range.\n\n"
                f"Proceed anyway?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return

        self._reset_checklist()
        self._set_running(True)
        self.log_view.clear()
        self._append_log("Starting data pull...")

        self._worker = _IngestionWorker(
            mode="ingest",
            pat=self.pat, chart_url=self.chart_url,
            date_start=ds, date_end=de,
            db_path=self.db.db_path, trusted_base=self.trusted_base,
            is_test_mode=self.is_test_mode,
        )
        self._connect_worker()
        self._worker.start()

    def _check_date_range(self):
        """Show warning if date range exceeds 30 days."""
        days = self.date_from.date().daysTo(self.date_to.date())
        if days > 30:
            self.date_warning.setText(
                f"⚠ {days}-day range — this may be an expensive pull. "
                f"Consider using Estimate first."
            )
            self.date_warning.setVisible(True)
        else:
            self.date_warning.setVisible(False)

    def _connect_worker(self):
        w = self._worker.signals
        w.progress.connect(self._on_progress)
        w.log_line.connect(self._append_log)
        w.finished.connect(self._on_finished)
        w.error.connect(self._on_error)
        w.preflight_step.connect(self._on_preflight_step)

    def _on_cancel(self):
        if self._worker and self._worker.isRunning():
            self._worker.cancel()
            self._append_log("⚠ Cancellation requested...")
        else:
            self.reject()

    # ═══════════════════════════════════
    #  SIGNAL HANDLERS
    # ═══════════════════════════════════

    def _on_progress(self, msg, pct):
        self.progress_label.setText(msg)
        if pct is not None:
            self.progress_bar.setValue(int(pct))

    def _append_log(self, text):
        self.log_view.append(text)
        # Auto-scroll to bottom
        cursor = self.log_view.textCursor()
        cursor.movePosition(QTextCursor.End)
        self.log_view.setTextCursor(cursor)

    def _on_preflight_step(self, step_name, passed, detail):
        if step_name in self._checklist_items:
            icon_lbl, name_lbl, detail_lbl = self._checklist_items[step_name]
            if passed:
                icon_lbl.setText("✓")
                icon_lbl.setStyleSheet(f"color: {ALMA_SUCCESS}; font-size: 14px; font-weight: 700; border: none;")
                detail_lbl.setText(detail)
                detail_lbl.setStyleSheet(f"font-size: 11px; color: {ALMA_SUCCESS}; border: none;")
            else:
                icon_lbl.setText("✗")
                icon_lbl.setStyleSheet(f"color: {ALMA_ERROR}; font-size: 14px; font-weight: 700; border: none;")
                detail_lbl.setText(detail)
                detail_lbl.setStyleSheet(f"font-size: 11px; color: {ALMA_ERROR}; border: none;")
        QApplication.processEvents()

    def _on_finished(self, stats):
        self._set_running(False)

        mode = stats.get("mode", "")

        if mode == "preflight":
            passed = stats.get("passed", False)
            if passed:
                self.progress_label.setText("✓ All preflight checks passed")
                self.progress_bar.setValue(100)
                self._append_log("\n✓ PREFLIGHT PASSED — ready to pull data")
            else:
                self.progress_label.setText("✗ Preflight failed — check details above")
                self._append_log("\n✗ PREFLIGHT FAILED")

        elif mode == "estimate":
            est = stats.get("estimated_rows", 0)
            chunks = stats.get("chunks", 0)
            self.progress_label.setText(
                f"Estimate: ~{est:,} rows in {chunks} chunk(s)"
            )
            self.progress_bar.setValue(100)
            self._append_log(
                f"\n📊 ESTIMATE: ~{est:,} rows across {chunks} chunk(s)\n"
                f"   Use 'Pull Data' to start the actual ingestion."
            )

        else:
            convos = stats.get("conversations", 0)
            self.progress_label.setText(f"✓ Done — {convos:,} conversations loaded")
            self.progress_bar.setValue(100)
            self._append_log(f"\n✓ INGESTION COMPLETE: {convos:,} conversations")
            self.ingestion_complete.emit(stats)

    def _on_error(self, msg):
        self._set_running(False)
        self.progress_label.setText(f"✗ Error: {msg[:80]}")
        self._append_log(f"\n✗ ERROR: {msg}")
        QMessageBox.critical(self, "Ingestion Error", msg)

    # ═══════════════════════════════════
    #  HELPERS
    # ═══════════════════════════════════

    def _set_running(self, running):
        self.test_btn.setEnabled(not running)
        self.estimate_btn.setEnabled(not running)
        self.pull_btn.setEnabled(not running)
        self.cancel_btn.setVisible(running)
        self.close_btn.setVisible(not running)
        self.date_from.setEnabled(not running)
        self.date_to.setEnabled(not running)

    def _reset_checklist(self):
        for step_name, (icon_lbl, name_lbl, detail_lbl) in self._checklist_items.items():
            icon_lbl.setText("○")
            icon_lbl.setStyleSheet(f"color: {ALMA_TEXT_LIGHT}; font-size: 14px; border: none;")
            detail_lbl.setText("")
            detail_lbl.setStyleSheet(f"font-size: 11px; color: {ALMA_TEXT_LIGHT}; border: none;")
