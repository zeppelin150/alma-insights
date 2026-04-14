"""
Alma Insights — Tag Pill Widget (Phase 1)

Compact, reusable pill-row widget for rendering tag chips. Used in the Data
Warehouse page's tag column today; will be reused in chat results and the
Phase 9 tag audit UI.

Design:
- Accepts a list of tag strings.
- Renders at most `max_visible` pills + a "+N" overflow label.
- Each pill is a QLabel with a colored background chosen deterministically
  from the tag string hash (stable across sessions; no bespoke theming
  required from callers).
- Hovering the overflow label shows the full tag list as a tooltip.
- No signals — purely display. If we need click handling later, it's a
  drop-in extension.

Public API:
    TagPillWidget(tags=None, max_visible=3, parent=None)
    .set_tags(tags: list[str])

Module deps: PySide6, stdlib (hashlib)
Dependents: data_warehouse_page, (future) chat_results_view, tag_audit_dialog
"""

from __future__ import annotations

import hashlib

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget


# A small palette of accessible tag backgrounds. Picked to pair with white text
# and stay within the app's muted-earth theme. Deterministically selected
# per-tag so the same tag always renders the same color.
_PILL_PALETTE = (
    "#6E8E59",  # olive
    "#A87C4F",  # tan
    "#6C8C94",  # slate-teal
    "#8E6A57",  # terracotta
    "#7A6D8F",  # mauve
    "#5C8477",  # sage
    "#B38B4A",  # amber
    "#7D7F86",  # pewter
)


def _color_for_tag(tag: str) -> str:
    """Stable palette pick per tag via MD5 modulo palette length."""
    h = hashlib.md5(tag.encode("utf-8"), usedforsecurity=False).digest()
    idx = h[0] % len(_PILL_PALETTE)
    return _PILL_PALETTE[idx]


class TagPillWidget(QWidget):
    """Row of compact colored pills with graceful overflow.

    Args:
        tags: Initial tag strings. Empty / None renders an empty widget.
        max_visible: How many pills to show before collapsing to "+N".
        parent: Qt parent.
    """

    def __init__(
        self,
        tags: list[str] | None = None,
        max_visible: int = 3,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._max_visible = max(1, int(max_visible))
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(4)
        self._layout.addStretch(1)
        self.set_tags(tags or [])

    # ───── public ─────────────────────────────────────────────────

    def set_tags(self, tags: list[str]) -> None:
        """Replace current tag display.

        Args:
            tags: list of tag strings. None values are dropped; duplicates
                preserved in input order.
        """
        self._clear()
        if not tags:
            return

        clean = [t for t in tags if isinstance(t, str) and t.strip()]
        if not clean:
            return

        visible = clean[: self._max_visible]
        overflow = len(clean) - len(visible)

        # Keep a trailing stretch so pills left-align. Insert pills before it.
        for tag in visible:
            self._layout.insertWidget(self._layout.count() - 1, self._build_pill(tag))

        if overflow > 0:
            more = QLabel(f"+{overflow}")
            more.setToolTip("\n".join(clean))
            more.setStyleSheet(
                "color: #5C5C5C; font-size: 11px; padding: 1px 4px;"
            )
            self._layout.insertWidget(self._layout.count() - 1, more)

    # ───── internal ───────────────────────────────────────────────

    def _build_pill(self, tag: str) -> QLabel:
        lbl = QLabel(tag)
        lbl.setToolTip(tag)
        lbl.setAlignment(Qt.AlignCenter)
        font = QFont()
        font.setPointSize(9)
        font.setBold(True)
        lbl.setFont(font)
        bg = _color_for_tag(tag)
        lbl.setStyleSheet(
            f"background: {bg}; color: #FFFFFF; "
            f"padding: 1px 7px; border-radius: 8px; "
        )
        return lbl

    def _clear(self) -> None:
        # Drop everything except the trailing stretch at index -1
        while self._layout.count() > 1:
            item = self._layout.takeAt(0)
            if item is None:
                break
            w = item.widget()
            if w is not None:
                w.deleteLater()
