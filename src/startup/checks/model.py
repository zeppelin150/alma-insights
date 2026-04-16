"""
Check 7 — Embedding model (Qwen3-Embedding-0.6B).

Verifies the model files are present on disk. Non-critical: if the
model is missing, semantic search is disabled but the rest of the app
still works.

Uses the same resolution logic as src.data.embedding.model_loader so
this check stays in sync with the actual runtime path.
"""

from __future__ import annotations

from src.startup.checker import CheckResult


def check_embedding_model() -> CheckResult:
    model_path = _find_model_path()
    if model_path is None:
        return CheckResult(
            id="embedding_model",
            name="ML model check",
            status="warn",
            message="Qwen3-Embedding-0.6B not found in data/models/ — semantic search unavailable",
            remediation="Reinstall Alma Insights to restore the bundled model.",
            critical=False,
        )

    return CheckResult(
        id="embedding_model",
        name="ML model check",
        status="pass",
        message=f"Qwen3-Embedding-0.6B loaded from {model_path.name}",
        critical=False,
    )


def _find_model_path():
    """Delegate to model_loader's existing resolver."""
    try:
        from src.data.embedding.model_loader import _find_model_path as resolve
        return resolve()
    except Exception:  # noqa: BLE001 — model loader shouldn't block startup
        return None
