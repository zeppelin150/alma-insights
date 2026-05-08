"""
Hardware profiler — M1/M2 wiring tests (2026-05-07)
====================================================

Covers the five-phase rebuild that makes the hardware profile
load-bearing for the embedding pipeline:

* Phase 3: ``_tune()`` returns the right batch/threads/max_seq for each
  machine class (M1 8GB, M1 Pro 16GB, M1 Max 32GB, CUDA, Intel CPU).
* Phase 2: ``needs_reprofile()`` triggers on schema bump, app version
  change, missing JSON, and machine drift; doesn't trigger on identical
  state. Schema is v2.
* Phase 1: ``_resolve_device()`` reads the profile, validates against
  torch's runtime view, and downgrades when the requested accelerator
  is unavailable. ``get_model()`` passes ``device=`` and falls back to
  CPU on first-encode failure.
* Phase 1 (builder): ``_resolve_batch_and_seq()`` reads the profile,
  defaults sensibly, and clamps to safe minimums.

Run: ``python -m pytest tests/test_hardware_profiler_m1.py -x -v``
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.startup import hardware as hw
from src.data.embedding import model_loader
from src.data.embedding import builder as eb


# ── Phase 3: _tune() per machine class ────────────────────────────────


@pytest.mark.parametrize(
    "label, ram, cpu, acc, vram, expected",
    [
        # M1 MacBook Air/Pro 8GB — the production target
        ("m1-8gb", 8, 8, "mps", 0, {
            "embedding_batch_size": 12,
            "embedding_threads": 4,
            "embedding_device": "mps",
            "embedding_max_seq_length": 1024,
            "acp_max_workers": 2,
        }),
        # M1 Pro 16GB
        ("m1-pro-16gb", 16, 10, "mps", 0, {
            "embedding_batch_size": 32,
            "embedding_threads": 5,
            "embedding_device": "mps",
            "embedding_max_seq_length": 2048,
        }),
        # M1 Max 32GB
        ("m1-max-32gb", 32, 12, "mps", 0, {
            "embedding_batch_size": 64,
            "embedding_threads": 6,
            "embedding_device": "mps",
            "embedding_max_seq_length": 2048,
        }),
        # Dev machine — RTX 4070 Ti SUPER 15GB
        ("cuda-15gb", 63, 20, "cuda", 15, {
            "embedding_batch_size": 120,
            "embedding_threads": 1,
            "embedding_device": "cuda",
            "embedding_max_seq_length": 2048,
        }),
        # Intel MacBook (no MPS, no CUDA) 8GB
        ("intel-8gb", 8, 4, "cpu", 0, {
            "embedding_batch_size": 8,
            "embedding_threads": 2,
            "embedding_device": "cpu",
            "embedding_max_seq_length": 2048,
        }),
    ],
)
def test_tune_per_machine_class(label, ram, cpu, acc, vram, expected):
    out = hw._tune(cpu, ram, acc, vram)
    for key, want in expected.items():
        assert out[key] == want, f"{label}: {key} got {out[key]}, expected {want}"


# ── Phase 2: needs_reprofile triggers ─────────────────────────────────


def _matching_cached(version="1.0.0"):
    """A cache that matches the current host (no reprofile expected)."""
    import multiprocessing
    import platform
    from src.startup import _hw_detect as _detect
    acc, _name, _vram = _detect.accelerator()
    return {
        "schema_version": hw._SCHEMA_VERSION,
        "app_version": version,
        "cpu_count": multiprocessing.cpu_count(),
        "ram_gb": _detect.ram_gb(),
        "accelerator": acc,
        "arch": platform.machine(),
    }


class TestNeedsReprofile:
    def test_no_cache_triggers_reprofile(self):
        assert hw.needs_reprofile(None, "1.0.0") is True

    def test_matching_cache_does_not_trigger(self):
        cached = _matching_cached()
        assert hw.needs_reprofile(cached, cached["app_version"]) is False

    def test_app_version_change_triggers(self):
        cached = _matching_cached(version="1.0.0")
        assert hw.needs_reprofile(cached, "1.0.1") is True

    def test_schema_drift_triggers(self):
        cached = _matching_cached()
        cached["schema_version"] = hw._SCHEMA_VERSION - 1
        assert hw.needs_reprofile(cached, cached["app_version"]) is True

    def test_machine_change_triggers_on_cpu(self, monkeypatch):
        cached = _matching_cached()
        cached["cpu_count"] = cached["cpu_count"] + 99   # force drift
        assert hw.needs_reprofile(cached, cached["app_version"]) is True

    def test_machine_change_triggers_on_ram(self, monkeypatch):
        cached = _matching_cached()
        cached["ram_gb"] = cached["ram_gb"] + 100
        assert hw.needs_reprofile(cached, cached["app_version"]) is True

    def test_machine_change_triggers_on_accelerator(self, monkeypatch):
        cached = _matching_cached()
        # If the host is currently cuda, set cached to "mps" to force drift
        cached["accelerator"] = "mps" if cached["accelerator"] != "mps" else "cpu"
        assert hw.needs_reprofile(cached, cached["app_version"]) is True

    def test_machine_change_triggers_on_arch(self, monkeypatch):
        cached = _matching_cached()
        cached["arch"] = "ARM64-FAKE"
        assert hw.needs_reprofile(cached, cached["app_version"]) is True

    def test_detection_failure_keeps_cache(self, monkeypatch):
        """If live detection raises, don't loop forever rewriting."""
        cached = _matching_cached()
        monkeypatch.setattr(hw, "_machine_changed", lambda c: False)
        assert hw.needs_reprofile(cached, cached["app_version"]) is False


