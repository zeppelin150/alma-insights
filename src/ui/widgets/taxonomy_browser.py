"""
Alma Insights -- Taxonomy Browser Widget (Pass 5.1)

Tab 3 of the NLP Scanner page. Provides:
  - Health stat cards (active, probationary, dormant patterns, avg n-grams)
  - Pattern growth line chart (top 10 patterns over time)
  - Sub-pattern taxonomy table (embedded QTreeWidget, clickable)
  - Latest findings section (moved from scanner tab)

Clicking a taxonomy row emits pattern_selected → drives drilldown panel.
"""

import json as _json
import logging

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
    QScrollArea, QTreeWidget, QTreeWidgetItem, QPushButton,
    QHeaderView, QSizePolicy,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont

from src.ui.theme import (
    ALMA_GREEN_DARK, ALMA_GREEN_LIGHT, ALMA_GREEN_MID, ALMA_GREEN_SUBTLE,
    ALMA_WHITE, ALMA_CREAM, ALMA_TEXT_DARK, ALMA_TEXT_MID,
    ALMA_TEXT_LIGHT, ALMA_TEXT_ON_DARK, ALMA_BORDER, ALMA_BORDER_LIGHT,
    ALMA_BG_ELEVATED, ALMA_SUCCESS, ALMA_WARNING, ALMA_ERROR, ALMA_INFO,
    apply_card_shadow_soft,
)
from src.ui.widgets.charts import LineChartWidget
from src.ui.widgets.empty_state import EmptyState

logger = logging.getLogger("alma.taxonomy_browser")


