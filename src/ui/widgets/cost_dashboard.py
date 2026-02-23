"""
Alma Insights -- Cost Dashboard Widget (Pass 5.1)

Tab 2 of the NLP Scanner page. Provides:
  - Cost limits configuration (per-scan, monthly, period)
  - Token usage cards (today, weekly, monthly, plan utilization)
  - Cost history bar chart (weekly aggregation)
  - Gemini plan reference panel
"""

import logging
from datetime import datetime

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
    QDoubleSpinBox, QComboBox, QProgressBar, QScrollArea,
    QSizePolicy,
)
from PySide6.QtCore import Qt, Signal

from src.ui.theme import (
    ALMA_GREEN_DARK, ALMA_GREEN_LIGHT, ALMA_GREEN_SUBTLE,
    ALMA_WHITE, ALMA_CREAM, ALMA_TEXT_DARK, ALMA_TEXT_MID,
    ALMA_TEXT_LIGHT, ALMA_BORDER, ALMA_BORDER_LIGHT,
    ALMA_BG_ELEVATED, apply_card_shadow_soft,
)
from src.ui.widgets.charts import BarChartWidget
from src.data.usage_tracker import UsageTracker, GEMINI_PLANS

logger = logging.getLogger("alma.cost_dashboard")


class CostDashboard(QWidget):
    """Full Tab 2 content: cost limits, token usage, cost history, plan reference."""

    cost_limits_changed = Signal()

    def __init__(self, db_manager, parent=None):
        super().__init__(parent)
        self.db = db_manager
        self._tracker = UsageTracker(db_manager)
        self._build_ui()
        self.refresh()

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

        # ── Cost Limits Section ──
        self._build_cost_limits(layout)

        # ── Token Usage Cards ──
        self._build_token_usage(layout)

        # ── Cost History Chart ──
        self._build_cost_history(layout)

        # ── Plan Reference ──
        self._build_plan_reference(layout)

        layout.addStretch()
        scroll.setWidget(content)
        outer.addWidget(scroll)

    def _build_cost_limits(self, parent_layout):
        """Build cost limits configuration section."""
        card = self._make_section_card("Cost Limits")
        card_layout = card.layout()

        # Spinboxes row
        spin_row = QHBoxLayout()
        spin_row.setSpacing(16)

        # Per-scan limit
        self._per_scan_spin, per_scan_col = self._make_limit_column(
            "Per-Scan Limit", 50.00, 0.0, 1000.0
        )
        spin_row.addLayout(per_scan_col, 1)

        # Monthly limit
        self._monthly_spin, monthly_col = self._make_limit_column(
            "Monthly Limit", 500.00, 0.0, 10000.0
        )
        spin_row.addLayout(monthly_col, 1)

        # Period limit
        self._period_spin, period_col = self._make_limit_column(
            "Period Limit", 2000.00, 0.0, 50000.0
        )
        spin_row.addLayout(period_col, 1)

        card_layout.addLayout(spin_row)

        # Period type selector
        period_row = QHBoxLayout()
        period_row.addStretch()
        period_lbl = QLabel("Period:")
        period_lbl.setStyleSheet(f"font-size: 11px; color: {ALMA_TEXT_MID}; border: none;")
        period_row.addWidget(period_lbl)
        self._period_type = QComboBox()
        self._period_type.addItems(["Quarterly", "Yearly"])
        self._period_type.setStyleSheet(f"""
            QComboBox {{
                font-size: 11px; padding: 4px 8px;
                border: 1px solid {ALMA_BORDER};
                border-radius: 4px; background: {ALMA_WHITE};
            }}
        """)
        self._period_type.currentTextChanged.connect(self._on_limits_changed)
        period_row.addWidget(self._period_type)
        card_layout.addLayout(period_row)

        # Usage vs limit progress bars
        usage_row = QHBoxLayout()
        usage_row.setSpacing(12)

        self._scan_usage_card = self._make_usage_vs_limit_card("This Scan", "$0.00", "$50.00", 0)
        usage_row.addWidget(self._scan_usage_card["frame"], 1)

        self._month_usage_card = self._make_usage_vs_limit_card("This Month", "$0.00", "$500.00", 0)
        usage_row.addWidget(self._month_usage_card["frame"], 1)

        self._period_usage_card = self._make_usage_vs_limit_card("This Quarter", "$0.00", "$2,000.00", 0)
        usage_row.addWidget(self._period_usage_card["frame"], 1)

        card_layout.addLayout(usage_row)
        parent_layout.addWidget(card)

    def _build_token_usage(self, parent_layout):
        """Build token usage KPI cards section."""
        card = self._make_section_card("Gemini Token Usage")
        card_layout = card.layout()

        cards_row = QHBoxLayout()
        cards_row.setSpacing(12)

        self._daily_card = self._make_kpi_card("Today", "--", "tokens")
        cards_row.addWidget(self._daily_card["frame"], 1)

        self._weekly_card = self._make_kpi_card("This Week", "--", "tokens")
        cards_row.addWidget(self._weekly_card["frame"], 1)

        self._monthly_card = self._make_kpi_card("This Month", "--", "tokens")
        cards_row.addWidget(self._monthly_card["frame"], 1)

        self._plan_card = self._make_kpi_card("Plan Utilization", "--", "")
        cards_row.addWidget(self._plan_card["frame"], 1)

        card_layout.addLayout(cards_row)
        parent_layout.addWidget(card)

    def _build_cost_history(self, parent_layout):
        """Build cost history bar chart section."""
        card = self._make_section_card("Cost History")
        card_layout = card.layout()

        self._cost_chart = BarChartWidget()
        self._cost_chart.setMinimumHeight(250)
        card_layout.addWidget(self._cost_chart)

        # Summary row
        self._cost_summary = QLabel("Total scans: 0  |  Total cost: $0.00  |  Avg: $0.00/scan")
        self._cost_summary.setStyleSheet(
            f"font-size: 11px; color: {ALMA_TEXT_MID}; border: none;"
        )
        self._cost_summary.setAlignment(Qt.AlignCenter)
        card_layout.addWidget(self._cost_summary)

        parent_layout.addWidget(card)

    def _build_plan_reference(self, parent_layout):
        """Build Gemini plan reference panel."""
        card = self._make_section_card("Gemini Plan Reference")
        card_layout = card.layout()

        # Plan selector
        plan_row = QHBoxLayout()
        plan_lbl = QLabel("Plan:")
        plan_lbl.setStyleSheet(f"font-size: 12px; color: {ALMA_TEXT_DARK}; border: none;")
        plan_row.addWidget(plan_lbl)

        self._plan_combo = QComboBox()
        for key, plan in GEMINI_PLANS.items():
            self._plan_combo.addItem(plan["label"], key)
        self._plan_combo.setStyleSheet(f"""
            QComboBox {{
                font-size: 12px; padding: 4px 12px;
                border: 1px solid {ALMA_BORDER};
                border-radius: 4px; background: {ALMA_WHITE};
            }}
        """)
        self._plan_combo.currentIndexChanged.connect(self._update_plan_display)
        plan_row.addWidget(self._plan_combo)
        plan_row.addStretch()
        card_layout.addLayout(plan_row)

        self._plan_details = QLabel("")
        self._plan_details.setStyleSheet(
            f"font-size: 11px; color: {ALMA_TEXT_MID}; border: none; padding: 8px;"
        )
        self._plan_details.setWordWrap(True)
        card_layout.addWidget(self._plan_details)

        parent_layout.addWidget(card)

    # ── Refresh ──

    def refresh(self):
        """Reload all panels from DB."""
        self._load_cost_limits()
        self._refresh_token_cards()
        self._refresh_cost_chart()
        self._update_plan_display()
        self._refresh_usage_vs_limits()

    def _load_cost_limits(self):
        """Load saved cost limits from DB."""
        try:
            limits = self.db.get_cost_limits()
            if "per_scan" in limits:
                self._per_scan_spin.setValue(limits["per_scan"]["limit_usd"])
            if "monthly" in limits:
                self._monthly_spin.setValue(limits["monthly"]["limit_usd"])
            if "period" in limits:
                self._period_spin.setValue(limits["period"]["limit_usd"])
                pt = limits["period"].get("period_type", "quarterly")
                idx = 0 if pt == "quarterly" else 1
                self._period_type.setCurrentIndex(idx)
        except Exception as e:
            logger.debug(f"Load cost limits: {e}")

    def _refresh_token_cards(self):
        """Update token usage KPI cards."""
        try:
            daily = self._tracker.get_daily_totals()
            self._update_kpi_card(
                self._daily_card,
                self._format_tokens(daily["tokens_total"]),
                f"In: {self._format_tokens(daily['tokens_in'])} | "
                f"Out: {self._format_tokens(daily['tokens_out'])}"
            )
        except Exception:
            pass

        try:
            weekly = self._tracker.get_weekly_totals()
            self._update_kpi_card(
                self._weekly_card,
                self._format_tokens(weekly["tokens_total"]),
                f"In: {self._format_tokens(weekly['tokens_in'])} | "
                f"Out: {self._format_tokens(weekly['tokens_out'])}"
            )
        except Exception:
            pass

        try:
            monthly = self._tracker.get_monthly_totals()
            self._update_kpi_card(
                self._monthly_card,
                self._format_tokens(monthly["tokens_total"]),
                f"In: {self._format_tokens(monthly['tokens_in'])} | "
                f"Out: {self._format_tokens(monthly['tokens_out'])}"
            )
        except Exception:
            pass

        try:
            plan_key = self._plan_combo.currentData() or "flash_2.5"
            util = self._tracker.get_plan_utilization(plan_key)
            self._update_kpi_card(
                self._plan_card,
                f"{util['tpm_pct']:.1f}%",
                f"RPD: {util['calls_total']:,} / {util['rpd_limit']:,}"
            )
        except Exception:
            pass

    def _refresh_cost_chart(self):
        """Update cost history bar chart."""
        try:
            history = self._tracker.get_cost_history_weekly(weeks=12)
            if not history:
                self._cost_chart.set_data([], [])
                return

            labels = [h.get("week", "?") for h in history]
            # Shorten labels: "2026-08" -> "W08"
            short_labels = []
            for lbl in labels:
                parts = lbl.split("-")
                if len(parts) == 2:
                    short_labels.append(f"W{parts[1]}")
                else:
                    short_labels.append(lbl)

            values = [h.get("cost_usd", 0) for h in history]
            self._cost_chart.set_data(values, short_labels)

            total_cost = sum(values)
            total_scans = sum(h.get("api_calls", 0) for h in history)
            avg = total_cost / max(len(history), 1)
            self._cost_summary.setText(
                f"Total scans: {total_scans}  |  Total cost: "
                f"${total_cost:.2f}  |  Avg: ${avg:.2f}/week"
            )
        except Exception as e:
            logger.debug(f"Cost chart refresh: {e}")

    def _refresh_usage_vs_limits(self):
        """Update usage vs limit progress cards."""
        try:
            per_scan = self._per_scan_spin.value()
            monthly = self._monthly_spin.value()
            period = self._period_spin.value()

            # Current scan cost (if there's an active scan)
            scan_cost = 0.0  # Will be set by parent page if needed
            self._update_usage_card(
                self._scan_usage_card, scan_cost, per_scan, "This Scan"
            )

            # Monthly
            monthly_totals = self._tracker.get_monthly_totals()
            month_cost = monthly_totals.get("cost_usd", 0.0)
            self._update_usage_card(
                self._month_usage_card, month_cost, monthly, "This Month"
            )

            # Period
            period_type = "quarterly" if self._period_type.currentIndex() == 0 else "yearly"
            period_totals = self._tracker.get_period_totals(period_type)
            period_cost = period_totals.get("cost_usd", 0.0)
            period_label = "This Quarter" if period_type == "quarterly" else "This Year"
            self._update_usage_card(
                self._period_usage_card, period_cost, period, period_label
            )
        except Exception as e:
            logger.debug(f"Usage vs limits refresh: {e}")

    def _update_plan_display(self):
        """Update plan reference text based on selected plan."""
        plan_key = self._plan_combo.currentData() or "flash_2.5"
        plan = GEMINI_PLANS.get(plan_key, GEMINI_PLANS["flash_2.5"])

        text = (
            f"Input: ${plan['input_cost_per_1m']:.2f} / 1M tokens  |  "
            f"Output: ${plan['output_cost_per_1m']:.2f} / 1M tokens\n"
            f"RPM: {plan['rpm']:,}  |  RPD: {plan['rpd']:,}  |  "
            f"TPM: {plan['tpm']:,}"
        )
        self._plan_details.setText(text)

    def _on_limits_changed(self):
        """Save cost limits when changed."""
        try:
            self.db.set_cost_limit("per_scan", self._per_scan_spin.value())
            self.db.set_cost_limit("monthly", self._monthly_spin.value())
            period_type = "quarterly" if self._period_type.currentIndex() == 0 else "yearly"
            self.db.set_cost_limit("period", self._period_spin.value(), period_type)
            self.cost_limits_changed.emit()
        except Exception as e:
            logger.debug(f"Save cost limits: {e}")

    # ── Widget Factories ──

    def _make_section_card(self, title_text):
        """Create a section card with title."""
        card = QFrame()
        card.setStyleSheet(f"""
            QFrame {{
                background: {ALMA_BG_ELEVATED};
                border: 1px solid rgba(214, 210, 202, 0.45);
                border-radius: 12px;
            }}
        """)
        apply_card_shadow_soft(card)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(12)

        title = QLabel(title_text)
        title.setStyleSheet(
            f"font-size: 14px; font-weight: 700; color: {ALMA_TEXT_DARK}; border: none;"
        )
        layout.addWidget(title)
        return card

    def _make_limit_column(self, label, default, min_val, max_val):
        """Create a cost limit column with label + spinbox."""
        col = QVBoxLayout()
        col.setSpacing(4)

        lbl = QLabel(label)
        lbl.setStyleSheet(f"font-size: 11px; color: {ALMA_TEXT_LIGHT}; border: none;")
        col.addWidget(lbl)

        spin = QDoubleSpinBox()
        spin.setRange(min_val, max_val)
        spin.setValue(default)
        spin.setPrefix("$")
        spin.setDecimals(2)
        spin.setSingleStep(10.0)
        spin.setStyleSheet(f"""
            QDoubleSpinBox {{
                font-size: 13px; padding: 4px 8px;
                border: 1px solid {ALMA_BORDER};
                border-radius: 6px; background: {ALMA_WHITE};
            }}
        """)
        spin.valueChanged.connect(self._on_limits_changed)
        col.addWidget(spin)

        return spin, col

    def _make_usage_vs_limit_card(self, title, used_text, limit_text, pct):
        """Create a usage vs limit mini-card with progress bar."""
        frame = QFrame()
        frame.setStyleSheet(f"""
            QFrame {{
                background: {ALMA_BG_ELEVATED};
                border: 1px solid rgba(214, 210, 202, 0.45);
                border-radius: 8px;
            }}
        """)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(4)

        title_lbl = QLabel(title)
        title_lbl.setStyleSheet(
            f"font-size: 11px; font-weight: 600; color: {ALMA_TEXT_DARK}; border: none;"
        )
        layout.addWidget(title_lbl)

        value_lbl = QLabel(f"{used_text} / {limit_text}")
        value_lbl.setStyleSheet(
            f"font-size: 12px; color: {ALMA_TEXT_MID}; border: none;"
        )
        layout.addWidget(value_lbl)

        progress = QProgressBar()
        progress.setRange(0, 100)
        progress.setValue(pct)
        progress.setFixedHeight(10)
        progress.setTextVisible(False)
        progress.setStyleSheet(f"""
            QProgressBar {{
                border: 1px solid {ALMA_BORDER_LIGHT};
                border-radius: 4px;
                background: {ALMA_CREAM};
            }}
            QProgressBar::chunk {{
                background: {ALMA_GREEN_DARK};
                border-radius: 3px;
            }}
        """)
        layout.addWidget(progress)

        return {"frame": frame, "title": title_lbl, "value": value_lbl, "progress": progress}

    def _update_usage_card(self, card_dict, used, limit, title):
        """Update a usage vs limit card."""
        pct = int(used / limit * 100) if limit > 0 else 0
        card_dict["title"].setText(title)
        card_dict["value"].setText(f"${used:.2f} / ${limit:.2f}")
        card_dict["progress"].setValue(min(100, pct))

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

    def _update_kpi_card(self, card_dict, value, subtitle=""):
        """Update KPI card value and subtitle."""
        card_dict["value"].setText(str(value))
        if subtitle:
            card_dict["subtitle"].setText(subtitle)

    @staticmethod
    def _format_tokens(count):
        """Format token count for display: 1234567 -> '1.2M'."""
        if count >= 1_000_000:
            return f"{count / 1_000_000:.1f}M"
        elif count >= 1_000:
            return f"{count / 1_000:.1f}K"
        else:
            return str(count)
