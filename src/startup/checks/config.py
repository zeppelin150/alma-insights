"""
Check 9 — Configuration.

Loads data/settings.yaml (auto-migrating from config/settings.yaml on
first launch) and verifies the required top-level sections exist.
Non-critical: missing sections are auto-seeded with defaults.
"""

from __future__ import annotations

import yaml

from src.data.settings_manager import get_settings_path
from src.startup.checker import CheckResult

_REQUIRED_SECTIONS = ("gemini", "behavior", "display")


def check_config() -> CheckResult:
    path = get_settings_path()
    if not path.exists():
        return CheckResult(
            id="config",
            name="Validating configuration",
            status="warn",
            message="No settings.yaml yet — will be seeded on first use",
            remediation="Defaults will apply until you change anything in Settings.",
            critical=False,
        )

    try:
        with open(path, encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
    except (OSError, yaml.YAMLError) as exc:
        return CheckResult(
            id="config",
            name="Validating configuration",
            status="fail",
            message=f"Parse error: {exc}",
            remediation=(
                f"Rename {path.name} and relaunch to regenerate defaults."
            ),
            critical=False,
        )

    missing = [s for s in _REQUIRED_SECTIONS if s not in raw]
    dataset_count = _count_datasets(raw)

    if missing:
        return CheckResult(
            id="config",
            name="Validating configuration",
            status="warn",
            message=f"Missing sections: {', '.join(missing)}",
            remediation="Defaults will be used for missing sections.",
            critical=False,
        )

    message = f"settings.yaml valid — {dataset_count} dataset(s) configured"
    return CheckResult(
        id="config",
        name="Validating configuration",
        status="pass",
        message=message,
        critical=False,
    )


def _count_datasets(cfg: dict) -> int:
    """Count Lightdash datasets in either legacy or current layout."""
    datasets = cfg.get("datasets") or cfg.get("lightdash", {}).get("datasets")
    if isinstance(datasets, list):
        return len(datasets)
    if isinstance(datasets, dict):
        return len(datasets)
    return 0
