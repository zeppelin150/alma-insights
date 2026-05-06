"""Canonical taxonomies for chat-tool filters.

Source-of-truth lists for free-text columns in ticket_index. The DB
columns themselves stay open (no enum constraint), so the NLP
classifier can write any value. These constants drive two things:

  1. **Partial gating in chat-tool handlers.** When the LLM passes a
     filter value (e.g. friction_type='feature_broken') we check it
     against the canonical list. Exact match → run normally. Different
     case → silently coerce. Novel value → run anyway, but tag the
     response with a `gate_warning` so drift is visible.

  2. **Periodic taxonomy drift audit.** tools/audit_taxonomies.py runs
     monthly, compares the actual distinct values in production data
     against these constants, and proposes additions, retirements,
     and likely typos for engineer review.

When the audit proposes an addition you accept, update the relevant
tuple below AND bump the LAST_REVIEWED date in the docstring of that
constant. The audit's `tools/audit_reports/last_snapshot.json` becomes
the new baseline for next month's diff.

Audit cadence: 30 days. See docs/TAXONOMY_AUDIT.md for the full
review/promotion workflow.
"""

from __future__ import annotations

# Last reviewed: 2026-04-23 — initial extraction from prod ticket_index.
# Next audit due: 2026-05-23. Run: python tools/audit_taxonomies.py
CANONICAL_FRICTION_TYPES: tuple[str, ...] = (
    "incorrect_charge",
    "feature_broken",
    "repeat_contact",
    "access_blocked",
    "policy_confusion",
    "missing_information",
    "self_serve_failure",
    "process_delay",
    "automation_loop",
    "communication_gap",
    "escalation_demand",
    "other",
)

# Last reviewed: 2026-04-23. Set by VOC sentiment classifier.
CANONICAL_SENTIMENT_POLARITY: tuple[str, ...] = (
    "negative",
    "mixed",
    "neutral",
    "positive",
)

# Last reviewed: 2026-04-23. Set by anomaly detection engine.
# 'normal' is the no-anomaly baseline; 'unusual' / 'critical' are flagged states.
CANONICAL_ANOMALY_FLAGS: tuple[str, ...] = (
    "normal",
    "unusual",
    "critical",
)


# ──────────────────────────────────────────────────────────────────
# Helpers — used by handlers to do the partial gate
# ──────────────────────────────────────────────────────────────────

def coerce_or_warn(
    value: str | None,
    canonical: tuple[str, ...],
    *,
    column_name: str,
) -> tuple[str | None, str | None]:
    """Partial gate: return (effective_value, warning).

    - None / "" input          → (None, None)
    - exact canonical match    → (value, None)
    - case-insensitive match   → (canonical_value, None)   silent coerce
    - novel value              → (value, warning_string)   pass-through + flag

    The warning string mentions the column, the unknown value, and where to
    look for the canonical list — so it doubles as drift-audit signal that
    bubbles all the way out to the LLM caller.
    """
    if not value:
        return None, None
    if value in canonical:
        return value, None
    # Try case-insensitive coercion
    lower = value.lower()
    for c in canonical:
        if c.lower() == lower:
            return c, None
    # Novel value — pass through but warn
    warning = (
        f"{column_name}={value!r} is not in the canonical taxonomy "
        f"({len(canonical)} known values). Returning best-effort match against "
        f"the column. If this value is recurrent, the next monthly drift audit "
        f"(tools/audit_taxonomies.py) will surface it for promotion."
    )
    return value, warning
