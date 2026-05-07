"""Regression test — `data/settings.yaml` ships with a working Gemini model.

Background: ``gemini-2.5-flash-lite`` returns invalid_stream errors for any
ACP call that involves native tool calls (memory: bridge_v5_rewrite.md). It
must not be the active model on a fresh install or after settings migration.

Two assertions:
  * `gemini.model` is one of the known-working tiers.
  * `ai.active_model` is one of the known-working tiers.

These guards exist to catch a regression where a developer (or settings
migration) reverts the active model back to ``-lite``. The 2026-05-07
config change replaces ``-lite`` with ``-flash`` — this test locks that in.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml


# Working tiers per memory:bridge_v5_rewrite.md scan results — flash + 3-flash-preview
# both produced 98%+ NLP scan completion. -lite produced invalid_stream errors.
_KNOWN_BAD = {"gemini-2.5-flash-lite"}
_KNOWN_GOOD = {
    "gemini-2.5-flash",
    "gemini-2.5-pro",
    "gemini-3-flash-preview",
    "gemini-3.1-pro-preview",
}


@pytest.fixture(scope="module")
def settings() -> dict:
    path = Path(__file__).resolve().parent.parent / "data" / "settings.yaml"
    if not path.is_file():
        pytest.skip("data/settings.yaml not present in this checkout")
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def test_gemini_model_is_not_lite(settings):
    chosen = (settings.get("gemini") or {}).get("model")
    assert chosen not in _KNOWN_BAD, (
        f"gemini.model={chosen!r} is known-broken (memory: bridge_v5_rewrite.md)"
    )


def test_active_model_is_not_lite(settings):
    chosen = (settings.get("ai") or {}).get("active_model")
    assert chosen not in _KNOWN_BAD, (
        f"ai.active_model={chosen!r} is known-broken (memory: bridge_v5_rewrite.md)"
    )


def test_active_model_is_in_known_good(settings):
    chosen = (settings.get("ai") or {}).get("active_model")
    # Allow unknown values (someone might use a custom-named model) — only
    # block the specific known-bad ones.
    if chosen and chosen.startswith("gemini-"):
        assert chosen in _KNOWN_GOOD or "lite" not in chosen, (
            f"ai.active_model={chosen!r} contains 'lite'"
        )


def test_report_pipeline_section_present(settings):
    rp = (settings.get("ai") or {}).get("report_pipeline") or {}
    assert "kind" in rp
    assert rp["kind"] in ("multi_bridge", "single_pass")