def test_schema_version_is_v2():
    """Bumping the schema is a load-bearing migration trigger."""
    assert hw._SCHEMA_VERSION == 2


# ── Phase 2: M1 confirmation log ──────────────────────────────────────


def test_m1_log_emits_apple_silicon_marker(caplog):
    """When MPS+arm64 lands in the profile, the splash log must say so."""
    import logging
    caplog.set_level(logging.INFO, logger="alma.hardware")
    profile = {
        "accelerator": "mps", "arch": "arm64", "ram_gb": 8,
        "embedding_batch_size": 12, "embedding_max_seq_length": 1024,
        "embedding_threads": 4, "acp_max_workers": 2,
    }
    hw._log_acceleration_summary(profile)
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "Apple Silicon" in text
    assert "Metal Performance Shaders" in text
    assert "batch=12" in text
    assert "max_seq=1024" in text


def test_cuda_log_emits_gpu_marker(caplog):
    import logging
    caplog.set_level(logging.INFO, logger="alma.hardware")
    profile = {
        "accelerator": "cuda", "gpu_name": "RTX 4070",
        "vram_gb": 12, "embedding_batch_size": 96, "embedding_threads": 1,
    }
    hw._log_acceleration_summary(profile)
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "CUDA" in text
    assert "RTX 4070" in text


# ── Phase 1: _resolve_device() ────────────────────────────────────────


class TestResolveDevice:
    def test_cpu_when_profile_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(model_loader, "_PROFILE_PATH", tmp_path / "missing.json")
        assert model_loader._resolve_device() == "cpu"

    def test_returns_profile_device_when_torch_agrees(self, tmp_path, monkeypatch):
        p = tmp_path / "hw.json"
        p.write_text(json.dumps({"embedding_device": "mps"}), encoding="utf-8")
        monkeypatch.setattr(model_loader, "_PROFILE_PATH", p)

        # Stub torch with mps available
        fake_torch = MagicMock()
        fake_torch.cuda.is_available.return_value = False
        fake_torch.backends.mps.is_available.return_value = True
        monkeypatch.setitem(sys.modules, "torch", fake_torch)

        assert model_loader._resolve_device() == "mps"

    def test_downgrades_mps_when_torch_disagrees(self, tmp_path, monkeypatch):
        p = tmp_path / "hw.json"
        p.write_text(json.dumps({"embedding_device": "mps"}), encoding="utf-8")
        monkeypatch.setattr(model_loader, "_PROFILE_PATH", p)

        fake_torch = MagicMock()
        fake_torch.cuda.is_available.return_value = False
        fake_torch.backends.mps.is_available.return_value = False
        monkeypatch.setitem(sys.modules, "torch", fake_torch)

        assert model_loader._resolve_device() == "cpu"

    def test_downgrades_cuda_when_unavailable(self, tmp_path, monkeypatch):
        p = tmp_path / "hw.json"
        p.write_text(json.dumps({"embedding_device": "cuda"}), encoding="utf-8")
        monkeypatch.setattr(model_loader, "_PROFILE_PATH", p)

        fake_torch = MagicMock()
        fake_torch.cuda.is_available.return_value = False
        monkeypatch.setitem(sys.modules, "torch", fake_torch)

        assert model_loader._resolve_device() == "cpu"


# ── Phase 1: get_model() passes device + falls back ───────────────────


class _FakeST:
    """Minimal SentenceTransformer stand-in that records its kwargs."""
    init_calls: list = []
    _next_raise_on: str | None = None  # device that should throw

    def __init__(self, path, device=None):
        type(self).init_calls.append({"path": path, "device": device})
        if type(self)._next_raise_on == device:
            raise RuntimeError(f"forced failure on {device}")
        self._device = device or "cpu"
        self.max_seq_length = 32768

    @property
    def device(self):
        return self._device

    def to(self, dev):
        self._device = dev
        return self


@pytest.fixture
def fake_sentence_transformers(monkeypatch):
    _FakeST.init_calls = []
    _FakeST._next_raise_on = None

    fake_module = type(sys)("sentence_transformers")
    fake_module.SentenceTransformer = _FakeST
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_module)

    # Bypass the air-gap network probe + path discovery
    monkeypatch.setattr(model_loader, "is_available", lambda: True)
    monkeypatch.setattr(model_loader, "_find_model_path",
                         lambda: Path("/fake/model"))
    monkeypatch.setattr("src.data.embedding.air_gap.enforce_air_gap",
                         lambda: None)
    # Reset the singleton between tests
    model_loader._model = None
    model_loader._active_device = None
    return _FakeST


