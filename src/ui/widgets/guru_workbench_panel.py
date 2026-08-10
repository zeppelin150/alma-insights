"""
Alma Insights — Guru Card Workbench Panel

Two-panel layout for SOP teams to select cards, scope friction by TRCs,
link product guide references, run Claude analysis, and view redline drafts.

Left panel:  Card selection, reference cards, TRC scope, context textarea
Right panel: Status, phase progress, GuruCardViewer with redlines, export/actions

Architecture: QSplitter(left_scroll, right_stack).  Analysis runs in a
QThread worker using ``build_client_for_task("ops")`` → Claude client.
"""

import json
import logging
from datetime import datetime, timedelta

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QScrollArea, QLineEdit, QTextEdit, QCheckBox,
    QSplitter, QApplication, QFileDialog, QSizePolicy,
)
from PySide6.QtCore import Qt, QThread, Signal, QTimer

from src.ui.theme import (
    ALMA_GREEN_DARK, ALMA_GREEN_LIGHT, ALMA_CREAM, ALMA_WHITE,
    ALMA_TEXT_DARK, ALMA_TEXT_MID, ALMA_TEXT_LIGHT,
    ALMA_BORDER, ALMA_BORDER_LIGHT, ALMA_BG_ELEVATED, ALMA_BG_INSET,
    ALMA_SUCCESS, ALMA_WARNING, ALMA_ERROR, ALMA_INFO,
)
from src.ui.widgets.guru_card_viewer import (
    GuruCardViewer, guru_card_to_html, GAP_LABELS,
)
from src.ui.widgets.empty_state import EmptyState

logger = logging.getLogger("alma.guru_workbench")

# ── Gap score thresholds → layman labels ────────────────────────

def score_to_gap_label(score: float) -> tuple[str, str]:
    """Convert a friction score to a (label, color) tuple.

    Returns one of the GAP_LABELS values based on thresholds.
    """
    if score >= 0.7:
        return GAP_LABELS["critical"]
    elif score >= 0.5:
        return GAP_LABELS["needs_update"]
    elif score >= 0.3:
        return GAP_LABELS["minor"]
    return GAP_LABELS["up_to_date"]


# ── Style constants ─────────────────────────────────────────────

_SECTION_STYLE = f"""
    QFrame {{
        background: {ALMA_BG_ELEVATED};
        border: none;
        border-radius: 12px;
    }}
"""
_SEARCH_STYLE = f"""
    QLineEdit {{
        background: {ALMA_WHITE}; border: 1px solid {ALMA_BORDER};
        border-radius: 8px; padding: 8px 12px; font-size: 13px;
        color: {ALMA_TEXT_DARK};
    }}
"""
_CONTEXT_STYLE = f"""
    QTextEdit {{
        background: {ALMA_WHITE}; border: 1px solid {ALMA_BORDER};
        border-radius: 8px; padding: 10px 12px; font-size: 13px;
        color: {ALMA_TEXT_DARK}; font-family: inherit;
    }}
"""
_PRIMARY_BTN = f"""
    QPushButton {{
        background: {ALMA_GREEN_DARK}; color: {ALMA_CREAM};
        border: none; border-radius: 8px; padding: 10px 24px;
        font-size: 13px; font-weight: 600;
    }}
    QPushButton:hover {{ background: {ALMA_GREEN_LIGHT}; }}
    QPushButton:disabled {{ background: {ALMA_BORDER}; color: {ALMA_TEXT_LIGHT}; }}
"""
_GHOST_BTN = f"""
    QPushButton {{
        background: transparent; color: {ALMA_GREEN_DARK};
        border: 1px solid {ALMA_GREEN_DARK}; border-radius: 6px;
        padding: 6px 14px; font-size: 12px; font-weight: 600;
    }}
    QPushButton:hover {{ background: {ALMA_CREAM}; }}
"""
_EXPORT_BTN = f"""
    QPushButton {{
        background: transparent; color: {ALMA_TEXT_MID};
        font-size: 11px; font-weight: 600;
        border: 1px solid {ALMA_BORDER}; border-radius: 6px;
        padding: 4px 12px;
    }}
    QPushButton:hover {{ background: {ALMA_CREAM}; }}
"""
_SECTION_TITLE = f"font-size: 12px; font-weight: 700; letter-spacing: 0.5px; text-transform: uppercase; background: transparent;"


