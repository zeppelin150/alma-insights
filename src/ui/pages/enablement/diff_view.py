"""Red/green line-level diff preview for a staged content update.

A pure, reusable PySide6 widget: ``set_diff(current_md, proposed_md)`` renders
a line-level diff — RED for removed lines, GREEN for added lines, neutral for
unchanged — so a reviewer sees exactly what a draft changes instead of a wall
of regenerated text. Lines are classed via ``difflib.SequenceMatcher`` opcodes.

Pure widget: no network, no DB, no threads. Rendering a passed-in (current,
proposed) pair is synchronous and runs on the calling (main) thread. Optionally
accepts pre-flight check rows (from ``enablement_checks.run_checks``) to show as
badges above the diff.
"""

from __future__ import annotations

import difflib

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget,
)

from src.ui.theme import (
    ALMA_BG_ELEVATED, ALMA_BG_INSET, ALMA_BORDER_LIGHT, ALMA_ERROR,
    ALMA_SUCCESS, ALMA_TEXT_DARK, ALMA_TEXT_LIGHT, ALMA_TEXT_MID,
    ALMA_WARNING,
)

# A diff line: (tag, gutter glyph, text). tag drives the row styling.
_ADD = "add"
_DEL = "del"
_EQUAL = "equal"

# tag → (row background, gutter+text color, gutter glyph)
_ROW_STYLE = {
    _ADD: ("#E4EFE9", ALMA_SUCCESS, "+"),
    _DEL: ("#F7E2E2", ALMA_ERROR, "−"),
    _EQUAL: (ALMA_BG_ELEVATED, ALMA_TEXT_MID, " "),
}

# check status → badge (background, text color)
_BADGE_TINT = {
    "ok": ("#E4EFE9", ALMA_SUCCESS),
    "warn": ("#F6EBDD", ALMA_WARNING),
    "fail": ("#F7E2E2", ALMA_ERROR),
}


def diff_lines(current_md: str, proposed_md: str) -> list[tuple[str, str]]:
    """Classify each line of the diff as add / del / equal (pure helper).

    Returns ``(tag, text)`` rows in display order. A ``replace`` opcode is
    expanded into its removed lines followed by its added lines, so the result
    reads top-to-bottom like a unified diff body without the @@ hunks.
    """
    a = (current_md or "").splitlines()
    b = (proposed_md or "").splitlines()
    rows: list[tuple[str, str]] = []
    matcher = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        rows.extend(_opcode_rows(tag, a[i1:i2], b[j1:j2]))
    return rows


def _opcode_rows(tag: str, removed: list[str], added: list[str]) -> list[tuple[str, str]]:
    """Expand one SequenceMatcher opcode into per-line (tag, text) rows."""
    if tag == "equal":
        return [(_EQUAL, line) for line in removed]
    if tag == "delete":
        return [(_DEL, line) for line in removed]
    if tag == "insert":
        return [(_ADD, line) for line in added]
    # replace → removed lines (red) then added lines (green)
    return [(_DEL, line) for line in removed] + [(_ADD, line) for line in added]


def has_changes(rows: list[tuple[str, str]]) -> bool:
    """True when any row is an add or delete (i.e. the diff is non-empty)."""
    return any(tag != _EQUAL for tag, _text in rows)