class TestGetModelDevicePassthrough:
    def test_passes_mps_when_profile_says_mps(
        self, fake_sentence_transformers, monkeypatch,
    ):
        monkeypatch.setattr(model_loader, "_resolve_device", lambda: "mps")
        m = model_loader.get_model()
        assert m is not None
        assert _FakeST.init_calls[0]["device"] == "mps"
        assert model_loader.get_active_device() == "mps"

    def test_passes_cuda_when_profile_says_cuda(
        self, fake_sentence_transformers, monkeypatch,
    ):
        monkeypatch.setattr(model_loader, "_resolve_device", lambda: "cuda")
        m = model_loader.get_model()
        assert m is not None
        assert _FakeST.init_calls[0]["device"] == "cuda"
        assert model_loader.get_active_device() == "cuda"

    def test_passes_cpu_when_profile_says_cpu(
        self, fake_sentence_transformers, monkeypatch,
    ):
        monkeypatch.setattr(model_loader, "_resolve_device", lambda: "cpu")
        m = model_loader.get_model()
        assert m is not None
        assert _FakeST.init_calls[0]["device"] == "cpu"
        assert model_loader.get_active_device() == "cpu"

    def test_falls_back_to_cpu_when_mps_load_throws(
        self, fake_sentence_transformers, tmp_path, monkeypatch,
    ):
        """If MPS load throws, retry on CPU + persist the fallback."""
        p = tmp_path / "hw.json"
        p.write_text(
            json.dumps({"embedding_device": "mps", "ram_gb": 8}),
            encoding="utf-8",
        )
        monkeypatch.setattr(model_loader, "_PROFILE_PATH", p)
        monkeypatch.setattr(model_loader, "_resolve_device", lambda: "mps")
        _FakeST._next_raise_on = "mps"

        m = model_loader.get_model()
        assert m is not None
        # Two init calls — first mps (raised), second cpu (succeeded)
        devices = [c["device"] for c in _FakeST.init_calls]
        assert "mps" in devices
        assert devices[-1] == "cpu"
        assert model_loader.get_active_device() == "cpu"

        # Profile was rewritten to cpu so we don't keep re-trying mps
        rewritten = json.loads(p.read_text(encoding="utf-8"))
        assert rewritten["embedding_device"] == "cpu"
        reason = rewritten.get("embedding_device_fallback_reason", "")
        assert "from mps" in reason
        assert "to cpu" in reason


# ── Phase 1: builder._resolve_batch_and_seq() ─────────────────────────


class TestResolveBatchAndSeq:
    def test_defaults_when_profile_missing(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)   # `data/hardware_profile.json` won't exist
        batch, seq = eb._resolve_batch_and_seq()
        assert batch == eb._DEFAULT_BATCH_SIZE
        assert seq == eb._DEFAULT_MAX_SEQ_LENGTH

    def test_reads_profile_values(self, tmp_path, monkeypatch):
        (tmp_path / "data").mkdir()
        (tmp_path / "data" / "hardware_profile.json").write_text(
            json.dumps({
                "embedding_batch_size": 12,
                "embedding_max_seq_length": 1024,
            }),
            encoding="utf-8",
        )
        monkeypatch.chdir(tmp_path)
        batch, seq = eb._resolve_batch_and_seq()
        assert batch == 12
        assert seq == 1024

    def test_clamps_to_safe_minimums(self, tmp_path, monkeypatch):
        (tmp_path / "data").mkdir()
        (tmp_path / "data" / "hardware_profile.json").write_text(
            json.dumps({"embedding_batch_size": 0,
                        "embedding_max_seq_length": 0}),
            encoding="utf-8",
        )
        monkeypatch.chdir(tmp_path)
        batch, seq = eb._resolve_batch_and_seq()
        assert batch >= 1
        assert seq >= 128

    def test_falls_back_on_corrupt_json(self, tmp_path, monkeypatch):
        (tmp_path / "data").mkdir()
        (tmp_path / "data" / "hardware_profile.json").write_text(
            "{not valid json", encoding="utf-8",
        )
        monkeypatch.chdir(tmp_path)
        batch, seq = eb._resolve_batch_and_seq()
        assert batch == eb._DEFAULT_BATCH_SIZE
        assert seq == eb._DEFAULT_MAX_SEQ_LENGTH


# ── End-to-end: profile dict shape ────────────────────────────────────


def test_profile_dict_includes_all_required_keys():
    """Anything that consumes the profile expects these keys to exist."""
    p = hw.profile(current_version="test")
    required = {
        "schema_version", "app_version", "profiled_at",
        "cpu_count", "ram_gb", "accelerator", "gpu_name", "vram_gb",
        "os", "arch",
        "embedding_batch_size", "embedding_threads", "embedding_device",
        "embedding_max_seq_length", "acp_max_workers",
    }
    missing = required - set(p.keys())
    assert not missing, f"profile missing keys: {missing}"
    assert p["schema_version"] == 2
