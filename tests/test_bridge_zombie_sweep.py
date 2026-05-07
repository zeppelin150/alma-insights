"""Unit tests — src/agents/bridge_zombie_sweep.py (2026-05-07).

Coverage:
  * sweep returns empty result on non-Windows (POSIX no-op)
  * dry_run identifies candidates without killing
  * candidates discovered via wmic stub are terminated via taskkill stub
  * own pid is skipped (don't kill ourselves)
  * wmic missing → error captured, no raise
  * all 4 wmic queries are issued
  * SweepResult.as_dict round-trips
"""
from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from src.agents import bridge_zombie_sweep as zs


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────

def _mk_completed(returncode: int, stdout: str = "", stderr: str = "") -> SimpleNamespace:
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def _wmic_stdout(*pids: int) -> str:
    """Mimic ``wmic ... /format:value`` output (key=value, blank-line separated)."""
    return "\n\n".join(f"ProcessId={p}\n" for p in pids)


# ──────────────────────────────────────────────────────────────────────
# Platform gating
# ──────────────────────────────────────────────────────────────────────

class TestPlatformGate:
    def test_non_windows_is_noop(self, monkeypatch):
        monkeypatch.setattr(zs, "_is_windows", lambda: False)
        result = zs.sweep_zombie_bridges()
        assert result.terminated_pids == []
        assert result.skipped_pids == []
        assert result.total_killed == 0


# ──────────────────────────────────────────────────────────────────────
# Candidate discovery + termination
# ──────────────────────────────────────────────────────────────────────

class TestSweep:
    @pytest.fixture
    def mock_run(self, monkeypatch):
        """Capture every subprocess.run call; return scripted stdout."""
        calls: list[list[str]] = []
        scripts: dict[tuple[str, ...], SimpleNamespace] = {}

        def _run(cmd, **kw):
            calls.append(list(cmd))
            key = tuple(cmd)
            if key in scripts:
                return scripts[key]
            # default: empty wmic / successful taskkill
            if cmd[0] == "wmic":
                return _mk_completed(0, "")
            if cmd[0] == "taskkill":
                return _mk_completed(0)
            return _mk_completed(0)

        monkeypatch.setattr(zs, "_is_windows", lambda: True)
        monkeypatch.setattr(subprocess, "run", _run)
        return SimpleNamespace(calls=calls, scripts=scripts)

    def test_no_zombies_passes_clean(self, mock_run):
        result = zs.sweep_zombie_bridges()
        assert result.terminated_pids == []
        assert result.errors == []
        # All 4 wmic queries should have been issued
        wmic_calls = [c for c in mock_run.calls if c and c[0] == "wmic"]
        assert len(wmic_calls) == 4

    def test_kills_discovered_pids(self, mock_run):
        # First wmic query (gemini.exe) returns pid 1234
        mock_run.scripts[(
            "wmic", "process", "where", "name='gemini.exe'",
            "get", "processid", "/format:value",
        )] = _mk_completed(0, _wmic_stdout(1234))
        result = zs.sweep_zombie_bridges()
        assert 1234 in result.terminated_pids
        # Verify a taskkill /F /T /PID 1234 was issued
        kills = [c for c in mock_run.calls if c and c[0] == "taskkill"]
        assert any("1234" in str(c) for c in kills)

    def test_dry_run_skips_kills(self, mock_run):
        mock_run.scripts[(
            "wmic", "process", "where", "name='gemini.exe'",
            "get", "processid", "/format:value",
        )] = _mk_completed(0, _wmic_stdout(2345))
        result = zs.sweep_zombie_bridges(dry_run=True)
        assert 2345 in result.skipped_pids
        assert result.terminated_pids == []
        kills = [c for c in mock_run.calls if c and c[0] == "taskkill"]
        assert not kills

    def test_skips_own_pid(self, mock_run, monkeypatch):
        import os
        my_pid = os.getpid()
        mock_run.scripts[(
            "wmic", "process", "where", "name='gemini.exe'",
            "get", "processid", "/format:value",
        )] = _mk_completed(0, _wmic_stdout(my_pid, 9999))
        result = zs.sweep_zombie_bridges()
        assert my_pid in result.skipped_pids
        assert 9999 in result.terminated_pids

    def test_wmic_missing_yields_error(self, monkeypatch):
        monkeypatch.setattr(zs, "_is_windows", lambda: True)

        def _raise_fnf(*a, **kw):
            raise FileNotFoundError("wmic")
        monkeypatch.setattr(subprocess, "run", _raise_fnf)
        result = zs.sweep_zombie_bridges()
        assert result.terminated_pids == []
        assert any("wmic not found" in e for e in result.errors)

    def test_taskkill_failure_recorded(self, mock_run):
        mock_run.scripts[(
            "wmic", "process", "where", "name='gemini.exe'",
            "get", "processid", "/format:value",
        )] = _mk_completed(0, _wmic_stdout(7777))
        mock_run.scripts[("taskkill", "/F", "/T", "/PID", "7777")] = _mk_completed(
            1, "", "Access denied",
        )
        result = zs.sweep_zombie_bridges()
        assert 7777 not in result.terminated_pids
        assert any("pid=7777" in e for e in result.errors)

    def test_dedupes_pids_across_queries(self, mock_run):
        # Same pid surfaces in two different wmic queries
        mock_run.scripts[(
            "wmic", "process", "where", "name='gemini.exe'",
            "get", "processid", "/format:value",
        )] = _mk_completed(0, _wmic_stdout(5555))
        mock_run.scripts[(
            "wmic", "process", "where",
            "name='node.exe' AND commandline LIKE '%gemini%'",
            "get", "processid", "/format:value",
        )] = _mk_completed(0, _wmic_stdout(5555))
        result = zs.sweep_zombie_bridges()
        # PID 5555 should appear at most once in terminated
        assert result.terminated_pids.count(5555) == 1


# ──────────────────────────────────────────────────────────────────────
# SweepResult dataclass
# ──────────────────────────────────────────────────────────────────────

class TestSweepResult:
    def test_as_dict_includes_total(self):
        r = zs.SweepResult(
            terminated_pids=[1, 2, 3], skipped_pids=[4],
            errors=[], platform="win32",
        )
        d = r.as_dict()
        assert d["total_killed"] == 3
        assert d["platform"] == "win32"
        assert d["skipped_pids"] == [4]

    def test_total_killed_zero_default(self):
        r = zs.SweepResult()
        assert r.total_killed == 0
