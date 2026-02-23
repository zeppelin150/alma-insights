"""
Alma Insights — Term Manager Panel (Embeddable Widget)

Reusable QWidget version of TermManagerDialog, designed for embedding
inside the DrilldownPanel side drawer.

Three tabs:
  Tab 1: Active Terms — user-curated overrides and feedback
  Tab 2: Discovered Candidates — PMI-based auto-discovered compounds
  Tab 3: Aliases & Concepts — merged view of all compound terms
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTabWidget, QTableWidget, QTableWidgetItem, QHeaderView,
    QAbstractItemView, QLineEdit, QFrame,
)
from PySide6.QtCore import Qt, Signal

from src.ui.theme import (
    ALMA_GREEN_DARK, ALMA_GREEN_MID, ALMA_CREAM, ALMA_WHITE,
    ALMA_TEXT_DARK, ALMA_TEXT_MID, ALMA_TEXT_LIGHT,
    ALMA_BORDER, ALMA_BORDER_LIGHT,
    ALMA_SUCCESS, ALMA_WARNING, ALMA_ERROR, ALMA_INFO,
)


class TermManagerPanel(QWidget):
    """Embeddable term management widget with three tabs."""

    terms_changed = Signal()

    def __init__(self, db_manager, parent=None):
        super().__init__(parent)
        self.db = db_manager
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # Tabs (no dialog chrome — DrilldownPanel provides its own header)
        self.tabs = QTabWidget()
        self.tabs.setStyleSheet(f"""
            QTabBar::tab {{
                font-size: 11px; padding: 6px 14px;
            }}
        """)
        self.tabs.addTab(self._build_active_terms_tab(), "Active Terms")
        self.tabs.addTab(self._build_candidates_tab(), "Candidates")
        self.tabs.addTab(self._build_aliases_tab(), "Aliases")
        layout.addWidget(self.tabs, 1)

    # ═══════════════════════════════════════════
    #  PUBLIC API
    # ═══════════════════════════════════════════

    def refresh(self):
        """Repopulate all three tabs from DB."""
        self._populate_active_terms()
        self._populate_candidates()
        self._populate_aliases()

    # ═══════════════════════════════════════════
    #  TAB 1: ACTIVE TERMS
    # ═══════════════════════════════════════════

    def _build_active_terms_tab(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 6, 0, 0)

        # Search + Add row
        top_row = QHBoxLayout()

        self._term_search = QLineEdit()
        self._term_search.setPlaceholderText("Filter terms...")
        self._term_search.textChanged.connect(self._filter_active_terms)
        top_row.addWidget(self._term_search, 1)

        add_btn = QPushButton("+ Add")
        add_btn.setCursor(Qt.PointingHandCursor)
        add_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {ALMA_GREEN_DARK};
                font-size: 11px; font-weight: 600;
                border: 1px solid {ALMA_GREEN_DARK}; border-radius: 4px;
                padding: 4px 10px;
            }}
            QPushButton:hover {{ background: {ALMA_CREAM}; }}
        """)
        add_btn.clicked.connect(self._add_term_row)
        top_row.addWidget(add_btn)

        layout.addLayout(top_row)

        # Table
        self._active_table = QTableWidget()
        self._active_table.setColumnCount(6)
        self._active_table.setHorizontalHeaderLabels([
            "Term", "Action", "Weight", "Feedback", "Updated", ""
        ])
        self._active_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        for i in range(1, 5):
            self._active_table.horizontalHeader().setSectionResizeMode(
                i, QHeaderView.ResizeToContents
            )
        self._active_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.Fixed)
        self._active_table.horizontalHeader().resizeSection(5, 60)
        self._active_table.verticalHeader().setVisible(False)
        self._active_table.verticalHeader().setDefaultSectionSize(28)
        self._active_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._active_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        layout.addWidget(self._active_table, 1)

        self._populate_active_terms()
        return widget

    def _populate_active_terms(self):
        terms = self.db.get_user_terms()
        feedback = {}
        for row in self.db.get_feedback_summary():
            term = row["term"]
            feedback[term] = feedback.get(term, 0) + row["cnt"]

        self._active_table.setRowCount(len(terms))
        for i, t in enumerate(terms):
            self._active_table.setItem(i, 0, QTableWidgetItem(t["term"]))

            action_item = QTableWidgetItem(t["action"])
            color = (ALMA_SUCCESS if t["action"] == "promote"
                     else ALMA_ERROR if t["action"] == "demote"
                     else ALMA_INFO)
            action_item.setForeground(self._qcolor(color))
            self._active_table.setItem(i, 1, action_item)

            self._active_table.setItem(i, 2, QTableWidgetItem(f"{t['weight_modifier']:.1f}"))
            self._active_table.setItem(i, 3, QTableWidgetItem(
                str(feedback.get(t["term"], 0))
            ))
            self._active_table.setItem(i, 4, QTableWidgetItem(
                t["updated_at"][:10] if t.get("updated_at") else ""
            ))

            del_btn = QPushButton("Delete")
            del_btn.setStyleSheet(
                f"background: transparent; color: {ALMA_ERROR}; font-size: 10px; border: none;"
            )
            del_btn.setCursor(Qt.PointingHandCursor)
            term_text = t["term"]
            del_btn.clicked.connect(lambda _, tt=term_text: self._delete_term(tt))
            self._active_table.setCellWidget(i, 5, del_btn)

    def _filter_active_terms(self, text):
        text = text.lower()
        for row in range(self._active_table.rowCount()):
            item = self._active_table.item(row, 0)
            if item:
                self._active_table.setRowHidden(
                    row, text not in item.text().lower()
                )

    def _add_term_row(self):
        from PySide6.QtWidgets import QInputDialog
        term, ok = QInputDialog.getText(self, "Add Term", "Enter term:")
        if ok and term.strip():
            canonical = term.strip().lower().replace(" ", "_")
            self.db.upsert_user_term(
                term.strip().lower(), canonical, "promote",
                weight_modifier=1.5
            )
            self._populate_active_terms()
            self.terms_changed.emit()

    def _delete_term(self, term):
        self.db.delete_user_term(term)
        self._populate_active_terms()
        self.terms_changed.emit()

    # ═══════════════════════════════════════════
    #  TAB 2: DISCOVERED CANDIDATES
    # ═══════════════════════════════════════════

    def _build_candidates_tab(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 6, 0, 0)

        info = QLabel(
            "Auto-discovered compound terms from PMI analysis. "
            "Approve to include in future analyses, or reject to suppress."
        )
        info.setStyleSheet(f"color: {ALMA_TEXT_LIGHT}; font-size: 10px;")
        info.setWordWrap(True)
        layout.addWidget(info)

        self._candidates_table = QTableWidget()
        self._candidates_table.setColumnCount(6)
        self._candidates_table.setHorizontalHeaderLabels([
            "Phrase", "PMI", "Freq", "First Seen", "Last Seen", "Actions"
        ])
        self._candidates_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        for i in range(1, 5):
            self._candidates_table.horizontalHeader().setSectionResizeMode(
                i, QHeaderView.ResizeToContents
            )
        self._candidates_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.Fixed)
        self._candidates_table.horizontalHeader().resizeSection(5, 140)
        self._candidates_table.verticalHeader().setVisible(False)
        self._candidates_table.verticalHeader().setDefaultSectionSize(30)
        self._candidates_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        layout.addWidget(self._candidates_table, 1)

        self._populate_candidates()
        return widget

    def _populate_candidates(self):
        candidates = self.db.get_discovered_compounds(status="candidate")
        self._candidates_table.setRowCount(len(candidates))

        from datetime import datetime, timedelta
        week_ago = (datetime.now() - timedelta(days=7)).isoformat()

        for i, c in enumerate(candidates):
            phrase_item = QTableWidgetItem(c["phrase"])
            if c.get("first_seen", "") >= week_ago:
                phrase_item.setText(f"\U0001f195 {c['phrase']}")
            self._candidates_table.setItem(i, 0, phrase_item)
            self._candidates_table.setItem(i, 1, QTableWidgetItem(f"{c['pmi_score']:.2f}"))
            self._candidates_table.setItem(i, 2, QTableWidgetItem(str(c["frequency"])))
            self._candidates_table.setItem(i, 3, QTableWidgetItem(
                c.get("first_seen", "")[:10]
            ))
            self._candidates_table.setItem(i, 4, QTableWidgetItem(
                c.get("last_seen", "")[:10]
            ))

            # Action buttons — narrower for 440px panel
            btn_widget = QWidget()
            btn_layout = QHBoxLayout(btn_widget)
            btn_layout.setContentsMargins(2, 2, 2, 2)
            btn_layout.setSpacing(3)

            approve_btn = QPushButton("Approve")
            approve_btn.setMinimumWidth(55)
            approve_btn.setFixedHeight(22)
            approve_btn.setStyleSheet(
                f"background: transparent; color: {ALMA_SUCCESS}; font-size: 10px; "
                f"border: 1px solid {ALMA_SUCCESS}; border-radius: 4px; padding: 1px 6px;"
            )
            approve_btn.setCursor(Qt.PointingHandCursor)
            phrase = c["phrase"]
            approve_btn.clicked.connect(lambda _, p=phrase: self._approve_compound(p))
            btn_layout.addWidget(approve_btn)

            reject_btn = QPushButton("Reject")
            reject_btn.setMinimumWidth(50)
            reject_btn.setFixedHeight(22)
            reject_btn.setStyleSheet(
                f"background: transparent; color: {ALMA_ERROR}; font-size: 10px; "
                f"border: 1px solid {ALMA_ERROR}; border-radius: 4px; padding: 1px 6px;"
            )
            reject_btn.setCursor(Qt.PointingHandCursor)
            reject_btn.clicked.connect(lambda _, p=phrase: self._reject_compound(p))
            btn_layout.addWidget(reject_btn)

            self._candidates_table.setCellWidget(i, 5, btn_widget)

    def _approve_compound(self, phrase):
        self.db.update_compound_status(phrase, "approved")
        self._populate_candidates()
        self._populate_aliases()
        self.terms_changed.emit()

    def _reject_compound(self, phrase):
        self.db.update_compound_status(phrase, "rejected")
        self._populate_candidates()

    # ═══════════════════════════════════════════
    #  TAB 3: ALIASES & CONCEPTS
    # ═══════════════════════════════════════════

    def _build_aliases_tab(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 6, 0, 0)

        info = QLabel(
            "All compound terms: built-in (from Pass 1) + approved discovered terms. "
            "Built-in terms cannot be modified."
        )
        info.setStyleSheet(f"color: {ALMA_TEXT_LIGHT}; font-size: 10px;")
        info.setWordWrap(True)
        layout.addWidget(info)

        self._aliases_table = QTableWidget()
        self._aliases_table.setColumnCount(4)
        self._aliases_table.setHorizontalHeaderLabels([
            "Phrase", "Normalized", "Source", "Status"
        ])
        self._aliases_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self._aliases_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        for i in range(2, 4):
            self._aliases_table.horizontalHeader().setSectionResizeMode(
                i, QHeaderView.ResizeToContents
            )
        self._aliases_table.verticalHeader().setVisible(False)
        self._aliases_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        layout.addWidget(self._aliases_table, 1)

        self._populate_aliases()
        return widget

    def _populate_aliases(self):
        from src.data.trending_engine import COMPOUND_TERMS

        # Built-in terms
        rows = []
        for phrase, normalized in sorted(COMPOUND_TERMS.items()):
            rows.append((phrase, normalized, "Built-in", "Active"))

        # Discovered + approved
        approved = self.db.get_discovered_compounds(status="approved")
        for c in approved:
            rows.append((c["phrase"], c["normalized"], "Discovered", "Active"))

        self._aliases_table.setRowCount(len(rows))
        for i, (phrase, normalized, source, status) in enumerate(rows):
            self._aliases_table.setItem(i, 0, QTableWidgetItem(phrase))
            self._aliases_table.setItem(i, 1, QTableWidgetItem(normalized))

            source_item = QTableWidgetItem(source)
            source_item.setForeground(
                self._qcolor(ALMA_INFO if source == "Built-in" else ALMA_SUCCESS)
            )
            self._aliases_table.setItem(i, 2, source_item)
            self._aliases_table.setItem(i, 3, QTableWidgetItem(status))

    # ── Helpers ──

    @staticmethod
    def _qcolor(hex_color):
        from PySide6.QtGui import QColor
        return QColor(hex_color)
