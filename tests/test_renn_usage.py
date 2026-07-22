"""Renn usage metering — recorder, bridge sink, engine wiring, panel.

Covers the full chain that feeds the enablement Usage tab:
CLI turn → ClaudeCliBridge._log_usage_if_configured → RennUsageRecorder →
gemini_usage rows → renn_usage.usage_summary → RennUsagePanel.

Hermetic: throwaway warehouses, no CLI subprocess, offscreen Qt only where
a widget is actually built.
"""

from __future__ import annotations

import os
from datetime import datetime
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from src.data import renn_usage  # noqa: E402
from src.data.renn_usage import RennUsageRecorder  # noqa: E402

pytestmark = pytest.mark.ui


# ── RennUsageRecorder ─────────────────────────────────────────────────

def _rows(conn):
    return [tuple(r) for r in conn.execute(
        "SELECT source, tokens_in, tokens_out, cost_usd, model "
        "FROM gemini_usage ORDER BY id").fetchall()]


def test_recorder_writes_a_renn_chat_row_with_real_cost(empty_db):
    rec = RennUsageRecorder(empty_db.db_path)
    rec.log_call(tokens_in=120, tokens_out=40,
                 model="claude-sonnet-4-6", cost_usd=0.0123)
    rows = _rows(empty_db.conn)
    assert rows == [("renn_chat", 120, 40, 0.0123, "claude-sonnet-4-6")]


def test_recorder_records_zero_cost_when_cli_reports_none(empty_db):
    """A subscription CLI login reports no dollar figure — the row must say
    0.0, never a Gemini-priced estimate for a Claude turn."""
    rec = RennUsageRecorder(empty_db.db_path)
    rec.log_call(tokens_in=500, tokens_out=100, model="claude-haiku-4-5")
    assert _rows(empty_db.conn)[0][3] == 0.0


def test_recorder_never_raises_on_a_broken_path(tmp_path):
    rec = RennUsageRecorder(tmp_path / "missing" / "nowhere.db")
    rec.log_call(tokens_in=1, tokens_out=1)   # must not raise


def test_recorder_estimate_matches_tracker_heuristic():
    from src.data.usage_tracker import UsageTracker
    text = "x" * 400
    assert RennUsageRecorder.estimate_tokens(text) == \
        UsageTracker.estimate_tokens(text)


# ── UsageTracker cost passthrough (shared ledger writer) ──────────────

def test_usage_tracker_uses_a_real_cost_when_given(empty_db):
    from src.data.usage_tracker import UsageTracker
    tracker = UsageTracker(empty_db)
    tracker.log_call(source="renn_chat", tokens_in=10, tokens_out=5,
                     model="claude-sonnet-4-6", cost_usd=0.5)
    assert _rows(empty_db.conn)[0][3] == 0.5


def test_usage_tracker_still_estimates_without_a_cost(empty_db):
    from src.data.usage_tracker import UsageTracker
    tracker = UsageTracker(empty_db)
    tracker.log_call(source="nlp_scan", tokens_in=1_000_000, tokens_out=0)
    assert _rows(empty_db.conn)[0][3] > 0.0   # Gemini plan pricing applied


# ── bridge sink (source + cost flow through) ──────────────────────────

class _SinkSpy:
    def __init__(self):
        self.calls = []

    def estimate_tokens(self, text):
        return 999   # the fallback marker — must NOT appear for real counts

    def log_call(self, **kw):
        self.calls.append(kw)


def _stream(input_tokens=100, output_tokens=20, cost=0.01, error=None):
    parser = SimpleNamespace(input_tokens=input_tokens,
                             output_tokens=output_tokens,
                             cost_usd=cost, full_text="hi", error=error)
    return SimpleNamespace(parser=parser)


def _bridge_stub(sink=None, source=None):
    from src.agents.claude_cli_bridge import ClaudeCliBridge
    stub = SimpleNamespace(_usage_tracker=None, _usage_source="nlp_scan",
                           _scan_id=None, _model="claude-sonnet-4-6")
    if sink is not None:
        ClaudeCliBridge.set_usage_sink(stub, sink, source=source or "nlp_scan")
    stub.log = lambda stream, prompt: \
        ClaudeCliBridge._log_usage_if_configured(stub, stream, prompt)
    return stub