class DiffView(QWidget):
    """Line-level red/green diff of current vs proposed card markdown.

    ``set_diff(current_md, proposed_md)`` (re)renders the body. ``set_checks``
    optionally shows pre-flight check badges above the diff. No DB, no network,
    no threads — rendering a passed-in pair is synchronous and main-thread only.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list[tuple[str, str]] = []
        self._build()

    # ── layout ────────────────────────────────────────────────────
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)

        self._badge_row = QHBoxLayout()
        self._badge_row.setSpacing(6)
        self._badge_row.addStretch(1)
        outer.addLayout(self._badge_row)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setStyleSheet(f"QScrollArea{{border:none; background:{ALMA_BG_ELEVATED};}}")
        self._body = QWidget()
        self._body.setStyleSheet(f"background:{ALMA_BG_ELEVATED};")
        self._body_lay = QVBoxLayout(self._body)
        self._body_lay.setContentsMargins(0, 0, 0, 0)
        self._body_lay.setSpacing(1)
        self._body_lay.addStretch(1)
        self._scroll.setWidget(self._body)
        outer.addWidget(self._scroll, 1)

    # ── public API ────────────────────────────────────────────────
    def set_diff(self, current_md: str, proposed_md: str) -> None:
        """Render the line-level diff of ``current_md`` → ``proposed_md``."""
        self._rows = diff_lines(current_md, proposed_md)
        self._clear_body()
        if not has_changes(self._rows):
            self._body_lay.insertWidget(0, self._empty_row())
            return
        for idx, (tag, text) in enumerate(self._rows):
            self._body_lay.insertWidget(idx, self._line_row(tag, text))

    def set_checks(self, checks: list[dict] | None) -> None:
        """Show pre-flight check badges above the diff (``run_checks`` rows)."""
        self._clear_badges()
        for chk in checks or []:
            self._badge_row.insertWidget(self._badge_row.count() - 1, self._check_badge(chk))

    def rows(self) -> list[tuple[str, str]]:
        """The classified (tag, text) rows of the last ``set_diff`` (for tests)."""
        return list(self._rows)

    # ── row builders ──────────────────────────────────────────────
    def _line_row(self, tag: str, text: str) -> QFrame:
        """One diff line: a colored gutter glyph + the line text."""
        bg, color, glyph = _ROW_STYLE.get(tag, _ROW_STYLE[_EQUAL])
        row = QFrame()
        row.setProperty("diffTag", tag)   # queryable in tests
        row.setStyleSheet(f"QFrame{{background:{bg}; border:none;}}")
        h = QHBoxLayout(row)
        h.setContentsMargins(8, 2, 10, 2)
        h.setSpacing(8)
        h.addWidget(self._gutter(glyph, color))
        h.addWidget(self._line_text(text, tag, color), 1)
        return row

    def _gutter(self, glyph: str, color: str) -> QLabel:
        lbl = QLabel(glyph)
        lbl.setFixedWidth(12)
        lbl.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
        lbl.setStyleSheet(
            f"color:{color}; font-family:Consolas,monospace; font-size:12.5px; "
            f"font-weight:700; border:none; background:transparent;"
        )
        return lbl

    def _line_text(self, text: str, tag: str, color: str) -> QLabel:
        lbl = QLabel(text or " ")
        lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lbl.setWordWrap(True)
        # equal lines read as muted body; changed lines pick up the tag color
        fg = ALMA_TEXT_DARK if tag == _EQUAL else color
        lbl.setStyleSheet(
            f"color:{fg}; font-family:Consolas,monospace; font-size:12.5px; "
            f"border:none; background:transparent;"
        )
        return lbl

    def _empty_row(self) -> QFrame:
        """Placeholder shown when current and proposed are identical."""
        row = QFrame()
        row.setProperty("diffEmpty", True)
        row.setStyleSheet(f"QFrame{{background:{ALMA_BG_INSET}; border:none; border-radius:8px;}}")
        h = QHBoxLayout(row)
        h.setContentsMargins(14, 14, 14, 14)
        lbl = QLabel("No changes — the proposed content matches the current card.")
        lbl.setStyleSheet(
            f"color:{ALMA_TEXT_LIGHT}; font-size:12.5px; border:none; background:transparent;"
        )
        h.addWidget(lbl)
        h.addStretch(1)
        return row

    def _check_badge(self, chk: dict) -> QLabel:
        """A small status badge for one pre-flight check row."""
        status = str(chk.get("status", "ok"))
        name = str(chk.get("check", ""))
        bg, fg = _BADGE_TINT.get(status, _BADGE_TINT["ok"])
        lbl = QLabel(f"{name}: {status}")
        lbl.setToolTip(str(chk.get("detail", "")))
        lbl.setStyleSheet(
            f"background:{bg}; color:{fg}; border-radius:10px; padding:2px 10px; "
            f"font-size:11px; font-weight:600; border:none;"
        )
        return lbl

    # ── teardown helpers ──────────────────────────────────────────
    def _clear_body(self) -> None:
        self._clear_layout(self._body_lay, keep_stretch=True)

    def _clear_badges(self) -> None:
        self._clear_layout(self._badge_row, keep_stretch=True)

    @staticmethod
    def _clear_layout(layout, *, keep_stretch: bool) -> None:
        """Drop every widget child of ``layout`` (leaving a trailing stretch)."""
        for i in reversed(range(layout.count())):
            item = layout.itemAt(i)
            w = item.widget()
            if w is not None:
                layout.removeWidget(w)
                w.deleteLater()
        if keep_stretch and layout.count() == 0:
            layout.addStretch(1)
