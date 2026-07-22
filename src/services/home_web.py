"""Controller for the web Home page — the Python half of the ``#/home`` route.

Mirrors ``src/ui/pages/home_page.HomePage``'s public surface (``set_mode`` +
the three outbound signals) so ``MainWindow._create_home_page`` wires either
implementation identically and every downstream route in ``main_window``
stays untouched.

**Pure-renderer rule.** Every query, every date decision and every display
string is built here; the page receives finished text. The greeting word, the
thousands separator, the 110-char title clamp and the ``ts[:16]`` timestamp
are all Python-side so the web and Qt surfaces render byte-identical output.

**QWebChannel is the trust boundary.** ``HomeBridge`` exposes these methods to
any script in the page, so nothing here trusts its arguments. Reads replay a
cached viewmodel. The one authority-bearing action — switching app mode, which
persists ``app.last_mode`` and rebuilds the sidebar and page mounts — runs the
full gate: allowlist against Python-held state, single-winner claim taken
BEFORE the dialog, a NATIVE confirm no page script can reach, and a deferred
dispatch so ``MainWindow.switch_mode`` never executes on the slot's stack
frame while it tears down the widget hosting the caller.
"""

from __future__ import annotations

import json
from datetime import datetime

from PySide6.QtCore import QObject, Signal

from src.branding import (
    CONTENT_COMMAND_CENTER, CONTENT_COMMAND_CENTER_DESCRIPTION,
)
from src.ui import app_modes

_ACCENT = "#0D7D72"

# Presentation constants are duplicated from home_page.py rather than imported:
# src/services must not depend on src/ui. The guard against drift is the
# parity test in tests/test_home_web_controller.py, which asserts this
# controller and the live HomePage agree on the same DB fixture.
_KIND_TINT = {
    "Chat": ("#E8F0FE", "#1D4ED8"),
    "Report": ("#F3E8FF", "#6D28D9"),
    "Task": ("#E5F3EC", _ACCENT),
}
_KIND_ICON = {"Chat": "chat", "Report": "doc", "Task": "tasks"}
_KIND_FALLBACK = ("#EEEEEE", "#4A4A4A")
_VALID_KINDS = tuple(_KIND_TINT)

_MODE_ICON = {app_modes.MODE_PRODUCT: "pie", app_modes.MODE_ENABLEMENT: "pen"}

_MODE_TILES = (
    (app_modes.MODE_PRODUCT, "Product",
     "Full analytics suite — conversations, TRC analytics, trending, "
     "incidents, and AI reporting."),
    (app_modes.MODE_ENABLEMENT, "Enablement",
     "Content Command Center — connects Asana, Guru, and Zendesk to review "
     "requests, draft updates, and publish content in one workflow. Skips "
     "the heavy analytics stack."),
)

_QUICK_ACTIONS = {
    app_modes.MODE_PRODUCT: [
        ("search", "Run a search", "chat"),
        ("reports", "Open AI Reports", "doc"),
    ],
    app_modes.MODE_ENABLEMENT: [
        ("workbench", "Open Workbench", "pen"),
        ("calendar", "Open Calendar", "calendar"),
        ("renn", "Chat with Renn", "chat"),
    ],
}

_STAT_SPECS = {
    app_modes.MODE_ENABLEMENT: [
        ("Pending drafts",
         "SELECT COUNT(*) FROM guru_content_drafts WHERE status='pending'"),
        ("Open tasks",
         "SELECT COUNT(*) FROM enablement_tasks "
         "WHERE status NOT IN ('done','dismissed')"),
        ("Decks", "SELECT COUNT(*) FROM pptx_decks"),
    ],
    app_modes.MODE_PRODUCT: [
        ("Tickets", "SELECT COUNT(*) FROM tickets"),
        ("Reports", "SELECT COUNT(*) FROM analysis_reports"),
        ("Chats", "SELECT COUNT(*) FROM chat_sessions"),
    ],
}

_ACTIVITY_QUERIES = (
    ("Chat",
     "SELECT COALESCE(title, source_page, 'Chat session'), updated_at "
     "FROM chat_sessions ORDER BY updated_at DESC LIMIT 8"),
    ("Report",
     "SELECT page || ' — ' || substr(summary, 1, 80), run_at "
     "FROM analysis_reports ORDER BY run_at DESC LIMIT 8"),
    ("Task",
     "SELECT title, COALESCE(updated_at, created_at, '') "
     "FROM enablement_tasks WHERE status != 'dismissed' "
     "ORDER BY COALESCE(updated_at, created_at, '') DESC LIMIT 8"),
)

