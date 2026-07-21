"""Attention Queue — the enablement Workbench "what needs attention" home.

A ranked landing surface driven by ``compute_health`` (Milestone B): every
Guru card is scored from Guru-native + enablement-local signals, then grouped
into the buckets the team works from — source-changed, verification overdue,
gap / duplicate, plus staged drafts awaiting review (an enablement-local
signal). Each row offers "Open targeted update" (→ Workbench) and "Dismiss".

Decoupled by construction: the off-thread loader only builds a
``get_connection`` + ``GuruClient`` and calls ``compute_health`` (itself fully
decoupled). No ticket / RCM / warehouse table is read here. Widgets are only
ever touched on the main thread — the worker emits ``health_ready`` /
``health_failed`` and the slots do the rendering.
"""

from __future__ import annotations

import threading

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from src.ui.pages.enablement._common import badge, card_frame, section_label
from src.ui.theme import (
    ALMA_ACCENT_TEAL, ALMA_BG_ELEVATED, ALMA_BORDER, ALMA_CREAM,
    ALMA_GREEN_DARK, ALMA_TEXT_DARK, ALMA_TEXT_LIGHT, ALMA_TEXT_MID,
    ALMA_TEXT_ON_DARK,
)

# bucket key → (section heading, badge label, badge kind from _common.TINT)
_BUCKETS = (
    ("source_changed", "SOURCE CHANGED", "Source changed", "high"),
    ("verification_overdue", "VERIFICATION OVERDUE", "Overdue", "in_progress"),
    ("gap_dup", "GAP / DUPLICATE", "Gap / dup", "draft"),
    ("staged", "STAGED DRAFTS AWAITING REVIEW", "Draft", "guru"),
)


def _title_of(card) -> str:
    """Best display title for a CardHealth row (signals.title → card_id)."""
    sig = getattr(card, "signals", None)
    if sig is not None and getattr(sig, "title", ""):
        return sig.title
    return getattr(card, "card_id", "") or "Untitled card"


def group_by_bucket(health: list) -> dict[str, list]:
    """Partition scored cards into the display buckets, worst score first.

    ``staged`` is keyed off a CardHealth whose ``bucket`` is literally
    ``"staged"`` (the loader appends those from local drafts); the three
    health buckets come straight from the scorer.
    """
    groups: dict[str, list] = {key: [] for key, _h, _l, _k in _BUCKETS}
    for card in health:
        bucket = getattr(card, "bucket", "")
        if bucket in groups:
            groups[bucket].append(card)
    for cards in groups.values():
        cards.sort(key=lambda c: getattr(c, "score", 1.0))
    return groups


