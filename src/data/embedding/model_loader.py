"""Singleton model loader for Qwen3-Embedding-0.6B.

Loads the model once on first access with air-gap verification.
All subsequent calls return the cached instance.

Hardware profile integration (2026-05-07): the model is loaded onto the
device chosen by `src.startup.hardware` and persisted in
`data/hardware_profile.json` (`embedding_device`). On Apple Silicon this
is `mps`; on CUDA boxes it's `cuda`; everything else is `cpu`. If the
chosen device fails to actually load the model (some PyTorch builds
report `mps.is_available() == True` but throw on the first encode), we
fall back to CPU and rewrite the profile so subsequent launches don't
repeat the failing attempt.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

MODEL_HF_ID = "Qwen/Qwen3-Embedding-0.6B"     # HuggingFace download ID
MODEL_NAME = "Qwen3-Embedding-0.6B"           # Local directory name
MODEL_DIR = Path(__file__).resolve().parent.parent.parent.parent / "data" / "models"
EMBEDDING_DIM = 1024
MAX_SEQ_LENGTH = 32768

_PROFILE_PATH = Path("data") / "hardware_profile.json"

_model = None
_active_device: str | None = None  # what we actually loaded onto


def _read_profile() -> dict:
    """Read the cached hardware profile. Returns {} on any failure."""
    try:
        if _PROFILE_PATH.exists():
            return json.loads(_PROFILE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass
    return {}


def _resolve_device() -> str:
    """Pick the device to load the model onto.

    Reads `embedding_device` from `data/hardware_profile.json`, then
    validates it against torch's runtime view — a profile that says
    "cuda" on a machine without torch.cuda available is silently
    downgraded to "cpu". Same for "mps". Defaults to "cpu" if the
    profile is missing or unreadable.
    """
    profile = _read_profile()
    requested = profile.get("embedding_device", "cpu")

    try:
        import torch
    except ImportError:
        return "cpu"

    if requested == "cuda":
        try:
            if torch.cuda.is_available():
                return "cuda"
        except Exception:
            pass
        logger.info("Profile asked for cuda but torch.cuda.is_available()=False; falling back to cpu")
        return "cpu"

    if requested == "mps":
        try:
            if torch.backends.mps.is_available():
                return "mps"
        except Exception:
            pass
        logger.info("Profile asked for mps but torch MPS is unavailable; falling back to cpu")
        return "cpu"

    return "cpu"


def _persist_device_fallback(device: str) -> None:
    """If MPS or CUDA load fails, rewrite the profile so the next launch
    doesn't repeat the failing attempt. Best-effort: any I/O error is
    swallowed (we're already in a fallback path)."""
    try:
        profile = _read_profile()
        if not profile:
            return
        # Record the OLD device in the reason BEFORE overwriting it.
        prior = profile.get("embedding_device", "unknown")
        profile["embedding_device"] = device
        profile["embedding_device_fallback_reason"] = (
            f"auto-downgraded from {prior} to {device} after first-load failure"
        )
        _PROFILE_PATH.write_text(json.dumps(profile, indent=2), encoding="utf-8")
        logger.warning("Rewrote hardware_profile.json: embedding_device=%s", device)
    except Exception as exc:  # noqa: BLE001 — best-effort
        logger.warning("Could not persist device fallback: %s", exc)


def get_active_device() -> str | None:
    """Return the device the model actually loaded onto, or None if the
    model hasn't been instantiated yet. Used by tests + Settings UI."""
    return _active_device


def is_available() -> bool:
    """Check if the embedding model is usable.

    Returns True if sentence_transformers is installed AND
    model files exist locally.
    """
    try:
        import sentence_transformers  # noqa: F401
    except ImportError:
        return False

    return _model_files_exist()


def get_model():
    """Get the singleton SentenceTransformer model.

    Enforces air-gap before loading. Loads onto the device chosen by the
    hardware profile (`embedding_device`) with a CPU fallback if the
    accelerator-specific load throws. Returns None if the model is
    structurally unavailable (missing files or library).
    """
    global _model, _active_device
    if _model is not None:
        return _model

    if not is_available():
        logger.warning("Embedding model not available (missing files or library)")
        return None

    from src.data.embedding.air_gap import enforce_air_gap
    enforce_air_gap()

    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        logger.error("sentence_transformers not installed")
        return None

    model_path = _find_model_path()
    if model_path is None:
        logger.error("Model path not found in %s", MODEL_DIR)
        return None

    device = _resolve_device()

    try:
        _model = SentenceTransformer(str(model_path), device=device)
        # Defensive: some sentence_transformers versions don't honor the
        # device kwarg when the model has multiple modules. Force the move.
        if device != "cpu":
            try:
                _model = _model.to(device)
            except Exception as move_err:
                logger.warning(
                    "model.to(%s) failed (%s); attempting CPU fallback.",
                    device, move_err,
                )
                raise
        _active_device = device
        logger.info("Loaded embedding model on device=%s from %s", device, model_path)
        return _model
    except Exception as exc:
        # If we asked for cuda/mps and it failed, try cpu before giving up
        if device != "cpu":
            logger.warning(
                "Loading embedding model on %s failed (%s); falling back to CPU.",
                device, exc,
            )
            try:
                _model = SentenceTransformer(str(model_path), device="cpu")
                _active_device = "cpu"
                _persist_device_fallback("cpu")
                logger.info("Loaded embedding model on device=cpu (fallback)")
                return _model
            except Exception as cpu_exc:  # noqa: BLE001
                logger.error("CPU fallback also failed: %s", cpu_exc)
                return None
        logger.error("Failed to load embedding model: %s", exc)
        return None


def _model_files_exist() -> bool:
    """Check if model files are present locally."""
    if not MODEL_DIR.is_dir():
        return False
    # Look for model directories matching the model name
    matches = list(MODEL_DIR.glob(f"*{MODEL_NAME}*"))
    if matches:
        return True
    # Also check for any model with a config.json (generic check)
    return any(MODEL_DIR.glob("*/config.json"))


def _find_model_path() -> Path | None:
    """Find the actual model directory path.

    Handles both clean directories (from snapshot_download with local_dir)
    and HF cache structure (models--Org--Name/snapshots/<hash>/).
    """
    # 1. Exact name match (clean install via snapshot_download)
    exact = MODEL_DIR / MODEL_NAME
    if exact.is_dir() and (exact / "config.json").exists():
        return exact

    # 2. HF cache structure: models--Org--Name/snapshots/<hash>/
    for d in sorted(MODEL_DIR.iterdir()):
        if not d.is_dir():
            continue
        snapshots = d / "snapshots"
        if snapshots.is_dir():
            for snap in sorted(snapshots.iterdir()):
                if snap.is_dir() and (snap / "config.json").exists():
                    return snap

    # 3. Glob match on model name
    matches = sorted(MODEL_DIR.glob(f"*{MODEL_NAME}*"))
    for m in matches:
        if m.is_dir() and (m / "config.json").exists():
            return m

    # 4. Fallback: any directory with config.json
    for d in sorted(MODEL_DIR.iterdir()):
        if d.is_dir() and (d / "config.json").exists():
            return d
    return None
