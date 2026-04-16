"""
Unit tests for src/updater/rollback.py

Covers:
  - record_apply writes rollback_state.json + moves _*_backup → _*_previous
  - needs_rollback returns False when no state, expired grace window,
    or crashes below threshold
  - needs_rollback returns True when crashes ≥ 3 within grace window
  - perform_rollback restores previous dirs and clears state
  - clear_state_if_stable removes previous dirs after grace expiry

Run: python -m pytest tests/test_rollback.py -x -v
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.updater import rollback


# ──────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────

@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """Redirect all rollback paths to a tmp_path sandbox."""
    monkeypatch.setattr(rollback, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(rollback, "_ROLLBACK_STATE",
                         tmp_path / "data" / "rollback_state.json")
    monkeypatch.setattr(rollback, "_CRASH_DIR",
                         tmp_path / "data" / "crash_reports")
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "crash_reports").mkdir(parents=True, exist_ok=True)
    yield tmp_path


def _write_crash_file(crash_dir: Path, when: datetime, idx: int) -> Path:
    f = crash_dir / f"crash_{idx}.json"
    f.write_text('{"exception_type":"RuntimeError"}')
    import os
    ts = when.timestamp()
    os.utime(f, (ts, ts))
    return f


# ──────────────────────────────────────────────────────────────────
# record_apply
# ──────────────────────────────────────────────────────────────────

class TestRecordApply:
    def test_writes_state_file(self, sandbox):
        rollback.record_apply(previous_version="9.2.0", new_version="9.3.0")
        state = rollback.current_state()
        assert state is not None
        assert state["previous"] == "9.2.0"
        assert state["new"] == "9.3.0"
        assert "applied_at" in state
        assert state["grace_window_s"] == 60

    def test_moves_backup_to_previous(self, sandbox):
        for name in ("src", "config", "migrations"):
            (sandbox / f"_{name}_backup").mkdir()
            (sandbox / f"_{name}_backup" / "marker.txt").write_text(name)

        rollback.record_apply("9.2.0", "9.3.0")

        for name in ("src", "config", "migrations"):
            previous = sandbox / f"_{name}_previous"
            backup = sandbox / f"_{name}_backup"
            assert previous.is_dir()
            assert (previous / "marker.txt").read_text() == name
            assert not backup.exists()

    def test_custom_grace_window_persisted(self, sandbox):
        rollback.record_apply("a", "b", grace_window_s=120)
        assert rollback.current_state()["grace_window_s"] == 120


# ──────────────────────────────────────────────────────────────────
# needs_rollback
# ──────────────────────────────────────────────────────────────────

class TestNeedsRollback:
    def test_no_state_means_no_rollback(self, sandbox):
        should, _ = rollback.needs_rollback()
        assert should is False

    def test_crashes_below_threshold_no_rollback(self, sandbox):
        rollback.record_apply("9.2.0", "9.3.0")
        now = datetime.now(timezone.utc)
        _write_crash_file(sandbox / "data" / "crash_reports", now, 1)
        _write_crash_file(sandbox / "data" / "crash_reports", now, 2)

        should, reason = rollback.needs_rollback()
        assert should is False
        assert "2" in reason or "under threshold" in reason

    def test_crashes_at_threshold_triggers_rollback(self, sandbox):
        rollback.record_apply("9.2.0", "9.3.0")
        now = datetime.now(timezone.utc)
        for i in range(3):
            _write_crash_file(sandbox / "data" / "crash_reports", now, i)

        should, reason = rollback.needs_rollback()
        assert should is True
        assert "3" in reason

    def test_crashes_older_than_apply_ignored(self, sandbox):
        rollback.record_apply("9.2.0", "9.3.0")
        old = datetime.now(timezone.utc) - timedelta(hours=1)
        for i in range(5):
            _write_crash_file(sandbox / "data" / "crash_reports", old, i)
        should, _ = rollback.needs_rollback()
        assert should is False

    def test_grace_window_expired_no_rollback(self, sandbox):
        # Simulate apply that happened more than grace_window_s ago
        long_ago = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        (sandbox / "data" / "rollback_state.json").write_text(json.dumps({
            "schema_version": 1,
            "previous": "9.2.0",
            "new": "9.3.0",
            "applied_at": long_ago,
            "grace_window_s": 60,
        }))
        should, reason = rollback.needs_rollback()
        assert should is False
        assert "expired" in reason


# ──────────────────────────────────────────────────────────────────
# perform_rollback
# ──────────────────────────────────────────────────────────────────

class TestPerformRollback:
    def _seed(self, sandbox):
        # Current (bad) versions
        (sandbox / "src").mkdir()
        (sandbox / "src" / "new.py").write_text("BROKEN")
        (sandbox / "config").mkdir()
        (sandbox / "config" / "new.yaml").write_text("BROKEN")
        (sandbox / "migrations").mkdir()
        (sandbox / "migrations" / "new.sql").write_text("-- BROKEN")
        (sandbox / "src" / "__init__.py").write_text('VERSION = "9.3.0"\n')

        # Previous (good) versions
        (sandbox / "_src_previous").mkdir()
        (sandbox / "_src_previous" / "__init__.py").write_text('VERSION = "9.2.0"\n')
        (sandbox / "_config_previous").mkdir()
        (sandbox / "_config_previous" / "old.yaml").write_text("ok")
        (sandbox / "_migrations_previous").mkdir()
        (sandbox / "_migrations_previous" / "old.sql").write_text("-- ok")

        (sandbox / "data" / "rollback_state.json").write_text(json.dumps({
            "schema_version": 1,
            "previous": "9.2.0",
            "new": "9.3.0",
            "applied_at": datetime.now(timezone.utc).isoformat(),
            "grace_window_s": 60,
        }))

    def test_restores_previous_directories(self, sandbox):
        self._seed(sandbox)
        ok, msg = rollback.perform_rollback()
        assert ok is True
        assert "9.2.0" in msg

        # Previous content now lives at src/ etc
        assert (sandbox / "src" / "__init__.py").read_text().strip() == 'VERSION = "9.2.0"'
        assert (sandbox / "config" / "old.yaml").exists()
        assert (sandbox / "migrations" / "old.sql").exists()

        # State cleared
        assert rollback.current_state() is None

    def test_no_state_returns_false(self, sandbox):
        ok, _ = rollback.perform_rollback()
        assert ok is False

    def test_nothing_to_restore_when_no_previous_dirs(self, sandbox):
        (sandbox / "data" / "rollback_state.json").write_text(json.dumps({
            "schema_version": 1,
            "previous": "9.2.0",
            "new": "9.3.0",
            "applied_at": datetime.now(timezone.utc).isoformat(),
            "grace_window_s": 60,
        }))
        ok, msg = rollback.perform_rollback()
        assert ok is False
        assert "nothing to restore" in msg.lower()


# ──────────────────────────────────────────────────────────────────
# clear_state_if_stable
# ──────────────────────────────────────────────────────────────────

class TestClearStateIfStable:
    def test_clears_after_grace_expiry(self, sandbox):
        # Apply happened long ago
        (sandbox / "data" / "rollback_state.json").write_text(json.dumps({
            "schema_version": 1,
            "previous": "9.2.0",
            "new": "9.3.0",
            "applied_at": (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat(),
            "grace_window_s": 60,
        }))
        (sandbox / "_src_previous").mkdir()

        cleared = rollback.clear_state_if_stable()
        assert cleared is True
        assert rollback.current_state() is None
        assert not (sandbox / "_src_previous").exists()

    def test_does_not_clear_during_grace_window(self, sandbox):
        rollback.record_apply("9.2.0", "9.3.0")
        assert rollback.clear_state_if_stable() is False
        # State and any backups remain untouched
        assert rollback.current_state() is not None