class GuruWorkbenchPanel(QWidget):
    """Card Workbench — select cards, scope friction, run analysis, view redlines."""

    analysis_complete = Signal(dict)  # emitted with analysis result

    def __init__(self, db, guru_client=None, parent=None):
        super().__init__(parent)
        self.db = db
        self._guru_client = guru_client
        self._drilldown = None
        self._analysis_worker = None
        self._current_card: dict = {}
        self._current_redlines: list[dict] = []

        self.setStyleSheet("background: transparent;")
        self._build_ui()

    # ── Public wiring ────────────────────────────────────────────

    def set_guru_client(self, client):
        self._guru_client = client

    def set_drilldown_panel(self, panel):
        self._drilldown = panel

    # ── UI Construction ──────────────────────────────────────────

    def _build_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 12, 0, 0)
        layout.setSpacing(16)

        # Left panel (scrollable, fixed width)
        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setFixedWidth(340)
        left_scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        left_widget = QWidget()
        left_widget.setStyleSheet("background: transparent;")
        self._left_layout = QVBoxLayout(left_widget)
        self._left_layout.setContentsMargins(0, 0, 0, 0)
        self._left_layout.setSpacing(12)

        self._build_card_selection()
        self._build_reference_cards()
        self._build_trc_scope()
        self._build_context_input()
        self._build_run_button()
        self._left_layout.addStretch()

        left_scroll.setWidget(left_widget)
        layout.addWidget(left_scroll)

        # Right panel (flex)
        right_widget = QWidget()
        right_widget.setStyleSheet("background: transparent;")
        self._right_layout = QVBoxLayout(right_widget)
        self._right_layout.setContentsMargins(0, 0, 0, 0)
        self._right_layout.setSpacing(12)

        self._build_status_bar()
        self._build_phase_track()
        self._build_output_area()
        self._build_export_bar()
        self._build_action_bar()

        layout.addWidget(right_widget, 1)

    # ── Left Panel Sections ──────────────────────────────────────

    def _build_card_selection(self):
        """Section 1: Select Cards to Update."""
        frame = QFrame()
        frame.setStyleSheet(_SECTION_STYLE + f"border-left: 4px solid {ALMA_GREEN_LIGHT};")
        fl = QVBoxLayout(frame)
        fl.setContentsMargins(16, 12, 16, 12)
        fl.setSpacing(6)

        header = QHBoxLayout()
        title = QLabel("Select Cards to Update")
        title.setStyleSheet(_SECTION_TITLE + f"color: {ALMA_GREEN_LIGHT};")
        header.addWidget(title)
        self._card_count_label = QLabel("0 selected")
        self._card_count_label.setStyleSheet(
            f"font-size: 11px; font-weight: 600; color: {ALMA_SUCCESS}; "
            f"background: rgba(22,118,58,0.12); padding: 2px 8px; border-radius: 10px;"
        )
        header.addStretch()
        header.addWidget(self._card_count_label)
        fl.addLayout(header)

        self._card_search = QLineEdit()
        self._card_search.setPlaceholderText("Search cards...")
        self._card_search.setStyleSheet(_SEARCH_STYLE)
        self._card_search.textChanged.connect(self._filter_card_list)
        fl.addWidget(self._card_search)

        self._card_check_area = QVBoxLayout()
        self._card_check_area.setSpacing(4)
        self._card_checkboxes: list[tuple[QCheckBox, str, QLabel]] = []
        fl.addLayout(self._card_check_area)

        self._left_layout.addWidget(frame)

    def _build_reference_cards(self):
        """Section 2: Link Reference Cards (Product Guides)."""
        frame = QFrame()
        frame.setStyleSheet(_SECTION_STYLE + f"border-left: 4px solid {ALMA_INFO};")
        fl = QVBoxLayout(frame)
        fl.setContentsMargins(16, 12, 16, 12)
        fl.setSpacing(6)

        header = QHBoxLayout()
        title = QLabel("Reference Cards (Product Guides)")
        title.setStyleSheet(_SECTION_TITLE + f"color: {ALMA_INFO};")
        header.addWidget(title)
        self._ref_count_label = QLabel("0 linked")
        self._ref_count_label.setStyleSheet(
            f"font-size: 11px; font-weight: 600; color: {ALMA_INFO}; "
            f"background: rgba(29,111,165,0.12); padding: 2px 8px; border-radius: 10px;"
        )
        header.addStretch()
        header.addWidget(self._ref_count_label)
        fl.addLayout(header)

        self._ref_check_area = QVBoxLayout()
        self._ref_check_area.setSpacing(4)
        self._ref_checkboxes: list[tuple[QCheckBox, str]] = []
        fl.addLayout(self._ref_check_area)

        add_btn = QPushButton("+ Link product guide card...")
        add_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {ALMA_TEXT_MID}; "
            f"border: 1.5px dashed {ALMA_BORDER}; border-radius: 8px; "
            f"padding: 8px 12px; font-size: 12px; }}"
        )
        add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        fl.addWidget(add_btn)

        self._left_layout.addWidget(frame)

    def _build_trc_scope(self):
        """Section 3: Friction Scope (TRCs)."""
        frame = QFrame()
        frame.setStyleSheet(_SECTION_STYLE + f"border-left: 4px solid {ALMA_WARNING};")
        fl = QVBoxLayout(frame)
        fl.setContentsMargins(16, 12, 16, 12)
        fl.setSpacing(6)

        header = QHBoxLayout()
        title = QLabel("Friction Scope (TRCs)")
        title.setStyleSheet(_SECTION_TITLE + f"color: {ALMA_WARNING};")
        header.addWidget(title)
        self._trc_count_label = QLabel("0 TRCs")
        self._trc_count_label.setStyleSheet(
            f"font-size: 11px; font-weight: 600; color: {ALMA_WARNING}; "
            f"background: rgba(180,83,9,0.12); padding: 2px 8px; border-radius: 10px;"
        )
        header.addStretch()
        header.addWidget(self._trc_count_label)
        fl.addLayout(header)

        self._trc_check_area = QVBoxLayout()
        self._trc_check_area.setSpacing(4)
        self._trc_checkboxes: list[tuple[QCheckBox, str, int]] = []
        fl.addLayout(self._trc_check_area)

        self._left_layout.addWidget(frame)

    def _build_context_input(self):
        """Section 4: Analysis Context."""
        frame = QFrame()
        frame.setStyleSheet(_SECTION_STYLE + f"border-left: 4px solid #7C3AED;")
        fl = QVBoxLayout(frame)
        fl.setContentsMargins(16, 12, 16, 12)
        fl.setSpacing(6)

        title = QLabel("Analysis Context")
        title.setStyleSheet(_SECTION_TITLE + "color: #7C3AED;")
        fl.addWidget(title)

        self._context_input = QTextEdit()
        self._context_input.setPlaceholderText(
            "Focus on specific changes, processes, or friction patterns..."
        )
        self._context_input.setStyleSheet(_CONTEXT_STYLE)
        self._context_input.setMaximumHeight(80)
        fl.addWidget(self._context_input)

        self._left_layout.addWidget(frame)

    def _build_run_button(self):
        self._run_btn = QPushButton("Run Analysis")
        self._run_btn.setStyleSheet(_PRIMARY_BTN)
        self._run_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._run_btn.clicked.connect(self._run_analysis)
        self._left_layout.addWidget(self._run_btn)

    # ── Right Panel Sections ─────────────────────────────────────

    def _build_status_bar(self):
        self._status_frame = QFrame()
        self._status_frame.setStyleSheet(
            f"QFrame {{ background: rgba(3,40,27,0.04); border-radius: 8px; "
            f"border: none; }}"
        )
        sl = QHBoxLayout(self._status_frame)
        sl.setContentsMargins(16, 8, 16, 8)
        self._status_dot = QLabel("●")
        self._status_dot.setStyleSheet(f"color: {ALMA_TEXT_LIGHT}; font-size: 10px; background: transparent;")
        sl.addWidget(self._status_dot)
        self._status_text = QLabel("Ready — select cards and TRCs, then run analysis")
        self._status_text.setStyleSheet(f"color: {ALMA_TEXT_MID}; font-size: 12px; background: transparent;")
        sl.addWidget(self._status_text, 1)
        self._right_layout.addWidget(self._status_frame)

    def _build_phase_track(self):
        self._phase_frame = QFrame()
        self._phase_frame.setStyleSheet("border: none; background: transparent;")
        pl = QVBoxLayout(self._phase_frame)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(4)

        track = QHBoxLayout()
        track.setSpacing(2)
        self._phase_bars = []
        for _ in range(3):
            bar = QFrame()
            bar.setFixedHeight(4)
            bar.setStyleSheet(
                f"background: {ALMA_BORDER_LIGHT}; border-radius: 2px; border: none;"
            )
            track.addWidget(bar, 1)
            self._phase_bars.append(bar)
        pl.addLayout(track)

        labels = QHBoxLayout()
        for name in ("Search & Gather", "Per-Card Analysis", "Synthesis & Draft"):
            lbl = QLabel(name)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet(f"font-size: 10px; color: {ALMA_TEXT_LIGHT}; background: transparent;")
            labels.addWidget(lbl, 1)
        pl.addLayout(labels)

        self._right_layout.addWidget(self._phase_frame)

    def _build_output_area(self):
        # Card viewer (main output)
        self._card_viewer = GuruCardViewer()
        self._card_viewer.setVisible(False)

        # Empty state
        self._empty_state = EmptyState(
            message="No analysis yet",
            icon="search",
            heading="No analysis yet",
            description="Select cards and TRCs from the left panel, then click Run Analysis.",
        )

        self._right_layout.addWidget(self._card_viewer, 1)
        self._right_layout.addWidget(self._empty_state, 1)

    def _build_export_bar(self):
        self._export_frame = QFrame()
        self._export_frame.setStyleSheet("border: none; background: transparent;")
        self._export_frame.setVisible(False)
        el = QHBoxLayout(self._export_frame)
        el.setContentsMargins(0, 0, 0, 0)
        el.setSpacing(8)
        el.addStretch()

        self._copy_btn = QPushButton("Copy")
        self._copy_btn.setStyleSheet(_EXPORT_BTN)
        self._copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._copy_btn.clicked.connect(self._export_copy)
        el.addWidget(self._copy_btn)

        self._save_md_btn = QPushButton("Save .md")
        self._save_md_btn.setStyleSheet(_EXPORT_BTN)
        self._save_md_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._save_md_btn.clicked.connect(self._export_save_md)
        el.addWidget(self._save_md_btn)

        self._save_html_btn = QPushButton("Save .html")
        self._save_html_btn.setStyleSheet(_EXPORT_BTN)
        self._save_html_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._save_html_btn.clicked.connect(self._export_save_html)
        el.addWidget(self._save_html_btn)

        self._right_layout.addWidget(self._export_frame)

    def _build_action_bar(self):
        self._action_frame = QFrame()
        self._action_frame.setStyleSheet("border: none; background: transparent;")
        self._action_frame.setVisible(False)
        al = QHBoxLayout(self._action_frame)
        al.setContentsMargins(0, 8, 0, 0)
        al.setSpacing(8)
        al.addStretch()

        reject_btn = QPushButton("Reject")
        reject_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {ALMA_ERROR}; "
            f"border: 1px solid {ALMA_ERROR}; border-radius: 6px; "
            f"padding: 6px 14px; font-size: 12px; font-weight: 600; }}"
        )
        reject_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        reject_btn.clicked.connect(self._on_reject)
        al.addWidget(reject_btn)

        edit_btn = QPushButton("Edit Draft")
        edit_btn.setStyleSheet(_GHOST_BTN)
        edit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        al.addWidget(edit_btn)

        commit_btn = QPushButton("Commit to Drafts")
        commit_btn.setStyleSheet(_PRIMARY_BTN)
        commit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        commit_btn.clicked.connect(self._on_commit_draft)
        al.addWidget(commit_btn)

        self._right_layout.addWidget(self._action_frame)

    # ── Data Loading ─────────────────────────────────────────────

    def refresh(self):
        """Reload card list and TRC list from data sources."""
        self._load_card_list()
        self._load_trc_list()

    def _load_card_list(self):
        """Load cards into the selection checklist."""
        # Clear existing
        for cb, cid, badge in self._card_checkboxes:
            cb.setParent(None)
            badge.setParent(None)
        self._card_checkboxes.clear()

        cards = []
        if self._guru_client:
            try:
                cards = self._guru_client.list_cards()
            except Exception as exc:
                logger.warning("Failed to load cards: %s", exc)

        for card in cards:
            card_id = card.get("id", "")
            title = card.get("title", "Untitled")[:50]
            score = self._get_friction_score(card_id)
            label_text, label_color = score_to_gap_label(score)

            row = QHBoxLayout()
            row.setSpacing(8)

            cb = QCheckBox(title)
            cb.setStyleSheet(f"font-size: 13px; color: {ALMA_TEXT_DARK}; background: transparent;")
            cb.stateChanged.connect(self._update_card_count)
            row.addWidget(cb, 1)

            badge = QLabel(label_text)
            badge.setStyleSheet(
                f"font-size: 11px; font-weight: 600; color: {label_color}; "
                f"padding: 2px 8px; border-radius: 10px; background: transparent;"
            )
            row.addWidget(badge)

            container = QWidget()
            container.setLayout(row)
            container.setStyleSheet("background: transparent;")
            self._card_check_area.addWidget(container)
            self._card_checkboxes.append((cb, card_id, badge))

    def _load_trc_list(self):
        """Load TRC codes into the scope checklist."""
        for cb, trc, count in self._trc_checkboxes:
            cb.setParent(None)
        self._trc_checkboxes.clear()

        # Get TRCs with ticket counts from last 90 days
        try:
            end = datetime.now().strftime("%Y-%m-%d")
            start = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
            trc_counts = self.db.get_trc_ticket_counts(start, end)
        except Exception:
            trc_counts = []

        for item in trc_counts[:20]:  # top 20
            trc = item.get("trc", "")
            count = item.get("n", 0)
            if not trc:
                continue

            row = QHBoxLayout()
            row.setSpacing(8)

            cb = QCheckBox(trc[:40])
            cb.setStyleSheet(f"font-size: 13px; color: {ALMA_TEXT_DARK}; background: transparent;")
            cb.stateChanged.connect(self._update_trc_count)
            row.addWidget(cb, 1)

            count_lbl = QLabel(f"{count} tickets")
            count_lbl.setStyleSheet(f"font-size: 11px; color: {ALMA_TEXT_LIGHT}; background: transparent;")
            row.addWidget(count_lbl)

            container = QWidget()
            container.setLayout(row)
            container.setStyleSheet("background: transparent;")
            self._trc_check_area.addWidget(container)
            self._trc_checkboxes.append((cb, trc, count))

    def _get_friction_score(self, card_id: str) -> float:
        try:
            row = self.db.conn.execute(
                "SELECT friction_score FROM guru_articles WHERE card_id = ?",
                (card_id,),
            ).fetchone()
            return row[0] if row else 0.0
        except Exception:
            return 0.0

    # ── Selection Helpers ────────────────────────────────────────

    def _filter_card_list(self, text: str):
        """Filter card checkboxes by search text."""
        text_lower = text.lower()
        for cb, card_id, badge in self._card_checkboxes:
            visible = text_lower in cb.text().lower() if text_lower else True
            cb.parent().setVisible(visible)

    def _update_card_count(self):
        count = sum(1 for cb, _, _ in self._card_checkboxes if cb.isChecked())
        self._card_count_label.setText(f"{count} selected")

    def _update_trc_count(self):
        count = sum(1 for cb, _, _ in self._trc_checkboxes if cb.isChecked())
        self._trc_count_label.setText(f"{count} TRCs")

    def get_selected_cards(self) -> list[str]:
        """Return list of selected card IDs."""
        return [cid for cb, cid, _ in self._card_checkboxes if cb.isChecked()]

    def get_selected_trcs(self) -> list[str]:
        """Return list of selected TRC codes."""
        return [trc for cb, trc, _ in self._trc_checkboxes if cb.isChecked()]

    def get_reference_cards(self) -> list[str]:
        """Return list of reference (product guide) card IDs."""
        return [cid for cb, cid in self._ref_checkboxes if cb.isChecked()]

    # ── Phase Progress ───────────────────────────────────────────

    def _set_phase(self, phase: int):
        """Update phase track bars (0=none, 1-3=phases complete)."""
        for i, bar in enumerate(self._phase_bars):
            if i < phase:
                bar.setStyleSheet(
                    f"background: {ALMA_SUCCESS}; border-radius: 2px; border: none;"
                )
            else:
                bar.setStyleSheet(
                    f"background: {ALMA_BORDER_LIGHT}; border-radius: 2px; border: none;"
                )

    def _set_status(self, text: str, color: str = ALMA_TEXT_MID):
        self._status_dot.setStyleSheet(f"color: {color}; font-size: 10px; background: transparent;")
        self._status_text.setText(text)

    # ── Analysis ─────────────────────────────────────────────────

    def _run_analysis(self):
        """Kick off Claude analysis in background thread."""
        selected = self.get_selected_cards()
        trcs = self.get_selected_trcs()

        if not selected:
            self._set_status("Select at least one card to analyze", ALMA_WARNING)
            return
        if not trcs:
            self._set_status("Select at least one TRC for friction scope", ALMA_WARNING)
            return
        if not self._guru_client:
            self._set_status("Guru not connected", ALMA_ERROR)
            return

        self._run_btn.setEnabled(False)
        self._run_btn.setText("Analyzing...")
        self._set_status("Analysis running...", ALMA_INFO)
        self._set_phase(0)

        # Gather data in background
        self._analysis_worker = AnalysisWorker(
            guru_client=self._guru_client,
            db=self.db,
            card_ids=selected,
            reference_ids=self.get_reference_cards(),
            trc_codes=trcs,
            context=self._context_input.toPlainText().strip(),
        )
        self._analysis_worker.phase_changed.connect(self._on_phase_changed)
        self._analysis_worker.finished_signal.connect(self._on_analysis_complete)
        self._analysis_worker.error_signal.connect(self._on_analysis_error)
        self._analysis_worker.start()

    def _on_phase_changed(self, phase: int, msg: str):
        self._set_phase(phase)
        self._set_status(msg, ALMA_INFO)

    def _on_analysis_complete(self, result: dict):
        self._run_btn.setEnabled(True)
        self._run_btn.setText("Run Analysis")

        card = result.get("card", {})
        redlines = result.get("redlines", [])
        stats_msg = result.get("status_message", "Analysis complete")

        self._current_card = card
        self._current_redlines = redlines

        # Show output
        self._card_viewer.set_card_with_redlines(card, redlines)
        self._card_viewer.setVisible(True)
        self._empty_state.setVisible(False)
        self._export_frame.setVisible(True)
        self._action_frame.setVisible(True)

        self._set_phase(3)
        self._set_status(stats_msg, ALMA_SUCCESS)

    def _on_analysis_error(self, error: str):
        self._run_btn.setEnabled(True)
        self._run_btn.setText("Run Analysis")
        self._set_status(f"Analysis failed: {error}", ALMA_ERROR)
        self._set_phase(0)

    # ── Actions ──────────────────────────────────────────────────

    def _on_reject(self):
        """Clear the current draft output."""
        self._card_viewer.clear_content()
        self._card_viewer.setVisible(False)
        self._empty_state.setVisible(True)
        self._export_frame.setVisible(False)
        self._action_frame.setVisible(False)
        self._set_phase(0)
        self._set_status("Draft rejected — select cards and run again", ALMA_TEXT_MID)
        self._current_card = {}
        self._current_redlines = []

    def _on_commit_draft(self):
        """Save the draft for human review (gated — does NOT auto-publish)."""
        if not self._current_card:
            return
        # For now, emit signal for upstream handling
        self.analysis_complete.emit({
            "card": self._current_card,
            "redlines": self._current_redlines,
        })
        self._set_status("Draft committed for review", ALMA_SUCCESS)

    # ── Export ───────────────────────────────────────────────────

    def _export_copy(self):
        md = self._card_viewer.get_card_markdown()
        if md:
            QApplication.clipboard().setText(md)
            self._copy_btn.setText("Copied!")
            QTimer.singleShot(2000, lambda: self._copy_btn.setText("Copy"))

    def _export_save_md(self):
        md = self._card_viewer.get_card_markdown()
        if not md:
            return
        from src.data.app_paths import start_dir
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Card Draft", start_dir("downloads"), "Markdown (*.md);;Text (*.txt)"
        )
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(md)
            self._save_md_btn.setText("Saved!")
            QTimer.singleShot(2000, lambda: self._save_md_btn.setText("Save .md"))

    def _export_save_html(self):
        if not self._current_card:
            return
        from src.data.app_paths import start_dir
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Card Draft as HTML", start_dir("downloads"), "HTML (*.html)"
        )
        if path:
            html = guru_card_to_html(self._current_card, self._current_redlines)
            with open(path, "w", encoding="utf-8") as f:
                f.write(html)
            self._save_html_btn.setText("Saved!")
            QTimer.singleShot(2000, lambda: self._save_html_btn.setText("Save .html"))