def test_bridge_logs_with_the_configured_source_and_real_cost():
    sink = _SinkSpy()
    stub = _bridge_stub(sink, source="renn_chat")
    stub.log(_stream(input_tokens=150, output_tokens=30, cost=0.02), "prompt")
    assert sink.calls == [{
        "source": "renn_chat", "tokens_in": 150, "tokens_out": 30,
        "scan_id": None, "model": "claude-sonnet-4-6", "cost_usd": 0.02,
    }]


def test_bridge_default_source_stays_nlp_scan():
    """scan_orchestrator assigns _usage_tracker directly and relies on the
    historical source — the default must not move under it."""
    sink = _SinkSpy()
    stub = _bridge_stub()
    stub._usage_tracker = sink          # the orchestrator's direct-attr path
    stub.log(_stream(), "prompt")
    assert sink.calls[0]["source"] == "nlp_scan"


def test_bridge_passes_none_cost_when_cli_reported_zero():
    sink = _SinkSpy()
    stub = _bridge_stub(sink, source="renn_chat")
    stub.log(_stream(cost=0.0), "prompt")
    assert sink.calls[0]["cost_usd"] is None


def test_bridge_skips_logging_on_error_turns():
    sink = _SinkSpy()
    stub = _bridge_stub(sink, source="renn_chat")
    stub.log(_stream(error="claude_cli_error"), "prompt")
    assert sink.calls == []


def test_cli_client_applies_the_sink_to_its_live_bridge():
    from src.llm.claude_cli_client import ClaudeCliClient
    applied = []
    fake_bridge = SimpleNamespace(
        set_usage_sink=lambda sink, source="nlp_scan":
            applied.append((sink, source)))
    client = SimpleNamespace(_bridge=fake_bridge)
    sink = _SinkSpy()
    ClaudeCliClient.set_usage_sink(client, sink, source="renn_chat")
    assert applied == [(sink, "renn_chat")]
    # Stored for _ensure_bridge to re-apply after a bridge respawn.
    assert client._usage_sink is sink
    assert client._usage_source == "renn_chat"


# ── chat engine wiring (only Renn's path gets metered) ────────────────

def _engine_stub(task_type, db_path):
    from src.services.chat_engine import ChatEngine
    stub = SimpleNamespace(_task_type=task_type, _db_path=db_path)
    stub.attach = lambda client: ChatEngine._attach_usage_sink(stub, client)
    return stub


class _ClientSpy:
    def __init__(self):
        self.sinks = []

    def set_usage_sink(self, sink, source="renn_chat"):
        self.sinks.append((sink, source))


def test_engine_attaches_recorder_for_enablement_chat(tmp_path):
    client = _ClientSpy()
    _engine_stub("enablement_chat", str(tmp_path / "w.db")).attach(client)
    assert len(client.sinks) == 1
    sink, source = client.sinks[0]
    assert isinstance(sink, RennUsageRecorder)
    assert source == "renn_chat"
    assert sink.db_path == str(tmp_path / "w.db")


def test_engine_leaves_product_chat_unmetered(tmp_path):
    client = _ClientSpy()
    _engine_stub("report_generation", str(tmp_path / "w.db")).attach(client)
    assert client.sinks == []


def test_engine_skips_without_a_db_path():
    client = _ClientSpy()
    _engine_stub("enablement_chat", None).attach(client)
    assert client.sinks == []


def test_engine_tolerates_clients_without_the_hook(tmp_path):
    _engine_stub("enablement_chat", str(tmp_path / "w.db")).attach(object())


# ── summary queries ───────────────────────────────────────────────────

def _seed(conn, date, hour, tin, tout, cost=0.0, source="renn_chat",
          model="claude-sonnet-4-6", created=None):
    conn.execute(
        "INSERT INTO gemini_usage (date, hour, source, scan_id, tokens_in, "
        "tokens_out, cost_usd, api_calls, model, created_at) "
        "VALUES (?, ?, ?, NULL, ?, ?, ?, 1, ?, ?)",
        (date, hour, source, tin, tout, cost, model,
         created or f"{date}T{hour:02d}:00:00"))


