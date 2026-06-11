"""
Bug Bash — delivery_drop provider label.

The 'delivery_drop' scan event hardcoded "Gemini signaled done", so under
Claude/Bedrock it mislabeled which provider produced the partial batch (this
actively misled the Run 3 performance analysis). These tests pin the message to
the active provider.
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from src.agents.scan_orchestrator import ScanOrchestrator


def _orch_with_spy(seeded_db, model):
    orch = ScanOrchestrator(db_path=str(seeded_db.db_path))
    orch._model = model                       # simulate the model the scan resolved
    captured = []
    orch._emit_event = (
        lambda scan_id, event_type, status, message, **kw: captured.append(
            (event_type, message)
        )
    )
    return orch, captured


def _fire_partial_batch(orch, seeded_db):
    """Drive _record_batch_result down the 'partial' (delivery-lie) branch."""
    batch = {"batch_id": "b-test", "retry_count": 0}
    result = {
        "classified": 2,
        "dropped_ids": ["t3", "t4", "t5"],          # non-empty → status 'partial'
        "delivered_ids": ["t1", "t2"],
        "requested_ids": ["t1", "t2", "t3", "t4", "t5"],
        "input_tokens": 100,
        "output_tokens": 50,
    }
    try:
        orch._record_batch_result(
            None, seeded_db.conn, "scan-test", batch, 1, "CLM-001",
            ["t1", "t2", "t3", "t4", "t5"], result, 5.0, False,
        )
    except Exception:
        pass  # tolerate post-emit side effects (supervisor=None); we only need the event


def test_delivery_drop_reflects_claude(seeded_db):
    """Under a Claude model the partial-batch message must NOT say Gemini."""
    orch, captured = _orch_with_spy(seeded_db, "claude-haiku-4-5-20251001")
    _fire_partial_batch(orch, seeded_db)
    drops = [m for (et, m) in captured if et == "delivery_drop"]
    assert drops, "no delivery_drop event emitted"
    assert "Claude" in drops[0], f"expected provider 'Claude' in message: {drops[0]!r}"
    assert "Gemini" not in drops[0], f"message still hardcodes Gemini: {drops[0]!r}"


def test_delivery_drop_reflects_gemini(seeded_db):
    """Under a Gemini model the message must still say Gemini (no regression)."""
    orch, captured = _orch_with_spy(seeded_db, "gemini-2.5-flash")
    _fire_partial_batch(orch, seeded_db)
    drops = [m for (et, m) in captured if et == "delivery_drop"]
    assert drops, "no delivery_drop event emitted"
    assert "Gemini" in drops[0], f"expected provider 'Gemini' in message: {drops[0]!r}"