class AttentionQueueTab(QWidget):
    """Ranked "what needs attention" home for the enablement Workbench."""

    open_update_requested = Signal(str)   # card_id → host opens it in the Workbench
    dismiss_requested = Signal(str)       # card_id → host stops surfacing it

    def __init__(self, health_provider=None, parent=None):
        """``health_provider`` (tests) is a zero-arg callable returning a list
        of CardHealth; when None the tab loads off-thread from Guru live."""
        super().__init__(parent)
        self._provider = health_provider
        self._dismissed: set[str] = set()
        self.setStyleSheet(f"background:{ALMA_CREAM};")
        self._build()

    # ── layout ──────────────────────────────────────────────────────

    def _build(self):
        self._outer = QVBoxLayout(self)
        self._outer.setContentsMargins(20, 16, 20, 18)
        self._outer.setSpacing(12)

        head = QHBoxLayout()
        head.setSpacing(10)
        head.addWidget(section_label("WHAT NEEDS ATTENTION"))
        head.addStretch(1)
        refresh = QPushButton("Refresh")
        refresh.setCursor(Qt.PointingHandCursor)
        refresh.setStyleSheet(
            f"QPushButton{{background:{ALMA_GREEN_DARK}; color:{ALMA_TEXT_ON_DARK}; "
            "border:none; border-radius:8px; padding:7px 16px; font-size:12px; "
            "font-weight:600; }"
        )
        refresh.clicked.connect(self.reload)
        head.addWidget(refresh)
        self._outer.addLayout(head)

        self._body = QVBoxLayout()
        self._body.setSpacing(12)
        self._outer.addLayout(self._body, 1)
        self._outer.addStretch(1)
        self._show_message("Computing what needs attention…")

    # ── public API ──────────────────────────────────────────────────

    def reload(self):
        """Recompute health and re-render — off-thread unless a provider is
        injected (tests). Always shows the cold-start message first.

        A reload is a deliberate "show me the current state", so it clears
        session dismissals: a row you dismissed comes back on an explicit
        Refresh (the recovery path), and dismiss means "not now", not "hide
        until I restart the app". Previously ``_dismissed`` survived reload but
        not restart — a confusing halfway state that matched neither."""
        self._dismissed.clear()
        self._show_message("Computing what needs attention…")
        if self._provider is not None:
            self.set_health(list(self._provider() or []))
            return
        threading.Thread(target=self._load_worker, daemon=True).start()

    def set_health(self, health: list):
        """Render the buckets from a scored card list (main-thread only)."""
        groups = group_by_bucket(health)
        if not any(groups.values()):
            self._show_message("All clear — nothing needs attention right now.")
            return
        self._clear_body()
        for key, heading, label, kind in _BUCKETS:
            rows = [c for c in groups[key]
                    if getattr(c, "card_id", "") not in self._dismissed]
            if rows:
                self._body.addWidget(self._section(heading, label, kind, rows))
        if self._body.count() == 0:
            self._show_message("All clear — nothing needs attention right now.")

    def show_not_connected(self):
        """Friendly empty state when Guru has no saved credentials."""
        self._show_message("Connect Guru in Settings to see what needs attention.")

    # ── worker (off-thread; decoupled: get_connection + GuruClient) ──

    health_ready = Signal(list)
    health_failed = Signal(str)

    def _load_worker(self):
        """Build a connection + GuruClient and call compute_health. Runs on a
        worker thread — emits a signal, never touches a widget directly."""
        try:
            health = self._compute_off_thread()
        except _NotConnected:
            self._safe_emit(self.health_failed, "not_connected")
            return
        except Exception as exc:  # noqa: BLE001 — surface, don't crash the UI
            self._safe_emit(self.health_failed, str(exc))
            return
        self._safe_emit(self.health_ready, health)

    @staticmethod
    def _safe_emit(signal, *args):
        # The user may navigate away (tab destroyed) before this background load
        # finishes — emitting from a deleted QObject raises RuntimeError. Drop it.
        try:
            signal.emit(*args)
        except RuntimeError:
            pass

    @staticmethod
    def _compute_off_thread() -> list:
        """The actual decoupled load: GuruClient + get_connection + compute_health
        + locally-staged drafts. Raises ``_NotConnected`` when Guru is unset."""
        from src.data.connection_factory import get_connection
        from src.data.enablement_health import compute_health
        from src.data.guru_client import GuruClient

        email, token = GuruClient.load_credentials()
        if not (email and token):
            raise _NotConnected()
        conn = get_connection()  # defaults to the local warehouse DB
        try:
            health = list(compute_health(conn, GuruClient(email, token)))
            return health + _staged_draft_cards(conn)
        finally:
            conn.close()

    def connect_signals(self):
        """Wire the worker signals to the render slots (call once, post-construct).
        Kept separate so a test can construct the tab without a live thread."""
        self.health_ready.connect(self.set_health)
        self.health_failed.connect(self._on_failed)

    def _on_failed(self, reason: str):
        if reason == "not_connected":
            self.show_not_connected()
        else:
            self._show_message(f"Couldn't load attention queue: {reason}")

    # ── section / row builders (small, single-purpose) ──────────────

    def _section(self, heading: str, label: str, kind: str, rows: list) -> QFrame:
        card = card_frame()
        col = QVBoxLayout(card)
        col.setContentsMargins(16, 12, 16, 12)
        col.setSpacing(6)
        col.addWidget(self._section_head(heading, len(rows)))
        for health in rows:
            col.addWidget(self._row(health, label, kind))
        return card

    def _section_head(self, heading: str, count: int) -> QWidget:
        row = QFrame()
        row.setStyleSheet("background:transparent; border:none;")
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)
        h.addWidget(section_label(heading))
        h.addWidget(badge(str(count), "normal"))
        h.addStretch(1)
        return row

    def _row(self, health, label: str, kind: str) -> QFrame:
        card_id = getattr(health, "card_id", "")
        row = QFrame()
        row.setStyleSheet("background:transparent; border:none;")
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 2, 0, 2)
        h.setSpacing(10)
        h.addWidget(badge(label, kind))
        h.addWidget(self._title_label(_title_of(health)), 1)
        h.addWidget(self._score_label(getattr(health, "score", 0.0)))
        h.addWidget(self._open_button(card_id))
        h.addWidget(self._dismiss_button(card_id, row))
        return row

    def _title_label(self, text: str) -> QLabel:
        lbl = QLabel(text[:70])
        lbl.setStyleSheet(
            f"color:{ALMA_TEXT_DARK}; font-size:12.5px; font-weight:600; border:none;")
        return lbl

    def _score_label(self, score: float) -> QLabel:
        lbl = QLabel(f"{score:.0%}")
        lbl.setStyleSheet(
            f"color:{ALMA_TEXT_MID}; font-size:11.5px; font-weight:700; border:none;")
        return lbl

    def _open_button(self, card_id: str) -> QPushButton:
        btn = QPushButton("Open targeted update")
        btn.setCursor(Qt.PointingHandCursor)
        btn.setStyleSheet(
            f"QPushButton{{background:{ALMA_BG_ELEVATED}; color:{ALMA_ACCENT_TEAL}; "
            f"border:1px solid {ALMA_ACCENT_TEAL}; border-radius:7px; "
            "padding:4px 10px; font-size:11px; font-weight:600; }"
        )
        btn.clicked.connect(
            lambda _=False, cid=card_id: self._emit_open(cid))
        return btn

    def _dismiss_button(self, card_id: str, row: QFrame) -> QPushButton:
        btn = QPushButton("Dismiss")
        btn.setCursor(Qt.PointingHandCursor)
        btn.setStyleSheet(
            f"QPushButton{{background:transparent; color:{ALMA_TEXT_LIGHT}; "
            f"border:1px solid {ALMA_BORDER}; border-radius:7px; "
            "padding:4px 10px; font-size:11px; font-weight:600; }"
        )
        btn.clicked.connect(
            lambda _=False, cid=card_id, r=row: self._on_dismiss(cid, r))
        return btn

    # ── actions ─────────────────────────────────────────────────────

    def _emit_open(self, card_id: str):
        if card_id:
            self.open_update_requested.emit(card_id)

    def _on_dismiss(self, card_id: str, row: QFrame):
        if card_id:
            self._dismissed.add(card_id)
            self.dismiss_requested.emit(card_id)
        row.hide()

    # ── empty / message states ──────────────────────────────────────

    def _show_message(self, text: str):
        self._clear_body()
        lbl = QLabel(text)
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setStyleSheet(
            f"color:{ALMA_TEXT_LIGHT}; font-size:13px; border:none; padding:28px 0;")
        self._body.addWidget(lbl)

    def _clear_body(self):
        while self._body.count():
            item = self._body.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()
                w.deleteLater()


class _NotConnected(Exception):
    """Raised by the loader when Guru has no saved credentials."""


def _staged_draft_cards(conn) -> list:
    """Pending enablement drafts → CardHealth rows in the ``staged`` bucket.

    Enablement-local only (guru_content_drafts via enablement_store); a draft
    has no health score, so it lands at 0.0 to sort to the top of its section.
    """
    from src.data import enablement_store as store
    from src.data.enablement_health import CardHealth, CardSignals
    cards = []
    for draft in store.list_drafts(conn, status="pending"):
        did = str(draft.get("id"))
        title = (draft.get("title") or "Untitled").split(" — ")[0]
        cards.append(CardHealth(
            card_id=f"draft:{did}",
            score=0.0,
            bucket="staged",
            components={},
            signals=CardSignals(card_id=f"draft:{did}", title=title),
        ))
    return cards