class TaxonomyBrowser(QWidget):
    """
    Full Tab 3 content: health stats, pattern growth, taxonomy table, findings.
    """

    # Emitted when user clicks a sub-pattern row
    pattern_selected = Signal(dict)
    # Forwarded from findings section
    deep_dive_requested = Signal(str, str)  # (finding_id, finding_title)
    view_tickets_requested = Signal(list)   # ticket_ids

    def __init__(self, db_manager, parent=None):
        super().__init__(parent)
        self.db = db_manager
        self._build_ui()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(16)

        # ── Health Stats Row ──
        self._build_health_stats(layout)

        # ── Pattern Growth Chart ──
        self._build_growth_chart(layout)

        # ── Taxonomy Table ──
        self._build_taxonomy_table(layout)

        # ── Latest Findings ──
        self._build_findings_section(layout)

        layout.addStretch()
        scroll.setWidget(content)
        outer.addWidget(scroll)

    def _build_health_stats(self, parent_layout):
        """Build 4 health stat KPI cards."""
        row_widget = QWidget()
        row = QHBoxLayout(row_widget)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)

        self._active_card = self._make_kpi_card("Active Patterns", "--", "patterns")
        row.addWidget(self._active_card["frame"], 1)

        self._prob_card = self._make_kpi_card("Probationary", "--", "patterns")
        row.addWidget(self._prob_card["frame"], 1)

        self._dormant_card = self._make_kpi_card("Dormant", "--", "patterns")
        row.addWidget(self._dormant_card["frame"], 1)

        self._ngram_card = self._make_kpi_card("Avg N-grams", "--", "per pattern")
        row.addWidget(self._ngram_card["frame"], 1)

        parent_layout.addWidget(row_widget)

    def _build_growth_chart(self, parent_layout):
        """Build pattern growth line chart."""
        card = QFrame()
        card.setStyleSheet(f"""
            QFrame {{
                background: {ALMA_BG_ELEVATED};
                border: 1px solid rgba(214, 210, 202, 0.45);
                border-radius: 12px;
            }}
        """)
        apply_card_shadow_soft(card)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(16, 14, 16, 14)
        card_layout.setSpacing(8)

        # Title row with filter buttons
        title_row = QHBoxLayout()
        title = QLabel("Pattern Growth")
        title.setStyleSheet(
            f"font-size: 14px; font-weight: 700; color: {ALMA_TEXT_DARK}; border: none;"
        )
        title_row.addWidget(title)
        title_row.addStretch()

        self._filter_all = QPushButton("All")
        self._filter_active = QPushButton("Active")
        self._filter_prob = QPushButton("Probationary")
        for btn in [self._filter_all, self._filter_active, self._filter_prob]:
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; color: {ALMA_TEXT_MID};
                    border: 1px solid {ALMA_BORDER}; border-radius: 4px;
                    padding: 3px 10px; font-size: 10px; font-weight: 600;
                }}
                QPushButton:hover {{ background: {ALMA_GREEN_SUBTLE}; }}
                QPushButton:checked {{ background: {ALMA_GREEN_DARK}; color: white; border: none; }}
            """)
            btn.setCheckable(True)
            title_row.addWidget(btn)

        self._filter_all.setChecked(True)
        self._growth_tier_filter = "all"
        self._filter_all.clicked.connect(lambda: self._set_growth_filter("all"))
        self._filter_active.clicked.connect(lambda: self._set_growth_filter("active"))
        self._filter_prob.clicked.connect(lambda: self._set_growth_filter("probationary"))
        card_layout.addLayout(title_row)

        self._growth_chart = LineChartWidget()
        self._growth_chart.setMinimumHeight(250)
        card_layout.addWidget(self._growth_chart)

        subtitle = QLabel("Top 10 patterns by cumulative ticket count")
        subtitle.setStyleSheet(
            f"font-size: 10px; color: {ALMA_TEXT_LIGHT}; border: none;"
        )
        subtitle.setAlignment(Qt.AlignCenter)
        card_layout.addWidget(subtitle)

        parent_layout.addWidget(card)

    def _build_taxonomy_table(self, parent_layout):
        """Build embedded QTreeWidget taxonomy table."""
        card = QFrame()
        card.setStyleSheet(f"""
            QFrame {{
                background: {ALMA_BG_ELEVATED};
                border: 1px solid rgba(214, 210, 202, 0.45);
                border-radius: 12px;
            }}
        """)
        apply_card_shadow_soft(card)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(16, 14, 16, 14)
        card_layout.setSpacing(8)

        title = QLabel("Sub-Pattern Taxonomy")
        title.setStyleSheet(
            f"font-size: 14px; font-weight: 700; color: {ALMA_TEXT_DARK}; border: none;"
        )
        card_layout.addWidget(title)

        self._taxonomy_tree = QTreeWidget()
        self._taxonomy_tree.setHeaderLabels([
            "Pattern", "Tier", "TRC", "Tickets", "Top N-grams"
        ])
        self._taxonomy_tree.setColumnWidth(0, 240)
        self._taxonomy_tree.setColumnWidth(1, 90)
        self._taxonomy_tree.setColumnWidth(2, 120)
        self._taxonomy_tree.setColumnWidth(3, 70)
        self._taxonomy_tree.setAlternatingRowColors(True)
        self._taxonomy_tree.setMinimumHeight(300)
        self._taxonomy_tree.setStyleSheet(f"""
            QTreeWidget {{
                background: {ALMA_WHITE}; border: 1px solid {ALMA_BORDER_LIGHT};
                border-radius: 6px; font-size: 12px;
            }}
            QTreeWidget::item {{
                padding: 4px 2px;
            }}
            QTreeWidget::item:selected {{
                background: {ALMA_GREEN_SUBTLE};
                color: {ALMA_TEXT_DARK};
            }}
            QHeaderView::section {{
                background: {ALMA_CREAM}; border: none;
                padding: 4px 8px; font-weight: 600; font-size: 11px;
                color: {ALMA_TEXT_DARK};
            }}
        """)
        self._taxonomy_tree.itemClicked.connect(self._on_taxonomy_row_clicked)
        card_layout.addWidget(self._taxonomy_tree)

        parent_layout.addWidget(card)

    def _build_findings_section(self, parent_layout):
        """Build latest findings section (moved from scanner tab)."""
        card = QFrame()
        card.setStyleSheet(f"""
            QFrame {{
                background: {ALMA_BG_ELEVATED};
                border: 1px solid rgba(214, 210, 202, 0.45);
                border-radius: 12px;
            }}
        """)
        apply_card_shadow_soft(card)
        self._findings_card_layout = QVBoxLayout(card)
        self._findings_card_layout.setContentsMargins(16, 14, 16, 14)
        self._findings_card_layout.setSpacing(8)

        title = QLabel("Latest Findings")
        title.setStyleSheet(
            f"font-size: 14px; font-weight: 700; color: {ALMA_TEXT_DARK}; border: none;"
        )
        self._findings_card_layout.addWidget(title)

        self._findings_container = QWidget()
        self._findings_layout = QVBoxLayout(self._findings_container)
        self._findings_layout.setContentsMargins(0, 0, 0, 0)
        self._findings_layout.setSpacing(8)
        self._findings_card_layout.addWidget(self._findings_container)

        parent_layout.addWidget(card)

    # ── Refresh ──

    def refresh(self):
        """Reload all sections from DB."""
        self._refresh_health_stats()
        self._refresh_growth_chart()
        self._refresh_taxonomy()
        self._refresh_findings()

    def _refresh_health_stats(self):
        """Update health stat KPI cards."""
        try:
            # Active patterns
            active = self.db.conn.execute(
                "SELECT COUNT(*) FROM sub_patterns WHERE tier = 'active' AND merged_into IS NULL"
            ).fetchone()[0]
            self._active_card["value"].setText(str(active))

            # Probationary
            prob = self.db.conn.execute(
                "SELECT COUNT(*) FROM sub_patterns WHERE tier = 'probationary' AND merged_into IS NULL"
            ).fetchone()[0]
            self._prob_card["value"].setText(str(prob))

            # Dormant
            dormant = self.db.conn.execute(
                "SELECT COUNT(*) FROM sub_patterns WHERE tier = 'dormant' AND merged_into IS NULL"
            ).fetchone()[0]
            self._dormant_card["value"].setText(str(dormant))

            # Avg n-grams per pattern
            try:
                avg_ngrams = self.db.conn.execute("""
                    SELECT AVG(cnt) FROM (
                        SELECT COUNT(*) as cnt FROM sub_pattern_ngrams
                        GROUP BY pattern_id
                    )
                """).fetchone()[0]
                self._ngram_card["value"].setText(
                    f"{avg_ngrams:.1f}" if avg_ngrams else "0"
                )
            except Exception:
                self._ngram_card["value"].setText("--")

        except Exception as e:
            logger.debug(f"Health stats refresh: {e}")

    def _refresh_growth_chart(self):
        """Update pattern growth line chart."""
        try:
            tier_filter = self._growth_tier_filter
            tier_clause = ""
            if tier_filter == "active":
                tier_clause = "AND sp.tier = 'active'"
            elif tier_filter == "probationary":
                tier_clause = "AND sp.tier = 'probationary'"

            # Get top 10 patterns by lifetime tickets
            rows = self.db.conn.execute(f"""
                SELECT sp.pattern_id, sp.label, sp.lifetime_tickets
                FROM sub_patterns sp
                WHERE sp.merged_into IS NULL {tier_clause}
                ORDER BY sp.lifetime_tickets DESC
                LIMIT 10
            """).fetchall()

            if not rows:
                self._growth_chart.set_data([], [])
                return

            # Get snapshot data for these patterns
            pattern_ids = [r["pattern_id"] for r in rows]
            pattern_labels = {r["pattern_id"]: r["label"][:25] for r in rows}

            # Build line data: one series per pattern
            all_dates = set()
            series_data = {}
            for pid in pattern_ids:
                snapshots = self.db.conn.execute("""
                    SELECT created_at, ticket_count
                    FROM sub_pattern_snapshots
                    WHERE pattern_id = ?
                    ORDER BY created_at
                """, (pid,)).fetchall()

                if snapshots:
                    dates = []
                    values = []
                    for snap in snapshots:
                        date_str = snap["created_at"][:10]
                        dates.append(date_str)
                        values.append(snap["ticket_count"] or 0)
                        all_dates.add(date_str)
                    series_data[pid] = (dates, values)

            if not series_data:
                self._growth_chart.set_data([], [])
                return

            # For simplicity, use the first pattern's data as the chart
            # LineChartWidget expects a single series
            sorted_dates = sorted(all_dates)
            # Show the top pattern's growth
            first_pid = pattern_ids[0]
            if first_pid in series_data:
                dates, values = series_data[first_pid]
                self._growth_chart.set_data(values, dates)

        except Exception as e:
            logger.debug(f"Growth chart refresh: {e}")

    def _refresh_taxonomy(self):
        """Reload taxonomy tree from sub_patterns."""
        self._taxonomy_tree.clear()

        try:
            patterns = self.db.conn.execute("""
                SELECT * FROM sub_patterns
                WHERE merged_into IS NULL
                ORDER BY trc, tier, lifetime_tickets DESC
            """).fetchall()

            trc_items = {}
            for p in patterns:
                p = dict(p)
                trc_raw = p.get("trc", "Unknown")

                # Parse TRC label
                trc_label = trc_raw
                if isinstance(trc_raw, str) and trc_raw.strip().startswith("["):
                    try:
                        trc_list = _json.loads(trc_raw)
                        if isinstance(trc_list, list) and len(trc_list) > 0:
                            if len(trc_list) <= 3:
                                trc_label = " | ".join(trc_list)
                            else:
                                trc_label = f"{trc_list[0]} (+{len(trc_list) - 1} more)"
                    except (_json.JSONDecodeError, TypeError):
                        pass

                if trc_raw not in trc_items:
                    trc_item = QTreeWidgetItem([trc_label, "", "", "", ""])
                    trc_item.setExpanded(True)
                    font = trc_item.font(0)
                    font.setBold(True)
                    trc_item.setFont(0, font)
                    self._taxonomy_tree.addTopLevelItem(trc_item)
                    trc_items[trc_raw] = trc_item

                # Get top n-grams
                try:
                    ngrams = self.db.get_sub_pattern_ngrams(
                        p["pattern_id"], min_specificity=0.1
                    )
                    ngram_str = ", ".join(
                        ng["ngram"] for ng in (ngrams or [])[:5]
                    )
                except Exception:
                    ngram_str = ""

                tier = p.get("tier", "")
                child = QTreeWidgetItem([
                    p.get("label", ""),
                    tier,
                    trc_label[:20],
                    str(p.get("lifetime_tickets", 0)),
                    ngram_str,
                ])
                # Store pattern data for click handling
                child.setData(0, Qt.UserRole, p)
                trc_items[trc_raw].addChild(child)

            # Update parent items with child count
            for trc_raw, trc_item in trc_items.items():
                count = trc_item.childCount()
                current = trc_item.text(0)
                trc_item.setText(0, f"{current}  [{count} patterns]")

        except Exception as e:
            self._taxonomy_tree.addTopLevelItem(
                QTreeWidgetItem([f"Error: {e}"])
            )

    def _refresh_findings(self):
        """Load and display findings for latest scan."""
        # Clear existing
        while self._findings_layout.count():
            item = self._findings_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        scan_id = None
        try:
            scan = self.db.get_latest_completed_scan()
            if scan:
                scan_id = scan.get("scan_id")
        except Exception:
            pass

        if not scan_id:
            self._findings_layout.addWidget(
                EmptyState("No findings yet -- run a scan", icon="search")
            )
            return

        try:
            findings = self.db.get_scan_findings(scan_id, limit=10)
        except Exception:
            findings = []

        if not findings:
            self._findings_layout.addWidget(
                EmptyState("No findings for this scan", icon="search")
            )
            return

        for f in findings:
            self._findings_layout.addWidget(self._build_finding_row(f))

    def _build_finding_row(self, finding):
        """Build a single finding display row (ported from nlp_scanner_page.py)."""
        row = QFrame()
        row.setStyleSheet(f"""
            QFrame {{
                background: {ALMA_WHITE}; border: 1px solid {ALMA_BORDER_LIGHT};
                border-radius: 8px; padding: 4px;
            }}
            QFrame:hover {{ border-color: {ALMA_GREEN_MID}; }}
        """)

        rl = QVBoxLayout(row)
        rl.setContentsMargins(12, 10, 12, 10)
        rl.setSpacing(4)

        # Title row with badge
        title_row = QHBoxLayout()
        impact = finding.get("impact_score", 0) or 0
        scope = finding.get("scope", "")

        if impact > 0.5:
            badge = QLabel("CRITICAL")
            badge.setStyleSheet(
                f"background: {ALMA_ERROR}; color: white; "
                "border-radius: 4px; padding: 2px 6px; font-size: 10px; font-weight: 700;"
            )
        elif "cross_trc" in (scope or ""):
            badge = QLabel("CROSS-TRC")
            badge.setStyleSheet(
                f"background: {ALMA_WARNING}; color: white; "
                "border-radius: 4px; padding: 2px 6px; font-size: 10px; font-weight: 700;"
            )
        else:
            badge = QLabel("NEW")
            badge.setStyleSheet(
                f"background: {ALMA_INFO}; color: white; "
                "border-radius: 4px; padding: 2px 6px; font-size: 10px; font-weight: 700;"
            )

        badge.setFixedHeight(20)
        title_row.addWidget(badge)

        title = QLabel(finding.get("title", "Untitled"))
        title.setStyleSheet(
            f"font-size: 13px; font-weight: 600; color: {ALMA_TEXT_DARK}; border: none;"
        )
        title_row.addWidget(title, 1)
        rl.addLayout(title_row)

        # Details line
        trcs_raw = finding.get("top_trcs", "[]")
        try:
            trcs = _json.loads(trcs_raw) if isinstance(trcs_raw, str) else trcs_raw
            trc_str = ", ".join(
                t.get("trc", t) if isinstance(t, dict) else str(t)
                for t in (trcs or [])[:3]
            )
        except Exception:
            trc_str = ""

        details = f"TRC: {trc_str} | {finding.get('ticket_count', 0)} tickets"
        friction = finding.get("dominant_friction_type", "")
        if friction:
            details += f" | {friction}"
        trend = finding.get("temporal_trend", "")
        if trend:
            details += f" | {trend}"

        detail_lbl = QLabel(details)
        detail_lbl.setStyleSheet(f"font-size: 11px; color: {ALMA_TEXT_MID}; border: none;")
        rl.addWidget(detail_lbl)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        deep_btn = QPushButton("Deep Dive")
        deep_btn.setCursor(Qt.PointingHandCursor)
        deep_btn.setStyleSheet(f"""
            QPushButton {{
                background: {ALMA_GREEN_DARK}; color: {ALMA_TEXT_ON_DARK};
                border: none; border-radius: 6px; padding: 4px 12px;
                font-size: 11px; font-weight: 600;
            }}
            QPushButton:hover {{ background: {ALMA_GREEN_MID}; }}
        """)
        finding_id = str(finding.get("finding_id", ""))
        finding_title = finding.get("title", "")
        deep_btn.clicked.connect(
            lambda checked, fid=finding_id, ft=finding_title:
                self.deep_dive_requested.emit(fid, ft)
        )
        btn_row.addWidget(deep_btn)

        view_btn = QPushButton("View Tickets")
        view_btn.setCursor(Qt.PointingHandCursor)
        view_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {ALMA_INFO};
                border: 1px solid {ALMA_INFO}; border-radius: 6px;
                padding: 4px 12px; font-size: 11px; font-weight: 600;
            }}
            QPushButton:hover {{ background: {ALMA_INFO}; color: white; }}
        """)
        ticket_ids_raw = finding.get("exemplar_ticket_ids", "[]")
        try:
            ticket_ids = _json.loads(ticket_ids_raw) if isinstance(ticket_ids_raw, str) else (ticket_ids_raw or [])
        except Exception:
            ticket_ids = []
        view_btn.clicked.connect(
            lambda checked, tids=ticket_ids:
                self.view_tickets_requested.emit(tids)
        )
        btn_row.addWidget(view_btn)

        btn_row.addStretch()
        rl.addLayout(btn_row)

        return row

    # ── Event Handlers ──

    def _on_taxonomy_row_clicked(self, item, column):
        """Emit pattern data when a taxonomy row is clicked."""
        pattern_data = item.data(0, Qt.UserRole)
        if pattern_data and isinstance(pattern_data, dict):
            self.pattern_selected.emit(pattern_data)

    def _set_growth_filter(self, tier):
        """Update growth chart filter."""
        self._growth_tier_filter = tier
        self._filter_all.setChecked(tier == "all")
        self._filter_active.setChecked(tier == "active")
        self._filter_prob.setChecked(tier == "probationary")
        self._refresh_growth_chart()

    # ── Widget Factory ──

    def _make_kpi_card(self, title, value, subtitle):
        """Create a KPI stat card (same pattern as trc_analytics)."""
        frame = QFrame()
        frame.setStyleSheet(f"""
            QFrame {{
                background: {ALMA_BG_ELEVATED};
                border: 1px solid rgba(214, 210, 202, 0.45);
                border-radius: 12px;
            }}
        """)
        apply_card_shadow_soft(frame)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(4)

        title_lbl = QLabel(title)
        title_lbl.setStyleSheet(
            f"font-size: 11px; font-weight: 600; color: {ALMA_TEXT_LIGHT}; "
            f"letter-spacing: 0.5px; border: none;"
        )
        layout.addWidget(title_lbl)

        value_lbl = QLabel(value)
        value_lbl.setStyleSheet(
            f"font-size: 26px; font-weight: 700; color: {ALMA_TEXT_DARK}; border: none;"
        )
        layout.addWidget(value_lbl)

        sub_lbl = QLabel(subtitle)
        sub_lbl.setStyleSheet(
            f"font-size: 11px; color: {ALMA_TEXT_LIGHT}; border: none;"
        )
        layout.addWidget(sub_lbl)

        return {"frame": frame, "value": value_lbl, "subtitle": sub_lbl}
