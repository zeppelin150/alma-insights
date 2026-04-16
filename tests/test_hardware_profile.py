"""
Unit tests for src/startup/hardware.py and src/startup/_hw_detect.py

Covers:
  - Heuristic tuning for CUDA / MPS / CPU accelerators
  - ACP worker count derivation under tight RAM / tight CPU / generous
  - Profile persistence + reload round-trip
  - needs_reprofile triggers (missing, version change, schema change)
  - RAM detection fallback when platform-specific probes fail

Run: python -m pytest tests/test_hardware_profile.py -x -v
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from src.startup import hardware
from src.startup import _hw_detect as detect


# ──────────────────────────────────────────────────────────────────
# Heuristic tuning
# ──────────────────────────────────────────────────────────────────

class TestTuningCuda:
    """Dev machine profile — i7-12700K + RTX 4070 Ti SUPER (16GB VRAM)."""

    def test_cuda_uses_large_batch(self):
        out = hardware._tune(cpu_count=20, ram_gb=32, accelerator="cuda", vram_gb=16)
        assert out["embedding_batch_size"] == 128  # capped at 128
        assert out["embedding_threads"] == 1  # GPU dominates
        assert out["embedding_device"] == "cuda"

    def test_cuda_low_vram_uses_smaller_batch(self):
        out = hardware._tune(cpu_count=8, ram_gb=16, accelerator="cuda", vram_gb=4)
        assert out["embedding_batch_size"] == 32  # floor
        assert out["embedding_device"] == "cuda"

    def test_cuda_mid_vram_scales(self):
        out = hardware._tune(cpu_count=8, ram_gb=16, accelerator="cuda", vram_gb=8)
        assert out["embedding_batch_size"] == 64  # 8 * 8


class TestTuningMps:
    """Production profile — MacBook Pro M1 / 16 GB."""

    def test_m1_profile_defaults(self):
        out = hardware._tune(cpu_count=8, ram_gb=16, accelerator="mps", vram_gb=0)
        assert out["embedding_batch_size"] == 32  # 16 * 2
        assert out["embedding_threads"] == 4  # 8 // 2
        assert out["embedding_device"] == "mps"

    def test_mps_caps_batch_at_64(self):
        out = hardware._tune(cpu_count=16, ram_gb=64, accelerator="mps", vram_gb=0)
        assert out["embedding_batch_size"] == 64  # cap


class TestTuningCpu:
    """Headless / no-GPU fallback."""

    def test_cpu_conservative_batch(self):
        out = hardware._tune(cpu_count=4, ram_gb=8, accelerator="cpu", vram_gb=0)
        assert out["embedding_batch_size"] == 8
        assert out["embedding_threads"] == 2
        assert out["embedding_device"] == "cpu"

    def test_cpu_caps_batch_at_32(self):
        out = hardware._tune(cpu_count=16, ram_gb=64, accelerator="cpu", vram_gb=0)
        assert out["embedding_batch_size"] == 32


class TestAcpWorkers:
    def test_m1_production_yields_4_workers(self):
        # M1 16GB / 8 cores: (16-4)//2 = 6, min(8-2=6, 6) = 6. Cap at 8 still allows 6.
        assert hardware._acp_workers(cpu_count=8, ram_gb=16) == 6

    def test_tight_ram_limits_workers(self):
        assert hardware._acp_workers(cpu_count=16, ram_gb=6) == 1

    def test_hard_cap_at_8(self):
        assert hardware._acp_workers(cpu_count=32, ram_gb=64) == 8

    def test_minimum_always_one(self):
        assert hardware._acp_workers(cpu_count=1, ram_gb=2) == 1


# ──────────────────────────────────────────────────────────────────
# Profile integration (detection + tuning)
# ──────────────────────────────────────────────────────────────────

class TestProfile:
    @patch("src.startup.hardware._detect.accelerator", return_value=("mps", "Apple Silicon GPU", 0))
    @patch("src.startup.hardware._detect.ram_gb", return_value=16)
    @patch("src.startup.hardware.multiprocessing.cpu_count", return_value=8)
    def test_m1_full_profile(self, *_):
        p = hardware.profile(current_version="v9.3.0")
        assert p["schema_version"] == 1
        assert p["app_version"] == "v9.3.0"
        assert p["cpu_count"] == 8
        assert p["ram_gb"] == 16
        assert p["accelerator"] == "mps"
        assert p["gpu_name"] == "Apple Silicon GPU"
        assert p["embedding_device"] == "mps"
        assert p["acp_max_workers"] == 6
        assert p["os"]  # present
        assert p["arch"]
        assert p["profiled_at"]

    @patch("src.startup.hardware._detect.accelerator", return_value=("cuda", "RTX 4070 Ti SUPER", 16))
    @patch("src.startup.hardware._detect.ram_gb", return_value=32)
    @patch("src.startup.hardware.multiprocessing.cpu_count", return_value=20)
    def test_cuda_dev_full_profile(self, *_):
        p = hardware.profile(current_version="v9.3.0-dev")
        assert p["accelerator"] == "cuda"
        assert p["gpu_name"] == "RTX 4070 Ti SUPER"
        assert p["vram_gb"] == 16
        assert p["embedding_batch_size"] == 128
        assert p["acp_max_workers"] == 8


# ──────────────────────────────────────────────────────────────────
# Persistence
# ──────────────────────────────────────────────────────────────────

class TestPersistence:
    def _redirect_profile_path(self, tmp_path, monkeypatch):
        monkeypatch.setattr(hardware, "_PROFILE_PATH", tmp_path / "hw.json")

    def test_save_creates_file(self, tmp_path, monkeypatch):
        self._redirect_profile_path(tmp_path, monkeypatch)
        p = {"cpu_count": 4, "schema_version": 1, "app_version": "v1"}
        path = hardware.save(p)
        assert path.exists()
        assert json.loads(path.read_text()) == p

    def test_load_returns_none_when_missing(self, tmp_path, monkeypatch):
        self._redirect_profile_path(tmp_path, monkeypatch)
        assert hardware.load() is None

    def test_load_returns_none_when_corrupt(self, tmp_path, monkeypatch):
        self._redirect_profile_path(tmp_path, monkeypatch)
        (tmp_path / "hw.json").write_text("not json")
        assert hardware.load() is None

    def test_save_load_round_trip(self, tmp_path, monkeypatch):
        self._redirect_profile_path(tmp_path, monkeypatch)
        p = {"a": 1, "b": [2, 3], "schema_version": 1, "app_version": "v1"}
        hardware.save(p)
        assert hardware.load() == p


# ──────────────────────────────────────────────────────────────────
# Reprofile triggers
# ──────────────────────────────────────────────────────────────────

class TestNeedsReprofile:
    def test_missing_cache(self):
        assert hardware.needs_reprofile(None, "v1") is True

    def test_matching_version_and_schema(self):
        cached = {"schema_version": 1, "app_version": "v1"}
        assert hardware.needs_reprofile(cached, "v1") is False

    def test_version_changed(self):
        cached = {"schema_version": 1, "app_version": "v1"}
        assert hardware.needs_reprofile(cached, "v2") is True

    def test_schema_changed(self):
        cached = {"schema_version": 0, "app_version": "v1"}
        assert hardware.needs_reprofile(cached, "v1") is True


# ──────────────────────────────────────────────────────────────────
# load_or_profile integration
# ──────────────────────────────────────────────────────────────────

class TestLoadOrProfile:
    def _mock_detection(self, monkeypatch):
        """Patch hardware detection primitives for a deterministic profile."""
        monkeypatch.setattr(hardware._detect, "accelerator", lambda: ("cpu", None, 0))
        monkeypatch.setattr(hardware._detect, "ram_gb", lambda: 8)

    def test_fresh_profile_on_first_run(self, tmp_path, monkeypatch):
        monkeypatch.setattr(hardware, "_PROFILE_PATH", tmp_path / "hw.json")
        self._mock_detection(monkeypatch)
        p = hardware.load_or_profile(current_version="v1")
        assert p["accelerator"] == "cpu"
        assert (tmp_path / "hw.json").exists()

    def test_uses_cache_on_second_run(self, tmp_path, monkeypatch):
        monkeypatch.setattr(hardware, "_PROFILE_PATH", tmp_path / "hw.json")
        self._mock_detection(monkeypatch)
        with patch("src.startup.hardware.multiprocessing.cpu_count", return_value=4) as mock_cpu:
            hardware.load_or_profile(current_version="v1")
            mock_cpu.reset_mock()
            # Second call should short-circuit to the cached profile
            hardware.load_or_profile(current_version="v1")
            mock_cpu.assert_not_called()

    def test_reprofiles_on_version_change(self, tmp_path, monkeypatch):
        monkeypatch.setattr(hardware, "_PROFILE_PATH", tmp_path / "hw.json")
        self._mock_detection(monkeypatch)
        with patch("src.startup.hardware.multiprocessing.cpu_count", return_value=4) as mock_cpu:
            hardware.load_or_profile(current_version="v1")
            mock_cpu.reset_mock()
            hardware.load_or_profile(current_version="v2")
            mock_cpu.assert_called()  # recomputed


# ──────────────────────────────────────────────────────────────────
# Detector fallbacks
# ──────────────────────────────────────────────────────────────────

class TestDetectorFallbacks:
    @patch("src.startup._hw_detect.platform.system", return_value="UnknownOS")
    def test_ram_unknown_os_falls_back_to_8(self, _):
        assert detect.ram_gb() == 8

    def test_accelerator_no_torch(self, monkeypatch):
        # Simulate torch not installed
        import builtins
        real_import = builtins.__import__

        def fake_import(name, *a, **kw):
            if name == "torch":
                raise ImportError("no torch here")
            return real_import(name, *a, **kw)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        name, gpu, vram = detect.accelerator()
        assert name == "cpu"
        assert gpu is None
        assert vram == 0

    def test_safe_catches_any_exception(self):
        assert detect._safe(lambda: 1 / 0) is None
        assert detect._safe(lambda: [][5]) is None
        assert detect._safe(lambda: "ok") == "ok"