# ══════════════════════════════════════════════════════════════════════
# Analysis Worker Thread
# ══════════════════════════════════════════════════════════════════════

class AnalysisWorker(QThread):
    """Background thread for Claude-powered card analysis.

    Phase 1: Gather data (card content, reference content, friction data)
    Phase 2: Per-card analysis via Claude
    Phase 3: Synthesize redline draft
    """

    phase_changed = Signal(int, str)  # (phase_number, description)
    finished_signal = Signal(dict)     # analysis result
    error_signal = Signal(str)         # error message

    def __init__(self, guru_client, db, card_ids, reference_ids,
                 trc_codes, context, parent=None):
        super().__init__(parent)
        self._guru_client = guru_client
        self._db = db
        self._card_ids = card_ids
        self._reference_ids = reference_ids
        self._trc_codes = trc_codes
        self._context = context

    def run(self):
        try:
            # Phase 1: Gather
            self.phase_changed.emit(1, "Gathering card content and friction data...")

            # Fetch target card (first selected)
            card = self._guru_client.get_card(self._card_ids[0])
            if not card:
                self.error_signal.emit("Could not fetch card content")
                return

            # Fetch reference cards
            ref_contents = []
            for rid in self._reference_ids:
                try:
                    ref = self._guru_client.get_card(rid)
                    if ref:
                        ref_contents.append(ref)
                except Exception:
                    pass

            # Fetch friction data for scoped TRCs
            friction_data = self._gather_friction_data()

            # Phase 2: Analyze via Claude
            self.phase_changed.emit(2, "Running per-card analysis with Claude...")

            redlines = self._run_claude_analysis(card, ref_contents, friction_data)

            # Phase 3: Synthesize
            self.phase_changed.emit(3, "Synthesizing draft...")

            # Build stats
            n_cards = len(self._card_ids)
            n_refs = len(ref_contents)
            n_tickets = sum(
                item.get("ticket_count", 0)
                for item in friction_data
            )

            self.finished_signal.emit({
                "card": card,
                "redlines": redlines,
                "status_message": (
                    f"Analysis complete — {n_cards} card{'s' if n_cards != 1 else ''} "
                    f"analyzed, {n_refs} reference{'s' if n_refs != 1 else ''} used, "
                    f"{n_tickets} friction tickets processed"
                ),
            })

        except Exception as exc:
            logger.warning("Analysis worker failed: %s", exc)
            self.error_signal.emit(str(exc))

    def _gather_friction_data(self) -> list[dict]:
        """Gather friction classification data for scoped TRCs."""
        results = []
        try:
            for trc in self._trc_codes:
                agg = self._db.get_nlp_aggregate_for_trc(trc)
                if agg:
                    agg["trc"] = trc
                    results.append(agg)
        except Exception as exc:
            logger.warning("Friction data gather failed: %s", exc)
        return results

    def _run_claude_analysis(self, card, ref_contents, friction_data) -> list[dict]:
        """Call Claude to analyze the card and produce redline annotations.

        Returns list of redline dicts: [{type, html, old, new, text, title}]
        """
        try:
            from src.gemini.client_factory import build_client_for_task
            client = build_client_for_task("ops")
        except Exception:
            # Fallback: return empty redlines if no Claude client
            logger.warning("No Claude client available for analysis")
            return []

        if not client:
            return []

        # Build prompt
        prompt = self._build_analysis_prompt(card, ref_contents, friction_data)

        try:
            response = client.generate(prompt)
            return parse_redline_response(response)
        except Exception as exc:
            logger.warning("Claude analysis failed: %s", exc)
            return []

    def _build_analysis_prompt(self, card, ref_contents, friction_data) -> str:
        """Assemble the analysis prompt for Claude."""
        parts = []
        parts.append(
            "You are a Guru knowledge base analyst. Analyze the following "
            "Guru card and produce specific edit recommendations based on "
            "customer friction data.\n"
        )

        # Target card
        parts.append(f"## Target Card: {card.get('title', 'Untitled')}")
        parts.append(f"Collection: {card.get('collection', 'N/A')}")
        parts.append(f"Content:\n```\n{card.get('content', '')}\n```\n")

        # Reference cards
        if ref_contents:
            parts.append("## Reference Cards (Product Guides)")
            for ref in ref_contents:
                parts.append(f"### {ref.get('title', 'Untitled')}")
                parts.append(f"```\n{ref.get('content', '')}\n```\n")

        # Friction data
        if friction_data:
            parts.append("## Friction Data (from NLP scan)")
            for item in friction_data:
                trc = item.get("trc", "")
                tc = item.get("ticket_count", 0)
                parts.append(f"### TRC: {trc} ({tc} tickets)")
                dist = item.get("friction_distribution", {})
                if dist:
                    for ftype, count in dist.items():
                        parts.append(f"  - {ftype}: {count}")
                hints = item.get("top_root_cause_hints", [])
                if hints:
                    parts.append("  Root causes: " + ", ".join(hints[:5]))
                parts.append("")

        # User context
        if self._context:
            parts.append(f"## Additional Context\n{self._context}\n")

        # Output format
        parts.append(
            "## Output Format\n"
            "Return a JSON array of edit annotations. Each item must have:\n"
            '- "type": "insert" | "modify" | "warning"\n'
            '- For "insert": include "html" (the proposed new text/HTML)\n'
            '- For "modify": include "title" (section), "old" (original text), '
            '"new" (replacement text)\n'
            '- For "warning": include "text" (the warning message)\n\n'
            "Return ONLY the JSON array, no other text."
        )

        return "\n".join(parts)


# ══════════════════════════════════════════════════════════════════════
# Response Parsing
# ══════════════════════════════════════════════════════════════════════

def parse_redline_response(response: str) -> list[dict]:
    """Parse Claude's JSON response into a list of redline dicts.

    Handles both raw JSON and markdown-wrapped JSON (```json ... ```).
    """
    if not response:
        return []

    text = response.strip()

    # Strip markdown code fences
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove first and last lines (fences)
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines).strip()

    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        logger.warning("Failed to parse redline JSON from Claude response")
        return []

    if not isinstance(data, list):
        return []

    # Validate each item
    valid = []
    for item in data:
        if not isinstance(item, dict):
            continue
        rl_type = item.get("type", "")
        if rl_type not in ("insert", "modify", "warning"):
            continue
        valid.append(item)

    return valid
