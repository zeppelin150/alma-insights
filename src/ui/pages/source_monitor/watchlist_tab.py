"""
Watchlist tab — card-based rule manager (2026-05-07 redesign).

Replaces the cramped QTableWidget with a QScrollArea of rule cards
grouped under "System Rules" and "Custom Rules" collapsible sections.
Each card shows: severity badge (red incident / amber watch), rule
name, type/source/keywords preview, EWMA confidence bar (color-tiered),
fires/confirmed/dismissed counts, and action buttons (ON/OFF, Edit,
Delete-when-custom).
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from src.ui.pages.source_monitor._styles import (
    DANGER_BTN,
    GHOST_BTN,
    PRIMARY_BTN,
)
from src.ui.pages.source_monitor.rule_dialog import RuleDialog
from src.ui.theme import (
    ALMA_BG_INSET,
    ALMA_BORDER_LIGHT,
    ALMA_ERROR,
    ALMA_GREEN_LIGHT,
    ALMA_SUCCESS,
    ALMA_TEXT_DARK,
    ALMA_TEXT_LIGHT,
    ALMA_TEXT_MID,
    ALMA_WARNING,
    ALMA_WHITE,
    apply_card_shadow_soft,
)

logger = logging.getLogger("alma.source_monitor.watchlist")

#: Confidence tier thresholds for EWMA bar color.
_EWMA_HIGH = 0.7
_EWMA_LOW = 0.3


class WatchlistTab(QWidget):
    """Card-based Watchlist rule manager.

    Public API
    ──────────
    - ``set_watchlist(watchlist)`` — wire engine, refresh on call
    - ``refresh_rules()`` — pull latest rules and rebuild card list
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._watchlist = None
        self._cards: list[QFrame] = []
        self._build_ui()

    # ── Public API ────────────────────────────────────────────────

    def set_watchlist(self, watchlist) -> None:
        """Wire the engine and refresh."""
        self._watchlist = watchlist
        self.refresh_rules()

    def refresh_rules(self) -> None:
        """Pull rules from the engine and rebuild the grouped lists."""
        self._clear_cards()

        if not self._watchlist:
            self._system_section.setVisible(False)
            self._custom_section.setVisible(False)
            self._placeholder.show()
            return

        try:
            rules = self._watchlist.list_rules()
        except Exception as exc:
            logger.warning("list_rules failed: %s", exc)
            rules = []

        if not rules:
            self._system_section.setVisible(False)
            self._custom_section.setVisible(False)
            self._placeholder.show()
            return

        self._placeholder.hide()
        system = [r for r in rules if r.get("is_system")]
        custom = [r for r in rules if not r.get("is_system")]

        self._populate_section(self._system_layout, system, is_system=True)
        self._populate_section(self._custom_layout, custom, is_system=False)

        self._system_header.setText(f"System Rules  ({len(system)})")
        self._system_section.setVisible(bool(system))

        self._custom_header.setText(f"Custom Rules  ({len(custom)})")
        # Always show the custom section so the empty state ("no custom
        # rules — click Add Rule") is visible.
        self._custom_section.setVisible(True)

    # ── UI scaffolding ───────────────────────────────────────────

    def _build_ui(self) -> None:
        """Lay out header + scroll area with grouped sections."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 12, 0, 0)
        layout.setSpacing(12)

        # Header row: descriptive copy + Add Rule button
        hdr_row = QHBoxLayout()
        hdr_row.setSpacing(12)
        desc = QLabel(
            "Manage alert rules. System rules are prepopulated and cannot "
            "be deleted but can be toggled on/off."
        )
        desc.setStyleSheet(f"font-size: 12px; color: {ALMA_TEXT_LIGHT};")
        desc.setWordWrap(True)
        hdr_row.addWidget(desc, 1)

        add_btn = QPushButton("+ Add Rule")
        add_btn.setStyleSheet(PRIMARY_BTN)
        add_btn.setCursor(Qt.PointingHandCursor)
        add_btn.clicked.connect(self._on_add_rule)
        hdr_row.addWidget(add_btn)

        layout.addLayout(hdr_row)

        # Scrollable container holding the two sections.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
        )
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(16)

        # ── System Rules section ──
        self._system_section, self._system_header, self._system_layout = (
            _make_section("System Rules")
        )
        body_layout.addWidget(self._system_section)

        # ── Custom Rules section ──
        self._custom_section, self._custom_header, self._custom_layout = (
            _make_section("Custom Rules")
        )
        body_layout.addWidget(self._custom_section)

        # ── Empty placeholder ──
        self._placeholder = QLabel(
            "No watchlist rules configured. Click 'Add Rule' to create one."
        )
        self._placeholder.setStyleSheet(
            f"font-size: 13px; color: {ALMA_TEXT_LIGHT}; padding: 40px;"
        )
        self._placeholder.setAlignment(Qt.AlignCenter)
        body_layout.addWidget(self._placeholder)

        body_layout.addStretch()
        scroll.setWidget(body)
        layout.addWidget(scroll, 1)

    def _clear_cards(self) -> None:
        """Drop every existing card widget; layouts keep their headers."""
        for card in self._cards:
            card.setParent(None)
            card.deleteLater()
        self._cards.clear()

    def _populate_section(
        self, layout: QVBoxLayout, rules: list[dict], *, is_system: bool
    ) -> None:
        """Build one card per rule and append to ``layout``."""
        for rule in rules:
            card = _build_rule_card(
                rule,
                on_toggle=self._on_toggle_rule,
                on_edit=self._on_edit_rule,
                on_delete=self._on_delete_rule if not is_system else None,
            )
            layout.addWidget(card)
            self._cards.append(card)

    # ── Action handlers ───────────────────────────────────────────

    def _on_add_rule(self) -> None:
        """Open the RuleDialog in create mode."""
        dlg = RuleDialog(self)
        if dlg.exec() != RuleDialog.Accepted:
            return
        if not self._watchlist:
            return
        try:
            self._watchlist.create_rule(**dlg.get_rule_data())
        except Exception as exc:
            QMessageBox.warning(
                self, "Create Rule Failed", f"Could not create rule:\n{exc}"
            )
            return
        self.refresh_rules()

    def _on_edit_rule(self, rule: dict) -> None:
        """Open the RuleDialog pre-populated for an edit."""
        dlg = RuleDialog(self, existing=rule)
        if dlg.exec() != RuleDialog.Accepted:
            return
        if not self._watchlist:
            return
        rule_id = rule.get("id")
        try:
            # Fall back to delete+create when update_rule isn't available
            # — keeps us forward-compat with engine variants.
            if hasattr(self._watchlist, "update_rule"):
                self._watchlist.update_rule(rule_id, **dlg.get_rule_data())
            else:
                if rule.get("is_system"):
                    QMessageBox.information(
                        self,
                        "Cannot Edit System Rule",
                        "System rules can only be toggled on/off.",
                    )
                    return
                self._watchlist.delete_rule(rule_id)
                self._watchlist.create_rule(**dlg.get_rule_data())
        except Exception as exc:
            QMessageBox.warning(
                self, "Edit Rule Failed", f"Could not save rule:\n{exc}"
            )
            return
        self.refresh_rules()

    def _on_toggle_rule(self, rule_id: int, enabled: bool) -> None:
        """Flip a rule's enabled flag."""
        if not self._watchlist:
            return
        try:
            self._watchlist.toggle_rule(rule_id, enabled)
        except Exception as exc:
            logger.warning("toggle_rule failed: %s", exc)
        self.refresh_rules()

    def _on_delete_rule(self, rule_id: int) -> None:
        """Confirm and delete a custom rule."""
        reply = QMessageBox.question(
            self,
            "Delete Rule",
            "Delete this watchlist rule? This cannot be undone.",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        if not self._watchlist:
            return
        try:
            self._watchlist.delete_rule(rule_id)
        except Exception as exc:
            logger.warning("delete_rule failed: %s", exc)
        self.refresh_rules()


# ─── Free functions: rule card + section builder ─────────────────

def _make_section(title: str) -> tuple[QFrame, QLabel, QVBoxLayout]:
    """Construct a section frame with a header label + body layout.

    Returns:
        ``(section_frame, header_label, body_layout)`` — caller appends
        cards to ``body_layout`` and updates ``header_label.setText``
        to reflect the count.
    """
    section = QFrame()
    section.setStyleSheet("QFrame { background: transparent; border: none; }")
    section_lay = QVBoxLayout(section)
    section_lay.setContentsMargins(0, 0, 0, 0)
    section_lay.setSpacing(8)

    header = QLabel(title)
    header.setStyleSheet(
        f"font-size: 12px; font-weight: 700; color: {ALMA_TEXT_MID}; "
        f"text-transform: uppercase; letter-spacing: 0.4px; border: none;"
    )
    section_lay.addWidget(header)

    body = QVBoxLayout()
    body.setContentsMargins(0, 0, 0, 0)
    body.setSpacing(8)
    section_lay.addLayout(body)

    return section, header, body


def _build_rule_card(rule: dict, *, on_toggle, on_edit, on_delete) -> QFrame:
    """Build one rule card.

    Args:
        rule: Row dict from ``WatchlistEngine.list_rules()``.
        on_toggle: Callable ``(rule_id, new_enabled_bool)``.
        on_edit: Callable ``(rule_dict)``.
        on_delete: Callable ``(rule_id)`` or ``None`` for system rules.

    Returns:
        Configured QFrame ready to add to a layout.
    """
    severity = rule.get("severity", "watch")
    is_incident = severity == "incident"
    enabled = bool(rule.get("enabled", 1))

    border_color = ALMA_ERROR if is_incident else ALMA_WARNING
    if not enabled:
        border_color = ALMA_BORDER_LIGHT

    card = QFrame()
    card.setStyleSheet(
        f"""
        QFrame {{
            background: {ALMA_WHITE};
            border: 1px solid {ALMA_BORDER_LIGHT};
            border-left: 4px solid {border_color};
            border-radius: 8px;
        }}
        """
    )
    card.setMinimumWidth(420)
    card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
    apply_card_shadow_soft(card)

    lay = QVBoxLayout(card)
    lay.setContentsMargins(14, 12, 14, 12)
    lay.setSpacing(8)

    lay.addLayout(_card_top_row(rule, is_incident, enabled, on_toggle, on_edit, on_delete))
    lay.addWidget(_card_subtitle(rule))
    lay.addLayout(_card_metrics_row(rule))

    return card


def _card_top_row(
    rule: dict,
    is_incident: bool,
    enabled: bool,
    on_toggle,
    on_edit,
    on_delete,
) -> QHBoxLayout:
    """Severity badge + name + action buttons."""
    row = QHBoxLayout()
    row.setSpacing(8)

    sev_badge = QLabel(rule.get("severity", "watch").upper())
    sev_color = ALMA_ERROR if is_incident else ALMA_WARNING
    sev_bg = "rgba(196,30,30,0.10)" if is_incident else "rgba(180,83,9,0.12)"
    sev_badge.setStyleSheet(
        f"font-size: 10px; font-weight: 700; color: {sev_color}; "
        f"background: {sev_bg}; border-radius: 4px; padding: 3px 9px; "
        f"border: none;"
    )
    sev_badge.setFixedHeight(22)
    row.addWidget(sev_badge)

    name = QLabel(rule.get("name", "Unnamed rule"))
    name.setStyleSheet(
        f"font-size: 13px; font-weight: 700; color: {ALMA_TEXT_DARK}; "
        f"border: none;"
    )
    row.addWidget(name)
    row.addStretch()

    rule_id = rule.get("id")

    toggle_btn = QPushButton("ON" if enabled else "OFF")
    tcolor = ALMA_SUCCESS if enabled else ALMA_TEXT_LIGHT
    toggle_btn.setStyleSheet(
        f"""
        QPushButton {{
            background: transparent; color: {tcolor};
            border: 1.5px solid {tcolor}; border-radius: 4px;
            padding: 4px 12px; font-size: 11px; font-weight: 700;
        }}
        QPushButton:hover {{ background: rgba(0,0,0,0.03); }}
        """
    )
    toggle_btn.setCursor(Qt.PointingHandCursor)
    toggle_btn.setFixedHeight(26)
    toggle_btn.setMinimumWidth(56)
    toggle_btn.clicked.connect(
        lambda _, rid=rule_id, en=enabled: on_toggle(rid, not en)
    )
    row.addWidget(toggle_btn)

    edit_btn = QPushButton("Edit")
    edit_btn.setStyleSheet(GHOST_BTN)
    edit_btn.setCursor(Qt.PointingHandCursor)
    edit_btn.setFixedHeight(26)
    edit_btn.clicked.connect(lambda _, r=rule: on_edit(r))
    row.addWidget(edit_btn)

    if on_delete is not None:
        del_btn = QPushButton("Del")
        del_btn.setStyleSheet(DANGER_BTN)
        del_btn.setCursor(Qt.PointingHandCursor)
        del_btn.setFixedHeight(26)
        del_btn.clicked.connect(lambda _, rid=rule_id: on_delete(rid))
        row.addWidget(del_btn)

    return row


def _card_subtitle(rule: dict) -> QLabel:
    """Compact one-line summary: ``type · source · keywords/scope``."""
    parts = [rule.get("rule_type", "—")]
    src = rule.get("source_filter", "") or "all sources"
    parts.append(src)

    keywords = rule.get("keywords", "").strip()
    if keywords:
        # Show up to 4 keywords; ellipsize beyond.
        kws = [k.strip() for k in keywords.split(",") if k.strip()]
        preview = ", ".join(kws[:4])
        if len(kws) > 4:
            preview += "…"
        parts.append(f"keywords: {preview}")

    entity = rule.get("entity_type", "")
    if entity:
        parts.append(f"entity: {entity}")

    threshold = rule.get("volume_threshold", 0)
    if threshold:
        win = rule.get("volume_window_minutes", 60)
        parts.append(f"vol≥{threshold}/{win}min")

    text = "  ·  ".join(parts)
    label = QLabel(text)
    label.setStyleSheet(
        f"font-size: 11px; color: {ALMA_TEXT_LIGHT}; border: none;"
    )
    label.setWordWrap(True)
    return label


def _card_metrics_row(rule: dict) -> QHBoxLayout:
    """Confidence bar + Fires/Confirmed/Dismissed counters."""
    row = QHBoxLayout()
    row.setSpacing(12)

    conf_label = QLabel("Confidence")
    conf_label.setStyleSheet(
        f"font-size: 10px; font-weight: 600; color: {ALMA_TEXT_MID}; "
        f"border: none;"
    )
    row.addWidget(conf_label)

    ewma = float(rule.get("ewma_confidence", 0.5))
    bar = QProgressBar()
    bar.setRange(0, 100)
    bar.setValue(int(ewma * 100))
    bar.setTextVisible(False)
    bar.setFixedSize(120, 8)
    chunk_color = _confidence_color(ewma)
    bar.setStyleSheet(
        f"""
        QProgressBar {{
            background: {ALMA_BG_INSET}; border: none;
            border-radius: 4px;
        }}
        QProgressBar::chunk {{
            background: {chunk_color}; border-radius: 4px;
        }}
        """
    )
    row.addWidget(bar)

    val_lbl = QLabel(f"{ewma:.2f}")
    val_lbl.setStyleSheet(
        f"font-family: 'Consolas', monospace; font-size: 11px; "
        f"color: {ALMA_TEXT_DARK}; border: none;"
    )
    row.addWidget(val_lbl)

    row.addStretch()

    fires = rule.get("total_fires", 0)
    confirmed = rule.get("total_confirmed", 0)
    dismissed = rule.get("total_dismissed", 0)
    counts = QLabel(
        f"Fires: {fires}    ✓ {confirmed}    ✗ {dismissed}"
    )
    counts.setStyleSheet(
        f"font-size: 10px; color: {ALMA_TEXT_LIGHT}; border: none;"
    )
    row.addWidget(counts)

    return row


def _confidence_color(ewma: float) -> str:
    """Tier the EWMA bar color: green / amber / red."""
    if ewma >= _EWMA_HIGH:
        return ALMA_GREEN_LIGHT
    if ewma >= _EWMA_LOW:
        return ALMA_WARNING
    return ALMA_ERROR