def test_usage_summary_aggregates_and_excludes_scan_rows(empty_db):
    conn = empty_db.conn
    today = datetime(2026, 7, 22, 12, 0)
    _seed(conn, "2026-07-22", 9, 100, 50, cost=0.01)
    _seed(conn, "2026-07-20", 10, 200, 80)
    _seed(conn, "2026-06-20", 11, 400, 100)   # outside the trailing 30 days
    _seed(conn, "2026-07-22", 9, 9999, 9999, source="nlp_scan")   # excluded
    conn.commit()

    s = renn_usage.usage_summary(conn, today=today)
    assert s["today"] == {"turns": 1, "tokens_in": 100, "tokens_out": 50,
                          "cost_usd": 0.01}
    assert s["week"]["turns"] == 2
    assert s["month"]["turns"] == 2
    assert s["all_time"]["turns"] == 3
    # The scanner's rows never leak into Renn's numbers.
    assert s["all_time"]["tokens_in"] == 700


def test_daily_series_is_zero_filled_and_ordered(empty_db):
    conn = empty_db.conn
    today = datetime(2026, 7, 22, 12, 0)
    _seed(conn, "2026-07-22", 9, 1, 1)
    _seed(conn, "2026-07-22", 10, 1, 1)
    _seed(conn, "2026-07-15", 10, 1, 1)
    conn.commit()
    series = renn_usage.daily_series(conn, 14, today=today)
    assert len(series) == 14
    assert series[0][0] == "2026-07-09" and series[-1][0] == "2026-07-22"
    counts = dict(series)
    assert counts["2026-07-22"] == 2
    assert counts["2026-07-15"] == 1
    assert counts["2026-07-10"] == 0


def test_recent_turns_newest_first(empty_db):
    conn = empty_db.conn
    _seed(conn, "2026-07-21", 9, 1, 1, created="2026-07-21T09:00:00")
    _seed(conn, "2026-07-22", 8, 2, 2, created="2026-07-22T08:00:00")
    conn.commit()
    recent = renn_usage.recent_turns(conn, 8)
    assert [r["date"] for r in recent] == ["2026-07-22", "2026-07-21"]


def test_usage_summary_survives_a_broken_connection():
    class _Boom:
        def execute(self, *a):
            raise RuntimeError("db gone")
    s = renn_usage.usage_summary(_Boom())
    assert s["today"]["turns"] == 0 and s["series"] == []


# ── the panel itself ──────────────────────────────────────────────────

@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


def _labels(widget):
    from PySide6.QtWidgets import QLabel
    return [lbl.text() for lbl in widget.findChildren(QLabel)]


def test_panel_renders_seeded_activity(qapp, empty_db):
    from src.ui.pages.enablement.usage_tab import RennUsagePanel
    now = datetime.now()
    _seed(empty_db.conn, now.strftime("%Y-%m-%d"), now.hour, 1200, 300,
          cost=0.05)
    empty_db.conn.commit()
    panel = RennUsagePanel(empty_db)
    texts = _labels(panel)
    assert "1" in texts                       # one turn today
    assert any("1.20k in" in t for t in texts)
    assert "$0.05" in texts
    assert any("renn_chat" in t for t in texts)   # recent-row pill


def test_panel_empty_state(qapp, empty_db):
    from src.ui.pages.enablement.usage_tab import RennUsagePanel
    panel = RennUsagePanel(empty_db)
    texts = _labels(panel)
    assert "Nothing metered yet." in texts
    assert any("$0 on subscription login" in t for t in texts)


def test_panel_formatters():
    from src.ui.pages.enablement.usage_tab import _fmt_cost, _fmt_tokens
    assert _fmt_tokens(950) == "950"
    assert _fmt_tokens(1_500) == "1.50k"
    assert _fmt_tokens(12_340) == "12.3k"
    assert _fmt_tokens(2_400_000) == "2.4M"
    assert _fmt_cost(0.0) == "$0.00"
    assert _fmt_cost(0.0042) == "$0.0042"
    assert _fmt_cost(1.5) == "$1.50"
