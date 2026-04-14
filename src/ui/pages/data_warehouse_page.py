"""
Alma Insights — Data Warehouse Page
Browse all stored data across all sources with virtual scroll,
ticket detail panel, and TRC history panel.

Session 4: Multi-source persistent database architecture.

Layout:
  PageHeader → Filter bar → Results count → Virtual scroll table
  → Ticket detail panel → TRC history panel
  → Empty state (when no data)
"""

import logging

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QLineEdit, QFrame, QScrollArea, QSizePolicy,
    QDateEdit, QSplitter,
)
from PySide6.QtCore import Qt, QDate, Signal

from src.ui.theme import (
    ALMA_GREEN_DARK, ALMA_GREEN_LIGHT, ALMA_CREAM, ALMA_WHITE,
    ALMA_TEXT_DARK, ALMA_TEXT_MID, ALMA_TEXT_LIGHT, ALMA_BORDER,
    ALMA_BORDER_LIGHT, ALMA_BG_ELEVATED,
)
from src.ui.widgets.page_header import PageHeader
from src.ui.widgets.virtual_scroll_table import VirtualScrollTable
from src.ui.widgets.ticket_detail_panel import TicketDetailPanel
from src.ui.widgets.trc_history_panel import TRCHistoryPanel
from src.ui.widgets.source_selector import SourceSelector

logger = logging.getLogger("alma.ui.data_warehouse_page")