_SUBTITLE = "Choose a workspace, or pick up where you left off."
_EMPTY_ACTIVITY = "No recent activity yet — run a search or scan to get started."
_TITLE_CAP = 110


class HomeWebController(QObject):
    """Builds the Home viewmodel and gates the one action that carries authority.

    ``mode_selected`` / ``quick_action`` / ``activity_activated`` are the same
    three signals ``HomePage`` emits, so ``MainWindow`` connects them to the
    same handlers and every existing route keeps working.
    """

    home_data = Signal(str)             # JSON viewmodel (see _build_payload)
    mode_selected = Signal(str)
    quick_action = Signal(str)
    activity_activated = Signal(str)    # activity kind (Chat/Report/Task)

    def __init__(self, db, current_mode: str = app_modes.MODE_PRODUCT,
                 confirm_fn=None, defer_fn=None, now_fn=None, parent=None):
        super().__init__(parent)
        self.db = db
        self._mode = (current_mode if current_mode in app_modes.MODES
                      else app_modes.MODE_PRODUCT)
        # ``confirm_fn(mode) -> bool`` is the NATIVE human gate (a QMessageBox
        # in main_window, physically unreachable from Chromium). Absent → the
        # mode-switch gate FAILS CLOSED: no confirm possible, no switch, ever.
        self._confirm_fn = confirm_fn
        # ``defer_fn(callable)`` pushes the switch off the slot's stack frame.
        # Injectable so tests can prove the deferral rather than infer it.
        self._defer_fn = defer_fn
        # ``now_fn()`` is test-pinnable, like calendar.py's ``_today``.
        self._now_fn = now_fn
        self._switch_inflight = False
        self._last_payload: str | None = None

    # ── the HomePage-compatible surface MainWindow drives ───────────

    def set_mode(self, mode: str):
        """Mirror ``HomePage.set_mode``: re-skin tiles, quick actions, stats."""
        if mode not in app_modes.MODES:
            return
        self._mode = mode
        self._push()

    def refresh(self):
        """Re-query and re-push. The WebHost's showEvent hook calls this, the
        way ``HomePage.showEvent`` calls ``HomePage.refresh``."""
        self._push()

    def request_refresh(self):
        """The page mounted (or remounted) → replay state.

        Ungated read. Replays the cached payload when one exists so a remount
        is free; otherwise builds one.
        """
        if self._last_payload is not None:
            self.home_data.emit(self._last_payload)
            return
        self._push()

    # ── viewmodel ───────────────────────────────────────────────────

    def _push(self):
        payload = json.dumps(self._build_payload(), default=str)
        self._last_payload = payload
        self.home_data.emit(payload)

    def _build_payload(self) -> dict:
        return {
            "mode": self._mode,
            "greeting": self._greeting(),
            "subtitle": _SUBTITLE,
            # Enablement-only Content Command Center banner; None in product.
            # Strings come from src.branding — the same constants the native
            # HomePage renders, so the two surfaces cannot drift.
            "banner": (
                {"title": CONTENT_COMMAND_CENTER,
                 "desc": CONTENT_COMMAND_CENTER_DESCRIPTION}
                if self._mode == app_modes.MODE_ENABLEMENT else None
            ),
            "empty_activity": _EMPTY_ACTIVITY,
            "tiles": [
                {
                    "key": mode,
                    "title": title,
                    "desc": desc,
                    "icon": _MODE_ICON.get(mode, "home"),
                    "active": mode == self._mode,
                }
                for mode, title, desc in _MODE_TILES
            ],
            "stats": self._stats(),
            "quick_actions": [
                {"key": key, "label": label, "icon": icon}
                for key, label, icon in _QUICK_ACTIONS.get(self._mode, [])
            ],
            "activity": self._activity(),
        }

    def _greeting(self) -> str:
        now = self._now_fn() if self._now_fn is not None else datetime.now()
        hour = now.hour
        word = "morning" if hour < 12 else ("afternoon" if hour < 17
                                            else "evening")
        return f"Good {word}"

    def _conn(self):
        return getattr(self.db, "conn", None)

    def _stats(self) -> list[dict]:
        """Mode-specific at-a-glance counts.

        Each query is individually guarded exactly as ``HomePage._stats`` does:
        a missing table yields 0, never an exception. ``available`` records
        whether the query actually ran — carried in the viewmodel for future
        use but deliberately ignored by the v1 UI so rendering matches the
        native page exactly.
        """
        conn = self._conn()
        if conn is None:
            return []
        out = []
        for label, sql in _STAT_SPECS.get(self._mode, []):
            try:
                value = int(conn.execute(sql).fetchone()[0])
                available = True
            except Exception:  # noqa: BLE001 — missing table / schema drift
                value, available = 0, False
            out.append({
                "label": label,
                "caption": label.upper(),
                "value": value,
                "display": f"{value:,}",
                "available": available,
            })
        return out

    def _activity(self) -> list[dict]:
        """The merged recent-activity feed, formatted for display.

        NOTE: the sort is a STRING compare on the raw timestamp, preserved
        bug-for-bug from ``HomePage._recent_activity`` (home_page.py:375) so
        the two surfaces order identically. Fixing the ordering is a separate,
        separately-tested change — doing it here would make any regression
        unattributable.
        """
        conn = self._conn()
        if conn is None:
            return []
        rows = []
        for kind, sql in _ACTIVITY_QUERIES:
            try:
                for title, ts in conn.execute(sql).fetchall():
                    rows.append((kind, str(title or ""), str(ts or "")))
            except Exception:  # noqa: BLE001 — never break Home
                continue
        rows.sort(key=lambda r: r[2], reverse=True)
        out = []
        for kind, title, ts in rows[:8]:
            bg, fg = _KIND_TINT.get(kind, _KIND_FALLBACK)
            out.append({
                "kind": kind,
                "title": title[:_TITLE_CAP],
                "ts_display": str(ts)[:16].replace("T", "  "),
                "icon": _KIND_ICON.get(kind, "doc"),
                "tint_bg": bg,
                "tint_fg": fg,
            })
        return out

    # ── inbound from JS (untrusted — validate everything) ───────────

    def js_quick_action(self, key):
        """A quick-action chip was clicked.

        Validated against the actions for the mode PYTHON holds, never a mode
        the page claims. Navigation carries no authority, so there is no
        confirm — but an unknown key is a silent no-op rather than a
        pass-through, so a forged call cannot reach a route this mode does not
        offer.
        """
        wanted = str(key or "")
        valid = {k for k, _label, _icon in _QUICK_ACTIONS.get(self._mode, [])}
        if wanted not in valid:
            return
        self.quick_action.emit(wanted)

    def js_activity_activated(self, kind):
        """A recent-activity row was clicked. Only the three known kinds route;
        anything else is a silent no-op. ``MainWindow._on_home_activity``
        applies its own mode-aware destination map unchanged."""
        wanted = str(kind or "")
        if wanted not in _VALID_KINDS:
            return
        self.activity_activated.emit(wanted)

    def js_request_mode_switch(self, mode):
        """Switch app mode — the one slot on Home that carries real authority.

        ``MainWindow.switch_mode`` persists ``app.last_mode``, stops and starts
        services, and rebuilds the sidebar and page mounts *around the live
        WebHost that is hosting the caller*. So the sequence is strictly:

        1. allowlist the value against ``app_modes.MODES``
        2. reject a switch to the mode Python already holds (not the page's)
        3. claim a single-winner flag BEFORE the dialog — the modal spins a
           nested event loop, so a second invoke during it must find the claim
        4. NATIVE confirm, unreachable from any page script; absent → closed
        5. dispatch DEFERRED, so ``switch_mode`` never runs on this stack frame

        Forged or redundant input is a silent no-op: no signal, no resolution,
        no oracle for a caller probing the boundary.
        """
        wanted = str(mode or "")
        if wanted not in app_modes.MODES:
            return
        if wanted == self._mode:
            return
        if self._switch_inflight:
            return
        if self._confirm_fn is None:
            return                      # fail closed — no gate, no switch
        self._switch_inflight = True
        try:
            approved = bool(self._confirm_fn(wanted))
        except Exception:  # noqa: BLE001 — a broken dialog must not switch
            self._switch_inflight = False
            return
        try:
            if not approved:
                return
            self._dispatch(lambda: self.mode_selected.emit(wanted))
        finally:
            self._switch_inflight = False

    def _dispatch(self, fn):
        """Run ``fn`` off the current stack frame.

        Load-bearing, not stylistic: undeferred, ``switch_mode`` would tear
        down and rebuild the page mounts while the QWebChannel is still
        walking the slot that invoked it, freeing objects underneath it.
        """
        if self._defer_fn is not None:
            self._defer_fn(fn)
            return
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, fn)
