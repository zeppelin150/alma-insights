"""Shared maintenance panel — memory diagnostics + the database danger zone.

Extracted verbatim from the product Settings page's Display tab
(settings_page.py, 2026-07-22) so both modes can host the same tools:
the product page keeps them on its Display tab, the enablement Settings
page gets a Maintenance tab. Same widget, same behavior, no drift.

The full-database reset reaches the hosting window via ``self.window()``
(MainWindow in both modes) and stays behind a typed-"DELETE" confirm.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from src.ui.theme import (
    ALMA_BG_ELEVATED,
    ALMA_BORDER,
    ALMA_BORDER_LIGHT,
    ALMA_ERROR,
    ALMA_GREEN_DARK,
    ALMA_GREEN_MID,
    ALMA_HOVER_LIGHT,
    ALMA_TEXT_DARK,
    ALMA_TEXT_LIGHT,
    ALMA_TEXT_MID,
    ALMA_WHITE,
    apply_card_shadow,
)


class MaintenancePanel(QWidget):
    """Memory profiler (snapshot / force-GC) + full database reset."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build()

    # ── layout ──────────────────────────────────────────────────────

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        lay.addWidget(self._section_label("MEMORY DIAGNOSTICS"))
        lay.addSpacing(8)
        lay.addWidget(self._build_memory_card())
        lay.addSpacing(24)

        lay.addWidget(self._section_label("DATA MANAGEMENT"))
        lay.addSpacing(8)
        lay.addWidget(self._build_reset_card())
        lay.addSpacing(24)

        lay.addWidget(self._section_label("DOCUMENTS"))
        lay.addSpacing(8)
        lay.addWidget(self._build_docs_card())

    def _build_memory_card(self) -> QFrame:
        card = self._card()
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(20, 18, 20, 18)
        card_layout.setSpacing(10)

        title_row = QHBoxLayout()
        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        title = QLabel("Memory Profiler")
        title.setStyleSheet(f"font-size: 14px; font-weight: 600; color: {ALMA_TEXT_DARK};")
        desc = QLabel(
            "Take a memory snapshot to diagnose allocation bloat. "
            "Shows object counts, large containers, conversation dict copies, "
            "and QThread worker lifecycle."
        )
        desc.setStyleSheet(f"font-size: 12px; color: {ALMA_TEXT_LIGHT};")
        desc.setWordWrap(True)
        title_col.addWidget(title)
        title_col.addWidget(desc)
        title_row.addLayout(title_col, 1)

        self._mem_snapshot_btn = QPushButton("Take Snapshot")
        self._mem_snapshot_btn.setCursor(Qt.PointingHandCursor)
        self._mem_snapshot_btn.setFixedHeight(32)
        self._mem_snapshot_btn.setStyleSheet(f"""
            QPushButton {{
                background: {ALMA_GREEN_DARK}; color: {ALMA_WHITE};
                border: none; border-radius: 8px;
                padding: 6px 18px; font-size: 12px; font-weight: 600;
            }}
            QPushButton:hover {{ background: {ALMA_GREEN_MID}; }}
        """)
        self._mem_snapshot_btn.clicked.connect(self._on_memory_snapshot)
        title_row.addWidget(self._mem_snapshot_btn)

        self._mem_gc_btn = QPushButton("Force GC")
        self._mem_gc_btn.setCursor(Qt.PointingHandCursor)
        self._mem_gc_btn.setFixedHeight(32)
        self._mem_gc_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {ALMA_TEXT_MID};
                border: 1px solid {ALMA_BORDER}; border-radius: 8px;
                padding: 6px 14px; font-size: 12px; font-weight: 500;
            }}
            QPushButton:hover {{ background: {ALMA_HOVER_LIGHT}; }}
        """)
        self._mem_gc_btn.clicked.connect(self._on_force_gc)
        title_row.addWidget(self._mem_gc_btn)

        card_layout.addLayout(title_row)

        self._mem_quick_stats = QLabel("No snapshot taken yet")
        self._mem_quick_stats.setStyleSheet(
            f"font-size: 12px; color: {ALMA_TEXT_MID}; padding: 4px 0;"
        )
        card_layout.addWidget(self._mem_quick_stats)

        self._mem_report_area = QTextEdit()
        self._mem_report_area.setReadOnly(True)
        self._mem_report_area.setVisible(False)
        self._mem_report_area.setMinimumHeight(300)
        self._mem_report_area.setMaximumHeight(500)
        self._mem_report_area.setStyleSheet(f"""
            QTextEdit {{
                background: {ALMA_WHITE}; color: {ALMA_TEXT_DARK};
                border: 1px solid {ALMA_BORDER_LIGHT}; border-radius: 8px;
                padding: 12px; font-family: 'Consolas', 'Courier New', monospace;
                font-size: 11px; line-height: 1.4;
            }}
        """)
        card_layout.addWidget(self._mem_report_area)

        return card

    def _build_reset_card(self) -> QFrame:
        """Full Database Reset — the danger zone."""
        card = self._card()
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(20, 18, 20, 18)
        card_layout.setSpacing(10)

        title_row = QHBoxLayout()
        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        title = QLabel("Full Database Reset")
        title.setStyleSheet(f"font-size: 14px; font-weight: 700; color: {ALMA_ERROR};")
        desc = QLabel(
            "Permanently delete ALL data — tickets, conversations, NLP results, "
            "reports, and enrichments. This cannot be undone."
        )
        desc.setStyleSheet(f"font-size: 12px; color: {ALMA_TEXT_LIGHT};")
        desc.setWordWrap(True)
        title_col.addWidget(title)
        title_col.addWidget(desc)
        title_row.addLayout(title_col, 1)

        reset_btn = QPushButton("Full Database Reset")
        reset_btn.setCursor(Qt.PointingHandCursor)
        reset_btn.setFixedHeight(32)
        reset_btn.setStyleSheet(f"""
            QPushButton {{
                background: {ALMA_ERROR}; color: {ALMA_WHITE};
                border: none; border-radius: 8px;
                padding: 6px 18px; font-size: 12px; font-weight: 600;
            }}
            QPushButton:hover {{ background: #B71C1C; }}
        """)
        reset_btn.setToolTip("Permanently delete ALL data and start fresh. This cannot be undone.")
        reset_btn.clicked.connect(self._full_database_reset)
        title_row.addWidget(reset_btn)

        card_layout.addLayout(title_row)
        return card

    def _build_docs_card(self) -> QFrame:
        """Managed documents tree — open it, or backfill it from app content."""
        card = self._card()
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(20, 18, 20, 18)
        card_layout.setSpacing(10)

        title_row = QHBoxLayout()
        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        title = QLabel("Documents Folder")
        title.setStyleSheet(f"font-size: 14px; font-weight: 600; color: {ALMA_TEXT_DARK};")
        desc = QLabel(
            "One organized home for app files — Downloads, Exports, Zendesk "
            "Imports, Zendesk Edits, and Worksheets. Backfill re-creates the "
            "folders and exports existing decks and Zendesk revision drafts "
            "into them. Safe to run any time."
        )
        desc.setStyleSheet(f"font-size: 12px; color: {ALMA_TEXT_LIGHT};")
        desc.setWordWrap(True)
        title_col.addWidget(title)
        title_col.addWidget(desc)
        title_row.addLayout(title_col, 1)

        open_btn = QPushButton("Open Folder")
        open_btn.setCursor(Qt.PointingHandCursor)
        open_btn.setFixedHeight(32)
        open_btn.setStyleSheet(f"""
            QPushButton {{
                background: {ALMA_WHITE}; color: {ALMA_TEXT_MID};
                border: 1px solid {ALMA_BORDER}; border-radius: 8px;
                padding: 6px 18px; font-size: 12px; font-weight: 600;
            }}
            QPushButton:hover {{ border-color: {ALMA_GREEN_MID}; color: {ALMA_GREEN_DARK}; }}
        """)
        open_btn.clicked.connect(self._on_open_docs_folder)
        title_row.addWidget(open_btn)

        self._docs_backfill_btn = QPushButton("Run Backfill")
        self._docs_backfill_btn.setCursor(Qt.PointingHandCursor)
        self._docs_backfill_btn.setFixedHeight(32)
        self._docs_backfill_btn.setStyleSheet(f"""
            QPushButton {{
                background: {ALMA_GREEN_DARK}; color: {ALMA_WHITE};
                border: none; border-radius: 8px;
                padding: 6px 18px; font-size: 12px; font-weight: 600;
            }}
            QPushButton:hover {{ background: {ALMA_GREEN_MID}; }}
        """)
        self._docs_backfill_btn.clicked.connect(self._on_docs_backfill)
        title_row.addWidget(self._docs_backfill_btn)

        card_layout.addLayout(title_row)

        self._docs_status = QLabel("")
        self._docs_status.setStyleSheet(f"font-size: 12px; color: {ALMA_TEXT_MID};")
        self._docs_status.setWordWrap(True)
        card_layout.addWidget(self._docs_status)
        return card

    # ── handlers ────────────────────────────────────────────────────

    def _on_open_docs_folder(self):
        """Open the managed documents root in Explorer/Finder.

        The root can come from the documents.root SETTING, so it is not
        blindly trusted: docs_root() mkdirs it and we refuse anything that
        is not a real directory before handing it to the OS — the local-file
        analogue of the http/https-only openUrl guards elsewhere."""
        try:
            from PySide6.QtCore import QUrl
            from PySide6.QtGui import QDesktopServices
            from src.data.app_paths import docs_root
            root = docs_root()
            if not root.is_dir():
                self._docs_status.setText(f"Documents folder unavailable: {root}")
                return
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(root)))
        except Exception as exc:
            self._docs_status.setText(f"Could not open folder: {exc}")

    def _on_docs_backfill(self):
        """Synchronous backfill (mkdir + small file exports — instant)."""
        self._docs_backfill_btn.setText("Backfilling...")
        self._docs_backfill_btn.setEnabled(False)
        from PySide6.QtWidgets import QApplication
        QApplication.processEvents()
        try:
            from src.data.docs_backfill import run_backfill
            report = run_backfill()
            created = sum(1 for t in report["tree"] if t["created"])
            bits = [
                f"{created} folders created" if created else "folders present",
                f"{report['exports_copied']} exports copied",
                f"{report['zendesk_edits_written']} Zendesk drafts exported",
            ]
            if report["errors"]:
                bits.append(f"{len(report['errors'])} errors — first: {report['errors'][0]}")
            self._docs_status.setText("Backfill done: " + " · ".join(bits))
        except Exception as exc:
            self._docs_status.setText(f"Backfill failed: {exc}")
        finally:
            self._docs_backfill_btn.setText("Run Backfill")
            self._docs_backfill_btn.setEnabled(True)

    def _on_memory_snapshot(self):
        """Take a memory snapshot and display the report."""
        self._mem_snapshot_btn.setText("Analyzing...")
        self._mem_snapshot_btn.setEnabled(False)
        from PySide6.QtWidgets import QApplication
        QApplication.processEvents()

        try:
            from src.data.memory_profiler import MemoryProfiler
            if not MemoryProfiler.is_started():
                MemoryProfiler.start()
            report = MemoryProfiler.snapshot()
            text = MemoryProfiler.format_report(report)

            # Quick stats summary
            rss = report.get("process_rss_mb")
            rss_str = f"{rss:.0f} MB" if rss else "N/A"
            tracked = report.get("tracemalloc_mb", 0)
            gc_objs = report.get("gc_objects_total", 0)
            alma = report.get("alma_objects", {})
            conv_total = alma.get("conversation_dicts_total", 0)
            conv_thread = alma.get("conversation_dicts_with_full_thread", 0)
            thread_mb = alma.get("full_thread_total_mb", 0)
            result_dicts = alma.get("analysis_result_dicts", 0)
            workers = alma.get("qthread_workers", [])
            worker_count = len(workers)
            running = sum(1 for w in workers if w.get("status") == "running")

            self._mem_quick_stats.setText(
                f"RSS: {rss_str}  |  "
                f"Tracked: {tracked:.1f} MB  |  "
                f"GC Objects: {gc_objs:,}  |  "
                f"Conv Dicts: {conv_total} ({conv_thread} with full_thread = {thread_mb:.1f} MB)  |  "
                f"Result Dicts: {result_dicts}  |  "
                f"Workers: {worker_count} ({running} running)"
            )

            self._mem_report_area.setPlainText(text)
            self._mem_report_area.setVisible(True)

        except Exception as e:
            import traceback
            self._mem_quick_stats.setText(f"Error: {e}")
            self._mem_report_area.setPlainText(traceback.format_exc())
            self._mem_report_area.setVisible(True)
        finally:
            self._mem_snapshot_btn.setText("Take Snapshot")
            self._mem_snapshot_btn.setEnabled(True)

    def _on_force_gc(self):
        """Force garbage collection and show before/after stats."""
        import gc as _gc
        from src.data.memory_profiler import MemoryProfiler

        before_rss = MemoryProfiler._get_process_rss_mb()
        before_objs = len(_gc.get_objects())

        collected = _gc.collect()

        after_rss = MemoryProfiler._get_process_rss_mb()
        after_objs = len(_gc.get_objects())

        before_str = f"{before_rss:.0f}" if before_rss else "?"
        after_str = f"{after_rss:.0f}" if after_rss else "?"
        delta_str = ""
        if before_rss and after_rss:
            delta = after_rss - before_rss
            delta_str = f" ({delta:+.0f} MB)"

        self._mem_quick_stats.setText(
            f"GC collected {collected} objects  |  "
            f"RSS: {before_str} → {after_str} MB{delta_str}  |  "
            f"Objects: {before_objs:,} → {after_objs:,}"
        )

    def _full_database_reset(self):
        """Confirm and execute full database reset."""
        from PySide6.QtWidgets import QMessageBox, QInputDialog

        confirm, ok = QInputDialog.getText(
            self,
            "Full Database Reset",
            'This will permanently delete ALL data.\n\n'
            'Type "DELETE" to confirm:',
        )
        if not ok or confirm.strip() != "DELETE":
            return

        try:
            main_window = self.window()
            if hasattr(main_window, '_clear_all_data'):
                main_window._clear_all_data()
            if hasattr(main_window, 'ticket_count_label'):
                main_window.ticket_count_label.setText("0 tickets in database")
            QMessageBox.information(
                self, "Reset Complete",
                "All data has been deleted. The database is now empty."
            )
        except Exception as e:
            QMessageBox.critical(
                self, "Reset Failed",
                f"Database reset failed: {e}"
            )

    # ── style helpers (match the product Settings chrome) ───────────

    def _section_label(self, text):
        lbl = QLabel(text)
        lbl.setStyleSheet(f"""
            font-size: 10px; font-weight: 700; color: {ALMA_TEXT_LIGHT};
            letter-spacing: 1.2px; padding: 0 4px;
        """)
        return lbl

    def _card(self):
        card = QFrame()
        card.setStyleSheet(f"""
            QFrame {{
                background: {ALMA_BG_ELEVATED}; border: none;
                border-radius: 12px;
            }}
        """)
        apply_card_shadow(card)
        return card
