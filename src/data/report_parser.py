"""AI Reports — Structured output parser (R1.3).

Extracts a `Report` from raw LLM output. The contract: the LLM emits one
fenced ` ```json ` block containing an object that matches `JSON_SCHEMA`
in `report_schema.py`. The parser:

1. Locates the fenced block (tolerates surrounding commentary).
2. Parses + validates against the schema.
3. Hydrates a `Report` dataclass.
4. On any failure, falls back to a "legacy markdown" Report whose
   `raw_markdown` carries the full text and `findings` is empty — the UI
   still renders it as a single big block, the evidence panel goes to
   metadata view, and the grounding harness flags `accuracy_flags=
   ['legacy_unparseable']`.

This mirrors the NDJSON-pivot pattern used for NLP classification — the
LLM emits structured text, we parse locally, no MCP function-calling
plumbing needed.

Reference: plan rebuild 2026-05-06 (R1.3 spec) and conversation history
on the structured-output contract.

Public API
----------
- `parse_report(raw_text, *, fallback_title="Report",
   pipeline_kind="single_pass") -> Report`
- `extract_json_block(raw_text) -> str | None`
- `LegacyMarkdownFallback` exception (raised internally, not propagated)

Dependencies
------------
- stdlib: re, json, logging
- jsonschema (optional — if installed we run full schema validation;
  otherwise we run a permissive local check)
- src.data.report_schema

Dependents
----------
- src.data.ai_report_pipeline (calls parse_report after each generate)
- tests/test_report_parser.py
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass

from src.data.report_schema import (
    Finding, Report, Severity, EvidenceChip, ChipKind, TrendPoint, JSON_SCHEMA,
)

logger = logging.getLogger("alma.report_parser")


# ──────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────

# Fenced JSON block — tolerant of optional language tag variations and
# surrounding whitespace. We only grab the FIRST fenced block; the prompt
# instructs the LLM to emit exactly one.
_JSON_FENCE_RE = re.compile(
    r"```(?:json|JSON|json5)?\s*\n?(?P<body>\{.*?\})\s*\n?```",
    re.DOTALL,
)

# Bare-JSON fallback — when the LLM forgets the fence, look for a top-level
# object that starts with `{"title":` or `{"findings":` near the start.
# We deliberately keep this narrow to avoid false positives in markdown
# code samples.
_BARE_JSON_RE = re.compile(
    r"^\s*(?P<body>\{(?:[^{}]|\{[^{}]*\})*\"findings\"\s*:.*?\})\s*$",
    re.DOTALL | re.MULTILINE,
)

_MAX_RAW_PREVIEW = 400  # for log lines


class LegacyMarkdownFallback(Exception):
    """Internal sentinel — caught by parse_report to trigger fallback."""


# ──────────────────────────────────────────────────────────────────────
# Public entry point
# ──────────────────────────────────────────────────────────────────────

def parse_report(
    raw_text: str,
    *,
    fallback_title: str = "Report",
    pipeline_kind: str = "single_pass",
    report_id: str | None = None,
) -> Report:
    """Parse LLM output into a `Report`.

    Never raises — on any failure returns a Report with `raw_markdown`
    populated and `accuracy_flags` containing `legacy_unparseable`.
    """
    rid = report_id or _new_report_id()
    if not raw_text or not raw_text.strip():
        return _empty_legacy(rid, fallback_title, pipeline_kind, raw_text or "")

    try:
        block = extract_json_block(raw_text)
        if not block:
            raise LegacyMarkdownFallback("no JSON block found")
        data = json.loads(block)
        _validate(data)
        return _hydrate(data, rid, raw_text, pipeline_kind)
    except (LegacyMarkdownFallback, json.JSONDecodeError, ValueError) as exc:
        logger.info(
            "parse_report fallback (%s); raw_preview=%r",
            exc, raw_text[:_MAX_RAW_PREVIEW],
        )
        return _legacy_fallback(rid, fallback_title, pipeline_kind, raw_text)


def extract_json_block(raw_text: str) -> str | None:
    """Return the JSON body string, or None if no block detected.

    Tries the fenced form first, then the bare top-level form.
    """
    match = _JSON_FENCE_RE.search(raw_text)
    if match:
        return match.group("body").strip()
    match = _BARE_JSON_RE.search(raw_text)
    if match:
        return match.group("body").strip()
    return None


# ──────────────────────────────────────────────────────────────────────
# Validation
# ──────────────────────────────────────────────────────────────────────

def _validate(data: dict) -> None:
    """Validate `data` against JSON_SCHEMA. Raises ValueError on failure.

    Uses `jsonschema` when available; falls back to a local permissive
    check that enforces the *required* fields only. This keeps the parser
    importable in environments where jsonschema isn't installed (e.g.
    M1 prod bundle) while still catching the worst LLM mistakes.
    """
    try:
        import jsonschema  # type: ignore
        jsonschema.validate(instance=data, schema=JSON_SCHEMA)
        return
    except ImportError:
        pass
    _local_validate(data)


def _local_validate(data: dict) -> None:
    """Permissive check — enforces required keys + finding shape only."""
    if not isinstance(data, dict):
        raise ValueError("top-level must be an object")
    if "findings" not in data or not isinstance(data["findings"], list):
        raise ValueError("missing or invalid 'findings' array")
    if not data["findings"]:
        raise ValueError("'findings' must have at least one item")
    for idx, finding in enumerate(data["findings"]):
        if not isinstance(finding, dict):
            raise ValueError(f"finding[{idx}] is not an object")
        for required in ("title", "summary", "severity"):
            if required not in finding:
                raise ValueError(f"finding[{idx}] missing '{required}'")


# ──────────────────────────────────────────────────────────────────────
# Hydration
# ──────────────────────────────────────────────────────────────────────

def _hydrate(data: dict, report_id: str, raw_text: str, pipeline_kind: str) -> Report:
    """Build a Report from validated dict data."""
    title = str(data.get("title") or "Report")
    findings_data = data.get("findings", [])
    findings = [
        _hydrate_finding(f, report_id, idx)
        for idx, f in enumerate(findings_data)
    ]
    return Report(
        report_id=report_id,
        title=title,
        executive_summary=str(data.get("executive_summary", "")),
        findings=findings,
        scope=dict(data.get("scope") or {}),
        pipeline_kind=pipeline_kind,
        raw_markdown=raw_text,
    )


def _hydrate_finding(d: dict, report_id: str, idx: int) -> Finding:
    """Build a Finding from a finding dict; auto-assigns ID if missing."""
    fid = str(d.get("finding_id") or f"f-{report_id}-{idx:02d}")
    return Finding(
        finding_id=fid,
        title=str(d["title"]),
        summary=str(d["summary"]),
        severity=Severity.from_str(d.get("severity")),
        confidence=_to_float(d.get("confidence", 0.0), default=0.0, lo=0.0, hi=1.0),
        supporting_ticket_ids=[str(t) for t in d.get("supporting_ticket_ids", [])][:50],
        evidence_chips=_hydrate_chips(d.get("evidence_chips", [])),
        trend=_hydrate_trend(d.get("trend", [])),
        body_md=str(d.get("body_md", "")),
        trcs_touched=[str(t) for t in d.get("trcs_touched", [])][:25],
        cohort=str(d.get("cohort", ""))[:240],
    )


def _hydrate_chips(chips: list) -> list[EvidenceChip]:
    out: list[EvidenceChip] = []
    for c in chips[:12]:
        if not isinstance(c, dict):
            continue
        out.append(EvidenceChip.from_dict(c))
    return out


def _hydrate_trend(points: list) -> list[TrendPoint]:
    out: list[TrendPoint] = []
    for p in points[:26]:
        if not isinstance(p, dict):
            continue
        try:
            out.append(TrendPoint.from_dict(p))
        except (ValueError, TypeError):
            continue
    return out


# ──────────────────────────────────────────────────────────────────────
# Fallbacks
# ──────────────────────────────────────────────────────────────────────

def _empty_legacy(report_id: str, title: str, pipeline_kind: str, raw: str) -> Report:
    """Empty-text path: still returns a valid Report shell."""
    r = Report.empty(report_id=report_id, title=title)
    r.pipeline_kind = pipeline_kind
    r.raw_markdown = raw
    r.accuracy_flags = ["empty_response"]
    return r


def _legacy_fallback(report_id: str, title: str, pipeline_kind: str, raw: str) -> Report:
    """Build a Report from non-structured markdown so the UI can still render."""
    r = Report.empty(report_id=report_id, title=title)
    r.pipeline_kind = pipeline_kind
    r.raw_markdown = raw
    r.accuracy_flags = ["legacy_unparseable"]
    return r


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────

def _to_float(v, *, default: float, lo: float, hi: float) -> float:
    """Coerce v to a float clamped to [lo, hi]; default on failure."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, f))


def _new_report_id() -> str:
    """Short, URL-safe report ID — used when caller doesn't supply one."""
    return f"r-{uuid.uuid4().hex[:10]}"
