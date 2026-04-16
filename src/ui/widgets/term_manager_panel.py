"""
Alma Insights — Term Manager Panel (Embeddable Widget)

Reusable QWidget version of TermManagerDialog, designed for embedding
inside a tab or the DrilldownPanel side drawer.

Three tabs:
  Tab 1: Active Terms — user-curated overrides and feedback
  Tab 2: Discovered Candidates — PMI-based auto-discovered compounds
  Tab 3: Aliases & Concepts — merged view of all compound terms

All tables are paginated (fixed row count, no horizontal scroll).
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTabWidget, QTableWidget, QTableWidgetItem, QHeaderView,
    QAbstractItemView, QLineEdit, QFrame, QSizePolicy, QFileDialog,
    QDialog, QFormLayout, QDialogButtonBox,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QColor

from src.ui.theme import (
    ALMA_GREEN_DARK, ALMA_GREEN_MID, ALMA_GREEN_LIGHT, ALMA_GREEN_SUBTLE,
    ALMA_CREAM, ALMA_WHITE,
    ALMA_TEXT_DARK, ALMA_TEXT_MID, ALMA_TEXT_LIGHT, ALMA_TEXT_ON_DARK,
    ALMA_BORDER, ALMA_BORDER_LIGHT,
    ALMA_BG_ELEVATED,
    ALMA_SUCCESS, ALMA_WARNING, ALMA_ERROR, ALMA_INFO,
    apply_card_shadow, apply_card_shadow_soft, configure_table,
)
from src.ui.widgets.pagination_bar import PaginationBar

# Page sizes for each tab
_ACTIVE_PAGE_SIZE = 8
_CANDIDATES_PAGE_SIZE = 5       # taller rows (80px) so fewer per page
_ALIASES_PAGE_SIZE = 8


# ═══════════════════════════════════════════
#  ADD ALIAS DIALOG (proper two-field form)
# ═══════════════════════════════════════════

class _AddAliasDialog(QDialog):
    """Two-field dialog for adding a phrase → normalized alias."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add Alias")
        self.setMinimumWidth(380)
        self.setStyleSheet(f"""
            QDialog {{ background: {ALMA_CREAM}; }}
            QLabel {{ font-size: 12px; color: {ALMA_TEXT_DARK}; }}
            QLineEdit {{
                border: 1px solid {ALMA_BORDER};
                border-radius: 8px; padding: 8px 12px;
                font-size: 13px; background: {ALMA_WHITE};
            }}
            QLineEdit:focus {{ border-color: {ALMA_GREEN_DARK}; }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(14)

        title = QLabel("Add New Alias")
        title.setStyleSheet(
            f"font-size: 15px; font-weight: 700; color: {ALMA_TEXT_DARK};"
        )
        layout.addWidget(title)

        desc = QLabel(
            "Enter the phrase as it appears in tickets, and the normalized "
            "form it should map to (e.g. 'auto pay' → 'auto_pay')."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet(f"font-size: 11px; color: {ALMA_TEXT_MID};")
        layout.addWidget(desc)

        form = QFormLayout()
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignRight)

        self.phrase_input = QLineEdit()
        self.phrase_input.setPlaceholderText("e.g. auto pay")
        form.addRow("Phrase:", self.phrase_input)

        self.normalized_input = QLineEdit()
        self.normalized_input.setPlaceholderText("e.g. auto_pay")
        form.addRow("Normalized:", self.normalized_input)

        layout.addLayout(form)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {ALMA_TEXT_MID};
                font-size: 12px; font-weight: 600;
                border: 1px solid {ALMA_BORDER}; border-radius: 6px;
                padding: 8px 20px;
            }}
            QPushButton:hover {{ background: {ALMA_WHITE}; }}
        """)
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        add_btn = QPushButton("Add Alias")
        add_btn.setStyleSheet(f"""
            QPushButton {{
                background: {ALMA_GREEN_DARK}; color: {ALMA_TEXT_ON_DARK};
                font-size: 12px; font-weight: 600;
                border: none; border-radius: 6px;
                padding: 8px 20px;
            }}
            QPushButton:hover {{ background: {ALMA_GREEN_MID}; }}
        """)
        add_btn.clicked.connect(self._try_accept)
        btn_row.addWidget(add_btn)

        layout.addLayout(btn_row)

    def _try_accept(self):
        if self.phrase_input.text().strip() and self.normalized_input.text().strip():
            self.accept()

    def get_values(self):
        return (
            self.phrase_input.text().strip().lower(),
            self.normalized_input.text().strip().lower(),
        )


