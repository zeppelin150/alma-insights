"""AI Reports — Scoped usage tracker (cost-tracking add-on, 2026-05-06).

Tiny proxy around `UsageTracker` that auto-injects a `scan_id` into every
`log_call`. The existing bridge code (src/agents/acp_bridge.py:770 and
src/agents/report_bridge_client.py:174) calls
``tracker.log_call(source, tokens_in, tokens_out, model)`` without a
scope identifier, so post-pipeline aggregation can't pick out *just*
the calls belonging to one report run.

Why this exists
---------------
We want `Report.cost_usd` to reflect exactly the LLM spend incurred by
one `AIReportPipeline.run()` invocation. The `gemini_usage` table
already supports per-scan aggregation via `UsageTracker.get_scan_cost`,
but only when `scan_id` is set on the row. Rather than thread `scan_id`
through every `log_call` callsite, we wrap the tracker.

Public API
----------
- `ScopedUsageTracker(base_tracker, scan_id)`
- `.estimate_tokens(text) -> int`           # passthrough
- `.log_call(source, tokens_in, tokens_out, model="…", scan_id=None)`
    - When `scan_id` is omitted on the call site (bridges, ReportBridgeClient),
      the wrapper substitutes its bound scope id.
- `.scan_id` (read-only)

Dependencies
------------
- src.data.usage_tracker.UsageTracker (duck-typed)

Dependents
----------
- src.data.ai_report_pipeline (creates + attaches per run)
- src.data.specialist_pipeline (forwards to each bridge)
"""

from __future__ import annotations

import logging

logger = logging.getLogger("alma.scoped_usage_tracker")


class ScopedUsageTracker:
    """Proxy that auto-tags every `log_call` with a fixed `scan_id`.

    Behaves identically to UsageTracker for `estimate_tokens` and any
    other passthrough attribute access (so existing bridge code that
    e.g. calls ``self._usage_tracker.estimate_tokens(...)`` keeps working
    without modification).
    """

    def __init__(self, base_tracker, scan_id: str) -> None:
        if base_tracker is None:
            raise ValueError("ScopedUsageTracker requires a base tracker")
        self._base = base_tracker
        self._scan_id = str(scan_id)

    @property
    def scan_id(self) -> str:
        return self._scan_id

    # ── Token-estimation passthrough ───────────────────────────────

    def estimate_tokens(self, text: str) -> int:
        """Delegate to base tracker; supports both static and instance forms."""
        return self._base.estimate_tokens(text)

    # ── Scope-injecting log_call ───────────────────────────────────

    def log_call(self, source: str, tokens_in: int, tokens_out: int,
                 model: str = "gemini-2.5-flash",
                 scan_id: str | None = None) -> None:
        """Inject the bound scan_id when the caller didn't supply one."""
        return self._base.log_call(
            source=source,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            model=model,
            scan_id=scan_id or self._scan_id,
        )

    # ── Attribute fall-through ─────────────────────────────────────

    def __getattr__(self, name: str):
        """Forward any other attribute access to the base tracker.

        Lets us support occasional callers that reach for `.db` or
        `.get_scan_cost` directly without having to enumerate every
        method of UsageTracker.
        """
        return getattr(self._base, name)