class DataWarehousePage(QWidget):
    """Data Warehouse — browse all stored ticket data with filters and detail views."""

    # Signal for drilldown panel integration
    scan_complete = Signal()

    def __init__(self, db_manager, parent=None):
        super().__init__(parent)
        self.db = db_manager
        self._drilldown = None
        self._wq = None
        self._build_ui()

    def set_drilldown_panel(self, panel):
        self._drilldown = panel

    # ═══════════════════════════════════════════
    #  UI CONSTRUCTION
    # ═══════════════════════════════════════════

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 16, 24, 16)
        layout.setSpacing(12)

        # Page header
        header = PageHeader(
            "Data Warehouse",
            "Browse all stored data across all sources",
        )
        self._ticket_count_badge = QLabel("0 tickets")
        self._ticket_count_badge.setStyleSheet(
            f"background: {ALMA_GREEN_DARK}; color: {ALMA_CREAM}; "
            f"padding: 4px 12px; border-radius: 10px; font-size: 12px; font-weight: 600;"
        )
        header.add_action(self._ticket_count_badge)
        layout.addWidget(header)

        # Filter bar
        layout.addWidget(self._build_filter_bar())

        # Results count
        self._results_label = QLabel("")
        self._results_label.setStyleSheet(f"font-size: 12px; color: {ALMA_TEXT_MID};")
        results_row = QHBoxLayout()
        results_row.addWidget(self._results_label)
        results_row.addStretch()
        layout.addLayout(results_row)

        # Scroll area for content below filters
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")

        scroll_content = QWidget()
        scroll_layout = QVBoxLayout(scroll_content)
        scroll_layout.setContentsMargins(0, 0, 0, 0)
        scroll_layout.setSpacing(12)

        # Virtual scroll table
        self._table_widget = VirtualScrollTable(parent=self)
        self._table_widget.setMinimumHeight(250)
        self._table_widget.row_selected.connect(self._on_ticket_selected)
        scroll_layout.addWidget(self._table_widget, 1)  # stretch factor = 1

        # Ticket detail panel (fixed height — tabs scroll internally)
        self._detail_panel = TicketDetailPanel(self)
        self._detail_panel.setMaximumHeight(260)
        scroll_layout.addWidget(self._detail_panel)

        # TRC history panel
        self._trc_panel = TRCHistoryPanel(self)
        scroll_layout.addWidget(self._trc_panel)

        # Empty state (shown when no data)
        self._empty_state = QWidget()
        empty_layout = QVBoxLayout(self._empty_state)
        empty_layout.setAlignment(Qt.AlignCenter)
        empty_layout.setContentsMargins(0, 40, 0, 40)
        empty_icon = QLabel("\U0001F5C4\ufe0f")
        empty_icon.setStyleSheet("font-size: 36px;")
        empty_icon.setAlignment(Qt.AlignCenter)
        empty_layout.addWidget(empty_icon)
        empty_title = QLabel("No data in warehouse")
        empty_title.setStyleSheet(
            f"font-size: 14px; font-weight: 600; color: {ALMA_TEXT_DARK};"
        )
        empty_title.setAlignment(Qt.AlignCenter)
        empty_layout.addWidget(empty_title)
        empty_hint = QLabel("Import data via Conversation Search to get started.")
        empty_hint.setStyleSheet(f"font-size: 12px; color: {ALMA_TEXT_MID};")
        empty_hint.setAlignment(Qt.AlignCenter)
        empty_layout.addWidget(empty_hint)
        self._empty_state.hide()
        scroll_layout.addWidget(self._empty_state)

        scroll_layout.addStretch()
        scroll.setWidget(scroll_content)
        layout.addWidget(scroll, 1)

    # ═══════════════════════════════════════════
    #  FILTER BAR
    # ═══════════════════════════════════════════

    def _build_filter_bar(self):
        bar = QFrame()
        bar.setStyleSheet(
            f"QFrame {{ background: {ALMA_WHITE}; border: 1px solid {ALMA_BORDER_LIGHT}; "
            f"border-radius: 6px; padding: 8px 12px; }}"
        )
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(8)

        lbl_style = f"font-size: 11px; color: {ALMA_TEXT_MID}; font-weight: 600;"
        input_style = (
            f"font-size: 12px; padding: 4px 6px; border: 1px solid {ALMA_BORDER}; "
            f"border-radius: 3px; background: {ALMA_WHITE};"
        )

        # Source
        lbl = QLabel("Source:")
        lbl.setStyleSheet(lbl_style)
        layout.addWidget(lbl)
        self._source_selector = SourceSelector(self)
        self._source_selector.setStyleSheet(input_style)
        self._source_selector.setFixedWidth(140)
        layout.addWidget(self._source_selector)

        # Date From
        lbl = QLabel("From:")
        lbl.setStyleSheet(lbl_style)
        layout.addWidget(lbl)
        self._date_from = QDateEdit()
        self._date_from.setCalendarPopup(True)
        self._date_from.setDate(QDate.currentDate().addYears(-2))
        self._date_from.setDisplayFormat("MMM d, yyyy")
        self._date_from.setStyleSheet(input_style)
        self._date_from.setFixedWidth(120)
        layout.addWidget(self._date_from)

        # Date To
        lbl = QLabel("To:")
        lbl.setStyleSheet(lbl_style)
        layout.addWidget(lbl)
        self._date_to = QDateEdit()
        self._date_to.setCalendarPopup(True)
        self._date_to.setDate(QDate.currentDate())
        self._date_to.setDisplayFormat("MMM d, yyyy")
        self._date_to.setStyleSheet(input_style)
        self._date_to.setFixedWidth(120)
        layout.addWidget(self._date_to)

        # TRC
        lbl = QLabel("TRC:")
        lbl.setStyleSheet(lbl_style)
        layout.addWidget(lbl)
        self._trc_combo = QComboBox()
        self._trc_combo.addItem("All TRCs", None)
        self._trc_combo.setStyleSheet(input_style)
        self._trc_combo.setFixedWidth(100)
        layout.addWidget(self._trc_combo)

        # Keyword
        lbl = QLabel("Keyword:")
        lbl.setStyleSheet(lbl_style)
        layout.addWidget(lbl)
        self._keyword_input = QLineEdit()
        self._keyword_input.setPlaceholderText("Search...")
        self._keyword_input.setStyleSheet(input_style)
        self._keyword_input.setFixedWidth(100)
        layout.addWidget(self._keyword_input)

        # ── Phase 1 enrichment filters ──
        # Insurance payer
        lbl = QLabel("Payer:")
        lbl.setStyleSheet(lbl_style)
        layout.addWidget(lbl)
        self._payer_combo = QComboBox()
        self._payer_combo.addItem("All payers", None)
        self._payer_combo.setStyleSheet(input_style)
        self._payer_combo.setFixedWidth(140)
        layout.addWidget(self._payer_combo)

        # Service state
        lbl = QLabel("State:")
        lbl.setStyleSheet(lbl_style)
        layout.addWidget(lbl)
        self._state_combo = QComboBox()
        self._state_combo.addItem("All states", None)
        self._state_combo.setStyleSheet(input_style)
        self._state_combo.setFixedWidth(80)
        layout.addWidget(self._state_combo)

        # Tag
        lbl = QLabel("Tag:")
        lbl.setStyleSheet(lbl_style)
        layout.addWidget(lbl)
        self._tag_combo = QComboBox()
        self._tag_combo.addItem("All tags", None)
        self._tag_combo.setStyleSheet(input_style)
        self._tag_combo.setFixedWidth(180)
        layout.addWidget(self._tag_combo)

        # Search button
        search_btn = QPushButton("\U0001F50D Search")
        search_btn.setStyleSheet(
            f"QPushButton {{ background: {ALMA_GREEN_DARK}; color: {ALMA_CREAM}; "
            f"padding: 5px 12px; border-radius: 4px; font-size: 12px; font-weight: 600; "
            f"border: none; }}"
            f"QPushButton:hover {{ background: {ALMA_GREEN_LIGHT}; }}"
        )
        search_btn.setCursor(Qt.PointingHandCursor)
        search_btn.clicked.connect(self._apply_filters)
        layout.addWidget(search_btn)

        # Auto-apply filters on change (Bug 4 fix)
        self._source_selector.source_changed.connect(self._apply_filters)
        self._date_from.dateChanged.connect(self._apply_filters)
        self._date_to.dateChanged.connect(self._apply_filters)
        self._trc_combo.currentIndexChanged.connect(self._apply_filters)
        self._keyword_input.returnPressed.connect(self._apply_filters)
        self._payer_combo.currentIndexChanged.connect(self._apply_filters)
        self._state_combo.currentIndexChanged.connect(self._apply_filters)
        self._tag_combo.currentIndexChanged.connect(self._apply_filters)

        layout.addStretch()
        return bar

    # ═══════════════════════════════════════════
    #  DATA LOADING
    # ═══════════════════════════════════════════

    def refresh_data(self):
        """Load/reload data into the warehouse page.

        Called when the page becomes visible or data changes.
        """
        self._ensure_wq()
        if not self._wq:
            self._show_empty_state()
            return

        # Refresh source selector
        try:
            from src.data.connection_factory import get_connection
            conn = get_connection(self.db.db_path)
            self._source_selector.refresh_sources(conn)
            conn.close()
        except Exception:
            pass

        # Populate TRC combo
        self._populate_trc_combo()

        # Populate enrichment combos (Phase 1)
        self._populate_enrichment_combos()

        # Set warehouse query on table
        self._table_widget.set_warehouse_query(self._wq)

        # Apply current filters
        self._apply_filters()

    def _ensure_wq(self):
        """Create WarehouseQuery instance if not already set."""
        if self._wq:
            return
        try:
            from src.data.source_registry import SourceRegistry
            from src.data.warehouse_query import WarehouseQuery
            from src.data.connection_factory import get_connection

            conn = get_connection(self.db.db_path)
            registry = SourceRegistry(conn)
            self._wq = WarehouseQuery(conn, registry)
        except Exception as e:
            logger.warning("Could not create WarehouseQuery: %s", e)
            self._wq = None

    def _populate_trc_combo(self):
        """Fill the TRC dropdown with available TRC codes."""
        self._trc_combo.blockSignals(True)
        current = self._trc_combo.currentData()
        self._trc_combo.clear()
        self._trc_combo.addItem("All TRCs", None)

        if self._wq:
            try:
                dist = self._wq.get_trc_distribution()
                for code in sorted(dist.keys()):
                    if code and code != "Unknown":
                        self._trc_combo.addItem(f"{code} ({dist[code]})", code)
            except Exception:
                pass

        # Restore previous selection
        for i in range(self._trc_combo.count()):
            if self._trc_combo.itemData(i) == current:
                self._trc_combo.setCurrentIndex(i)
                break

        self._trc_combo.blockSignals(False)

    def _populate_enrichment_combos(self):
        """Fill Payer / State / Tag dropdowns from current DB (Phase 1)."""
        from src.data.connection_factory import get_connection
        try:
            conn = get_connection(self.db.db_path, readonly=True)
        except Exception as exc:
            logger.warning("populate_enrichment_combos: cannot open DB: %s", exc)
            return
        try:
            self._populate_combo_from_query(
                self._payer_combo, conn,
                "SELECT payer_id, ticket_count FROM insurance_payers "
                "WHERE payer_id IS NOT NULL ORDER BY payer_id",
                label_fmt=lambda r: r[0],
                value_fn=lambda r: r[0],
                empty_label="All payers",
            )
            self._populate_combo_from_query(
                self._state_combo, conn,
                "SELECT DISTINCT service_state FROM ticket_index "
                "WHERE service_state IS NOT NULL AND service_state != '' "
                "ORDER BY service_state",
                label_fmt=lambda r: r[0],
                value_fn=lambda r: r[0],
                empty_label="All states",
            )
            self._populate_combo_from_query(
                self._tag_combo, conn,
                "SELECT tag, COUNT(*) FROM ticket_tags "
                "GROUP BY tag ORDER BY COUNT(*) DESC, tag",
                label_fmt=lambda r: f"{r[0]} ({r[1]})",
                value_fn=lambda r: r[0],
                empty_label="All tags",
            )
        except Exception as exc:
            logger.warning("populate_enrichment_combos failed: %s", exc)
        finally:
            conn.close()

    @staticmethod
    def _populate_combo_from_query(combo, conn, sql, *, label_fmt, value_fn,
                                    empty_label):
        """Helper: reset combo, add empty item, append rows from `sql`."""
        combo.blockSignals(True)
        current = combo.currentData()
        combo.clear()
        combo.addItem(empty_label, None)
        try:
            for row in conn.execute(sql).fetchall():
                combo.addItem(label_fmt(row), value_fn(row))
        except Exception:
            pass
        # Restore previous selection if still present
        for i in range(combo.count()):
            if combo.itemData(i) == current:
                combo.setCurrentIndex(i)
                break
        combo.blockSignals(False)

    def _apply_filters(self):
        """Read filter bar values and refresh the table."""
        if not self._wq:
            self._show_empty_state()
            return

        filters = {}

        # Source
        source_id = self._source_selector.selected_source_id()
        if source_id:
            filters["source_id"] = source_id

        # Dates
        filters["date_start"] = self._date_from.date().toString("yyyy-MM-dd")
        filters["date_end"] = self._date_to.date().toString("yyyy-MM-dd")

        # TRC
        trc = self._trc_combo.currentData()
        if trc:
            filters["trc_filter"] = trc

        # Keyword
        keyword = self._keyword_input.text().strip()
        if keyword:
            filters["keyword"] = keyword

        # Phase 1 enrichment filters
        payer = self._payer_combo.currentData()
        if payer:
            filters["insurance_payer"] = payer
        state = self._state_combo.currentData()
        if state:
            filters["service_state"] = state
        tag = self._tag_combo.currentData()
        if tag:
            filters["tag"] = tag

        self._table_widget.set_filters(**filters)

        # Update results label
        model = self._table_widget.model
        total = model.total_count
        loaded = model.loaded_count

        if total == 0:
            self._show_empty_state()
        else:
            self._hide_empty_state()
            self._results_label.setText(
                f"Results: <b>{total:,} tickets</b>"
            )
            self._results_label.setTextFormat(Qt.RichText)

        self._ticket_count_badge.setText(f"{total:,} tickets")

        # Clear detail panels
        self._detail_panel.clear()
        self._trc_panel.clear()

    def _show_empty_state(self):
        self._empty_state.show()
        self._table_widget.hide()
        self._detail_panel.hide()
        self._trc_panel.hide()
        self._results_label.setText("")
        self._ticket_count_badge.setText("0 tickets")

    def _hide_empty_state(self):
        self._empty_state.hide()
        self._table_widget.show()

    # ═══════════════════════════════════════════
    #  TICKET SELECTION
    # ═══════════════════════════════════════════

    def _on_ticket_selected(self, row_data: dict):
        """Handle ticket row selection — populate detail + TRC history."""
        if not row_data:
            self._detail_panel.clear()
            self._trc_panel.clear()
            return

        # Ticket detail — enrich row_data with NLP fields for Overview tab
        nlp_data = self._fetch_nlp_data(row_data.get("ticket_id"))
        if nlp_data:
            if nlp_data.get("sentiment_polarity") and not row_data.get("sentiment_score"):
                polarity = nlp_data["sentiment_polarity"]
                intensity = nlp_data.get("sentiment_intensity")
                # Map polarity string to numeric score for _format_sentiment()
                score_map = {"positive": 0.5, "negative": -0.5, "neutral": 0.0, "mixed": 0.1}
                base = score_map.get(str(polarity).lower(), 0.0)
                if intensity is not None:
                    try:
                        base = base * (int(intensity) / 5.0) if base != 0 else 0
                    except (ValueError, TypeError):
                        pass
                row_data["sentiment_score"] = base
            if nlp_data.get("friction_type") and not row_data.get("friction"):
                row_data["friction"] = nlp_data["friction_type"]
        # Fetch related tickets (same TRC) with NLP enrichment
        related_tickets = self._fetch_related_tickets(row_data)
        # Fetch comments for timeline
        comments = self._fetch_comments(row_data.get("ticket_id"))
        self._detail_panel.set_ticket_data(row_data, nlp_data, related_tickets, comments)

        # TRC history
        trc_code = row_data.get("trc_code")
        if trc_code and self._wq:
            source_id = self._source_selector.selected_source_id()
            history = self._wq.get_trc_history(trc_code, source_id=source_id)
            self._trc_panel.set_trc_data(trc_code, history)
        else:
            self._trc_panel.clear()

    def _fetch_nlp_data(self, ticket_id):
        """Fetch NLP enrichment data for a ticket from nlp_ticket_classifications."""
        if not ticket_id:
            return None
        try:
            conn = self.db.conn
            row = conn.execute(
                """SELECT trc, sub_cluster, sentiment_polarity, sentiment_intensity,
                          friction_type, anomaly_flag, anomaly_reason,
                          entities_json, key_phrases, root_cause_hint, summary,
                          created_at
                   FROM nlp_ticket_classifications
                   WHERE ticket_id = ? ORDER BY created_at DESC LIMIT 1""",
                (ticket_id,),
            ).fetchone()
            if not row:
                return None

            import json
            entities = []
            if row[7]:  # entities_json
                try:
                    ej = json.loads(row[7])
                    if isinstance(ej, dict):
                        entities = [f"{k}: {v}" for k, v in ej.items() if v]
                    elif isinstance(ej, list):
                        entities = ej
                except (json.JSONDecodeError, TypeError):
                    pass

            ngrams = []
            if row[8]:  # key_phrases
                ngrams = [p.strip() for p in row[8].split(",") if p.strip()]

            issue_types = []
            if row[4]:  # friction_type
                issue_types.append(row[4])
            if row[5]:  # anomaly_flag
                issue_types.append(f"Anomaly: {row[5]}")

            return {
                "trc": row[0],
                "sub_cluster": row[1],
                "sentiment_polarity": row[2],
                "sentiment_intensity": row[3],
                "friction_type": row[4],
                "anomaly_flag": row[5],
                "anomaly_reason": row[6],
                "root_cause_hint": row[9],
                "summary": row[10],
                "classified_at": row[11],
                "ngrams": ngrams,
                "issue_types": issue_types,
                "entities": entities,
            }
        except Exception:
            pass
        return None

    # ── Embedding cache for Related tab (lazy-loaded per session) ──
    _embedding_cache = None  # (matrix, ticket_ids) tuple

    def _get_embeddings(self):
        """Load Qwen 3 embeddings from ticket_embeddings table (cached)."""
        if self._embedding_cache is not None:
            return self._embedding_cache
        try:
            from src.data.embedding.search import load_embedding_cache
            matrix, ids = load_embedding_cache(self.db.conn)
            if len(ids) > 0:
                self._embedding_cache = (matrix, ids)
                return self._embedding_cache
        except Exception:
            pass
        self._embedding_cache = (None, [])
        return self._embedding_cache

    def _fetch_related_tickets(self, row_data):
        """Find semantically related tickets via Qwen 3 cosine similarity.

        Uses pre-computed 1024-dim embeddings to find tickets with the
        most similar service issues — no reliance on TRC codes or
        fragmented sub_cluster labels.
        """
        tid = row_data.get("ticket_id")
        if not tid:
            return []
        try:
            matrix, corpus_ids = self._get_embeddings()
            if matrix is None or len(corpus_ids) == 0:
                return []

            # Find this ticket's embedding
            try:
                idx = corpus_ids.index(tid)
            except ValueError:
                return []

            query_vec = matrix[idx]

            from src.data.embedding.search import semantic_search
            matches = semantic_search(
                query_embedding=query_vec,
                corpus_embeddings=matrix,
                corpus_ids=corpus_ids,
                top_k=16,        # 15 + self
                min_similarity=0.75,
            )

            # Remove self from results
            matches = [m for m in matches if m["ticket_id"] != tid][:15]

            if not matches:
                return []

            # Set the display label from sub_cluster
            conn = self.db.conn
            sc_row = conn.execute(
                """SELECT sub_cluster FROM nlp_ticket_classifications
                   WHERE ticket_id = ? AND sub_cluster IS NOT NULL
                   ORDER BY created_at DESC LIMIT 1""",
                (tid,),
            ).fetchone()
            if sc_row:
                row_data["_related_group"] = sc_row[0]

            # Enrich matches with ticket metadata
            match_ids = [m["ticket_id"] for m in matches]
            sim_map = {m["ticket_id"]: m["similarity"] for m in matches}
            placeholders = ", ".join("?" for _ in match_ids)

            rows = conn.execute(
                f"""SELECT n.ticket_id, n.trc, n.sub_cluster,
                           n.sentiment_polarity, n.friction_type, n.summary,
                           t.subject_sanitized, t.trc_label,
                           t.ticket_created_date
                    FROM nlp_ticket_classifications n
                    LEFT JOIN ticket_index t ON t.ticket_id = n.ticket_id
                    WHERE n.ticket_id IN ({placeholders})
                          AND n.created_at = (
                              SELECT MAX(n2.created_at)
                              FROM nlp_ticket_classifications n2
                              WHERE n2.ticket_id = n.ticket_id
                          )
                    ORDER BY n.ticket_id""",
                match_ids,
            ).fetchall()

            results = []
            for r in rows:
                results.append({
                    "ticket_id": r[0],
                    "trc_code": r[1],
                    "sub_cluster": r[2],
                    "sentiment_polarity": r[3] or "",
                    "friction": r[4] or "",
                    "summary": r[5] or "",
                    "subject": r[6] or "",
                    "status": r[7] or "",
                    "csat_score": None,
                    "created_at": r[8] or "",
                    "similarity": sim_map.get(r[0], 0),
                })
            # Sort by similarity descending
            results.sort(key=lambda x: -x.get("similarity", 0))
            return results
        except Exception:
            return []

    def _fetch_comments(self, ticket_id):
        """Fetch conversation comments/messages for the timeline tab."""
        if not ticket_id:
            return []
        try:
            conn = self.db.conn
            rows = conn.execute(
                """SELECT author_name, author_role, body, created_at
                   FROM comments
                   WHERE ticket_id = ?
                   ORDER BY created_at ASC""",
                (ticket_id,),
            ).fetchall()
            return [
                {
                    "author_name": r[0] or "Unknown",
                    "author_role": r[1] or "",
                    "body": r[2] or "",
                    "created_at": r[3] or "",
                }
                for r in rows
            ]
        except Exception:
            return []

    # ═══════════════════════════════════════════
    #  PAGE LIFECYCLE
    # ═══════════════════════════════════════════

    def showEvent(self, event):
        """Refresh data when page becomes visible."""
        super().showEvent(event)
        self.refresh_data()