# ═══════════════════════════════════════════
#  TERM MANAGER PANEL
# ═══════════════════════════════════════════

class TermManagerPanel(QWidget):
    """Embeddable term management widget with three tabs."""

    terms_changed = Signal()

    def __init__(self, db_manager, parent=None):
        super().__init__(parent)
        self.db = db_manager
        # Data caches (populated by _populate_* methods)
        self._active_all_data = []      # list of term dicts
        self._active_filtered = []      # after search filter
        self._active_feedback = {}      # term → feedback count
        self._candidates_all_data = []  # list of candidate dicts
        self._alias_all_rows = []       # list of (phrase, normalized, source, status, toggleable)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        # ── Section header ──
        header_frame = QFrame()
        header_frame.setObjectName("TermHeader")
        header_frame.setStyleSheet(
            f"#TermHeader {{ background: {ALMA_BG_ELEVATED};"
            " border: none; border-radius: 12px; }}"
        )
        apply_card_shadow_soft(header_frame)
        header_layout = QVBoxLayout(header_frame)
        header_layout.setContentsMargins(16, 12, 16, 12)
        header_layout.setSpacing(4)

        title = QLabel("Term Management")
        title.setStyleSheet(
            f"font-size: 16px; font-weight: 700; color: {ALMA_TEXT_DARK};"
            " background: transparent; border: none;"
        )
        header_layout.addWidget(title)

        desc = QLabel(
            "Curate the vocabulary used in trending-topic analysis. "
            "Promote or demote terms, approve auto-discovered compound phrases, "
            "and manage aliases that normalize different spellings to a single concept."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet(
            f"font-size: 12px; color: {ALMA_TEXT_MID};"
            " background: transparent; border: none; line-height: 1.5;"
        )
        header_layout.addWidget(desc)

        layout.addWidget(header_frame)

        # ── Tabs ──
        self.tabs = QTabWidget()
        self.tabs.setStyleSheet(f"""
            QTabWidget::pane {{
                background: transparent;
                border: none;
            }}
            QTabBar::tab {{
                font-size: 12px; padding: 8px 18px;
                color: {ALMA_TEXT_MID};
                border: none;
                border-bottom: 2px solid transparent;
            }}
            QTabBar::tab:selected {{
                color: {ALMA_GREEN_DARK};
                border-bottom: 2px solid {ALMA_GREEN_DARK};
                font-weight: 600;
            }}
            QTabBar::tab:hover {{
                color: {ALMA_TEXT_DARK};
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
        try:
            self._populate_active_terms()
            self._populate_candidates()
            self._populate_aliases()
        except Exception:
            pass  # Graceful degradation if DB is temporarily unavailable

    # ═══════════════════════════════════════════
    #  HELPERS
    # ═══════════════════════════════════════════

    @staticmethod
    def _qcolor(hex_color):
        return QColor(hex_color)

    @staticmethod
    def _resize_table(table, page_size, row_height):
        """Set table to fixed height for exactly page_size rows + header."""
        header_h = table.horizontalHeader().height()
        if header_h < 10:
            header_h = 38  # fallback before first paint
        needed = header_h + (page_size * row_height) + 4
        table.setFixedHeight(max(needed, 60))

    # ═══════════════════════════════════════════
    #  TAB 1: ACTIVE TERMS
    # ═══════════════════════════════════════════

    def _build_active_terms_tab(self):
        container = QFrame()
        container.setObjectName("ActiveTermsCard")
        container.setStyleSheet(
            f"#ActiveTermsCard {{ background: {ALMA_BG_ELEVATED};"
            " border: none; border-radius: 12px; }}"
        )
        apply_card_shadow_soft(container)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(12, 10, 12, 10)

        # Search + Add row
        top_row = QHBoxLayout()

        self._term_search = QLineEdit()
        self._term_search.setPlaceholderText("Filter terms...")
        self._term_search.setStyleSheet(
            f"QLineEdit {{ border: 1px solid {ALMA_BORDER_LIGHT};"
            " border-radius: 6px; padding: 6px 10px; font-size: 12px;"
            f" background: {ALMA_WHITE}; }}"
        )
        self._term_search.textChanged.connect(self._filter_active_terms)
        top_row.addWidget(self._term_search, 1)

        add_btn = QPushButton("+ Add Term")
        add_btn.setCursor(Qt.PointingHandCursor)
        add_btn.setStyleSheet(f"""
            QPushButton {{
                background: {ALMA_GREEN_DARK}; color: {ALMA_TEXT_ON_DARK};
                font-size: 12px; font-weight: 600;
                border: none; border-radius: 6px;
                padding: 6px 14px;
            }}
            QPushButton:hover {{ background: {ALMA_GREEN_MID}; }}
        """)
        add_btn.clicked.connect(self._add_term_row)
        top_row.addWidget(add_btn)

        layout.addLayout(top_row)

        # Table — fixed height, no scroll
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
        self._active_table.horizontalHeader().resizeSection(5, 70)
        self._active_table.verticalHeader().setVisible(False)
        self._active_table.verticalHeader().setDefaultSectionSize(32)
        self._active_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._active_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._active_table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._active_table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._active_table.setStyleSheet(
            "QTableWidget { border: none; background: transparent; }"
        )
        configure_table(self._active_table)
        self._active_table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._resize_table(self._active_table, _ACTIVE_PAGE_SIZE, 32)
        layout.addWidget(self._active_table)

        # Pagination
        self._active_pager = PaginationBar(page_size=_ACTIVE_PAGE_SIZE)
        self._active_pager.page_changed.connect(self._on_active_page_changed)
        layout.addWidget(self._active_pager)

        self._populate_active_terms()
        return container

    def _populate_active_terms(self):
        """Fetch all terms from DB, cache, and show page 0."""
        self._active_all_data = self.db.get_user_terms()
        self._active_feedback = {}
        for row in self.db.get_feedback_summary():
            term = row["term"]
            self._active_feedback[term] = self._active_feedback.get(term, 0) + row["cnt"]

        # Apply filter text
        self._apply_active_filter()

    def _apply_active_filter(self):
        """Filter cached data by search text, then paginate."""
        text = self._term_search.text().strip().lower() if hasattr(self, '_term_search') else ""
        if text:
            self._active_filtered = [
                t for t in self._active_all_data
                if text in t["term"].lower()
            ]
        else:
            self._active_filtered = list(self._active_all_data)

        self._active_pager.set_total(len(self._active_filtered))
        self._show_active_page(0)

    def _filter_active_terms(self, text):
        """Search box changed — re-filter and paginate."""
        self._apply_active_filter()

    def _on_active_page_changed(self, page):
        self._show_active_page(page)

    def _show_active_page(self, page):
        """Render one page of active terms."""
        ps = _ACTIVE_PAGE_SIZE
        start = page * ps
        end = min(start + ps, len(self._active_filtered))
        page_items = self._active_filtered[start:end]

        self._active_table.setRowCount(len(page_items))
        for i, t in enumerate(page_items):
            self._active_table.setItem(i, 0, QTableWidgetItem(t["term"]))

            action_item = QTableWidgetItem(t["action"])
            color = (ALMA_SUCCESS if t["action"] == "promote"
                     else ALMA_ERROR if t["action"] == "demote"
                     else ALMA_INFO)
            action_item.setForeground(self._qcolor(color))
            action_item.setFont(QFont("Segoe UI", 11, QFont.Bold))
            self._active_table.setItem(i, 1, action_item)

            self._active_table.setItem(i, 2, QTableWidgetItem(f"{t['weight_modifier']:.1f}"))
            self._active_table.setItem(i, 3, QTableWidgetItem(
                str(self._active_feedback.get(t["term"], 0))
            ))
            self._active_table.setItem(i, 4, QTableWidgetItem(
                t["updated_at"][:10] if t.get("updated_at") else ""
            ))

            del_btn = QPushButton("Delete")
            del_btn.setStyleSheet(
                f"QPushButton {{ background: transparent; color: {ALMA_ERROR};"
                " font-size: 11px; font-weight: 600; border: none; }}"
                f" QPushButton:hover {{ text-decoration: underline; }}"
            )
            del_btn.setCursor(Qt.PointingHandCursor)
            term_text = t["term"]
            del_btn.clicked.connect(lambda _, tt=term_text: self._delete_term(tt))
            self._active_table.setCellWidget(i, 5, del_btn)

        self._resize_table(self._active_table, _ACTIVE_PAGE_SIZE, 32)

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
        container = QFrame()
        container.setObjectName("CandidatesCard")
        container.setStyleSheet(
            f"#CandidatesCard {{ background: {ALMA_BG_ELEVATED};"
            " border: none; border-radius: 12px; }}"
        )
        apply_card_shadow_soft(container)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(12, 10, 12, 10)

        info = QLabel(
            "Auto-discovered compound terms from PMI analysis. "
            "Approve to include in future analyses, or reject to suppress."
        )
        info.setStyleSheet(
            f"color: {ALMA_TEXT_MID}; font-size: 12px;"
            " background: transparent; border: none;"
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        # Table — fixed height, no scroll
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
        self._candidates_table.horizontalHeader().resizeSection(5, 130)
        self._candidates_table.verticalHeader().setVisible(False)
        self._candidates_table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._candidates_table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._candidates_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._candidates_table.setStyleSheet(
            "QTableWidget { border: none; background: transparent; }"
        )
        configure_table(self._candidates_table)
        # Row height AFTER configure_table — global stylesheet adds 20px
        # vertical padding, so 80px fits two 24px stacked buttons comfortably
        self._candidates_table.verticalHeader().setDefaultSectionSize(80)
        self._candidates_table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._resize_table(self._candidates_table, _CANDIDATES_PAGE_SIZE, 80)
        layout.addWidget(self._candidates_table)

        # Pagination
        self._candidates_pager = PaginationBar(page_size=_CANDIDATES_PAGE_SIZE)
        self._candidates_pager.page_changed.connect(self._on_candidates_page_changed)
        layout.addWidget(self._candidates_pager)

        self._populate_candidates()
        return container

    def _populate_candidates(self):
        """Fetch all candidates from DB, cache, and show page 0."""
        self._candidates_all_data = self.db.get_discovered_compounds(status="candidate")
        self._candidates_pager.set_total(len(self._candidates_all_data))
        self._show_candidates_page(0)

    def _on_candidates_page_changed(self, page):
        self._show_candidates_page(page)

    def _show_candidates_page(self, page):
        """Render one page of candidate rows."""
        from datetime import datetime, timedelta
        week_ago = (datetime.now() - timedelta(days=7)).isoformat()

        ps = _CANDIDATES_PAGE_SIZE
        start = page * ps
        end = min(start + ps, len(self._candidates_all_data))
        page_items = self._candidates_all_data[start:end]

        self._candidates_table.setRowCount(len(page_items))
        for i, c in enumerate(page_items):
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

            # Action buttons — stacked vertically, compact to fit cell
            btn_widget = QWidget()
            btn_widget.setStyleSheet("QWidget { background: transparent; border: none; }")
            btn_layout = QVBoxLayout(btn_widget)
            btn_layout.setContentsMargins(6, 4, 6, 4)
            btn_layout.setSpacing(3)

            approve_btn = QPushButton("Approve")
            approve_btn.setFixedHeight(24)
            approve_btn.setStyleSheet(f"""
                QPushButton {{
                    background: {ALMA_SUCCESS}; color: #FFFFFF;
                    font-size: 10px; font-weight: 700;
                    border: none; border-radius: 4px; padding: 1px 4px;
                }}
                QPushButton:hover {{ background: {ALMA_GREEN_SUBTLE}; }}
            """)
            approve_btn.setCursor(Qt.PointingHandCursor)
            phrase = c["phrase"]
            approve_btn.clicked.connect(lambda _, p=phrase: self._approve_compound(p))
            btn_layout.addWidget(approve_btn)

            reject_btn = QPushButton("Reject")
            reject_btn.setFixedHeight(24)
            reject_btn.setStyleSheet(f"""
                QPushButton {{
                    background: #FFFFFF; color: {ALMA_ERROR};
                    font-size: 10px; font-weight: 700;
                    border: 1.5px solid {ALMA_ERROR}; border-radius: 4px;
                    padding: 1px 4px;
                }}
                QPushButton:hover {{ background: #FFF0F0; }}
            """)
            reject_btn.setCursor(Qt.PointingHandCursor)
            reject_btn.clicked.connect(lambda _, p=phrase: self._reject_compound(p))
            btn_layout.addWidget(reject_btn)

            self._candidates_table.setCellWidget(i, 5, btn_widget)

        self._resize_table(self._candidates_table, _CANDIDATES_PAGE_SIZE, 80)

    def _approve_compound(self, phrase):
        self.db.update_compound_status(phrase, "approved")
        # Also promote into user_terms so it appears in Active Terms
        canonical = phrase.replace(" ", "_")
        self.db.upsert_user_term(
            phrase, canonical, "promote",
            weight_modifier=1.5,
            notes="Approved from candidate",
        )
        self._populate_active_terms()
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
        container = QFrame()
        container.setObjectName("AliasesCard")
        container.setStyleSheet(
            f"#AliasesCard {{ background: {ALMA_BG_ELEVATED};"
            " border: none; border-radius: 12px; }}"
        )
        apply_card_shadow_soft(container)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(12, 10, 12, 10)

        info = QLabel(
            "All compound terms: built-in (from Pass 1) + approved discovered terms + user-added. "
            "Add individual aliases or import from CSV. Toggle status to activate or deactivate."
        )
        info.setStyleSheet(
            f"color: {ALMA_TEXT_MID}; font-size: 12px;"
            " background: transparent; border: none;"
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        # Action bar: Add Alias + Import CSV
        action_row = QHBoxLayout()
        action_row.setSpacing(8)

        add_alias_btn = QPushButton("+ Add Alias")
        add_alias_btn.setCursor(Qt.PointingHandCursor)
        add_alias_btn.setStyleSheet(f"""
            QPushButton {{
                background: {ALMA_GREEN_DARK}; color: {ALMA_TEXT_ON_DARK};
                font-size: 12px; font-weight: 600;
                border: none; border-radius: 6px;
                padding: 8px 16px;
            }}
            QPushButton:hover {{ background: {ALMA_GREEN_MID}; }}
        """)
        add_alias_btn.clicked.connect(self._add_alias)
        action_row.addWidget(add_alias_btn)

        import_btn = QPushButton("Import CSV")
        import_btn.setCursor(Qt.PointingHandCursor)
        import_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {ALMA_GREEN_DARK};
                font-size: 12px; font-weight: 600;
                border: 1px solid {ALMA_GREEN_DARK}; border-radius: 6px;
                padding: 8px 16px;
            }}
            QPushButton:hover {{ background: {ALMA_CREAM}; }}
        """)
        import_btn.clicked.connect(self._import_aliases_csv)
        action_row.addWidget(import_btn)

        action_row.addStretch()
        layout.addLayout(action_row)

        # Table — fixed height, no scroll
        self._aliases_table = QTableWidget()
        self._aliases_table.setColumnCount(5)
        self._aliases_table.setHorizontalHeaderLabels([
            "Phrase", "Normalized", "Source", "Status", ""
        ])
        self._aliases_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self._aliases_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self._aliases_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self._aliases_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self._aliases_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Fixed)
        self._aliases_table.horizontalHeader().resizeSection(4, 100)
        self._aliases_table.verticalHeader().setVisible(False)
        self._aliases_table.verticalHeader().setDefaultSectionSize(34)
        self._aliases_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._aliases_table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._aliases_table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._aliases_table.setStyleSheet(
            "QTableWidget { border: none; background: transparent; }"
        )
        configure_table(self._aliases_table)
        self._aliases_table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._resize_table(self._aliases_table, _ALIASES_PAGE_SIZE, 34)
        layout.addWidget(self._aliases_table)

        # Pagination
        self._aliases_pager = PaginationBar(page_size=_ALIASES_PAGE_SIZE)
        self._aliases_pager.page_changed.connect(self._on_aliases_page_changed)
        layout.addWidget(self._aliases_pager)

        self._populate_aliases()
        return container

    def _populate_aliases(self):
        """Fetch all alias rows from DB + builtins, cache, and show page 0."""
        from src.data.trending_engine import COMPOUND_TERMS

        rows = []

        # Built-in terms (always active, no toggle)
        for phrase, normalized in sorted(COMPOUND_TERMS.items()):
            rows.append((phrase, normalized, "Built-in", "Active", False))

        # Discovered + approved
        approved = self.db.get_discovered_compounds(status="approved")
        for c in approved:
            rows.append((c["phrase"], c["normalized"], "Discovered", "Active", False))

        # User-added aliases (from user_terms with action='alias')
        user_aliases = self.db.get_user_terms(action="alias")
        for a in user_aliases:
            status = a.get("notes", "Active") or "Active"
            rows.append((
                a["term"], a["canonical_form"],
                "End User Added", status, True
            ))

        self._alias_all_rows = rows
        self._aliases_pager.set_total(len(rows))
        self._show_aliases_page(0)

    def _on_aliases_page_changed(self, page):
        self._show_aliases_page(page)

    def _show_aliases_page(self, page):
        """Render one page of alias rows."""
        ps = _ALIASES_PAGE_SIZE
        start = page * ps
        end = min(start + ps, len(self._alias_all_rows))
        page_items = self._alias_all_rows[start:end]

        self._aliases_table.setRowCount(len(page_items))
        for i, (phrase, normalized, source, status, toggleable) in enumerate(page_items):
            self._aliases_table.setItem(i, 0, QTableWidgetItem(phrase))
            self._aliases_table.setItem(i, 1, QTableWidgetItem(normalized))

            source_item = QTableWidgetItem(source)
            color_map = {
                "Built-in": ALMA_INFO,
                "Discovered": ALMA_SUCCESS,
                "End User Added": ALMA_GREEN_DARK,
            }
            source_item.setForeground(
                self._qcolor(color_map.get(source, ALMA_TEXT_MID))
            )
            source_item.setFont(QFont("Segoe UI", 11, QFont.Bold))
            self._aliases_table.setItem(i, 2, source_item)

            # Status with color
            status_item = QTableWidgetItem(status)
            if status == "Active":
                status_item.setForeground(self._qcolor(ALMA_SUCCESS))
            else:
                status_item.setForeground(self._qcolor(ALMA_TEXT_LIGHT))
            status_item.setFont(QFont("Segoe UI", 11, QFont.Bold))
            self._aliases_table.setItem(i, 3, status_item)

            # Toggle button (only for user-added)
            if toggleable:
                toggle_label = "Deactivate" if status == "Active" else "Activate"
                toggle_btn = QPushButton(toggle_label)
                toggle_btn.setStyleSheet(f"""
                    QPushButton {{
                        background: transparent;
                        color: {ALMA_GREEN_DARK if status != "Active" else ALMA_TEXT_MID};
                        font-size: 11px; font-weight: 600;
                        border: 1px solid {ALMA_BORDER_LIGHT}; border-radius: 5px;
                        padding: 3px 10px;
                    }}
                    QPushButton:hover {{ background: {ALMA_CREAM}; }}
                """)
                toggle_btn.setCursor(Qt.PointingHandCursor)
                p = phrase
                toggle_btn.clicked.connect(
                    lambda _, pp=p, s=status: self._toggle_alias_status(pp, s)
                )
                self._aliases_table.setCellWidget(i, 4, toggle_btn)

        self._resize_table(self._aliases_table, _ALIASES_PAGE_SIZE, 34)

    def _toggle_alias_status(self, phrase, current_status):
        """Toggle an alias between Active and Dormant."""
        new_status = "Dormant" if current_status == "Active" else "Active"
        # Update notes field to store status
        self.db.upsert_user_term(
            phrase, phrase.replace(" ", "_"), "alias",
            weight_modifier=1.0, notes=new_status,
        )
        self._populate_aliases()
        self.terms_changed.emit()

    def _add_alias(self):
        """Add a single alias via proper two-field dialog."""
        dlg = _AddAliasDialog(self)
        if dlg.exec() == QDialog.Accepted:
            phrase, normalized = dlg.get_values()
            if phrase and normalized:
                self.db.upsert_user_term(
                    phrase, normalized, "alias",
                    weight_modifier=1.0, notes="Active",
                )
                self._populate_aliases()
                self.terms_changed.emit()

    def _import_aliases_csv(self):
        """Import aliases from a CSV file (columns: phrase, normalized)."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Import Aliases CSV", "",
            "CSV Files (*.csv);;All Files (*)",
        )
        if not path:
            return

        import csv
        imported = 0
        try:
            with open(path, newline="", encoding="utf-8") as f:
                reader = csv.reader(f)
                next(reader, None)  # skip header
                for row in reader:
                    if len(row) >= 2:
                        phrase = row[0].strip().lower()
                        normalized = row[1].strip().lower()
                        if phrase and normalized:
                            self.db.upsert_user_term(
                                phrase, normalized, "alias",
                                weight_modifier=1.0, notes="Active",
                            )
                            imported += 1
        except Exception:
            pass

        if imported:
            self._populate_aliases()
            self.terms_changed.emit()
