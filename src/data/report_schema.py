"""AI Reports — Structured output schema (R1.2).

Dataclasses + JSON-schema spec for the `Report` / `Finding` contract that
the pipeline emits and the UI renders. Replaces raw-text Gemini output
with a typed, validatable structure so the evidence panel can bind to
findings, the grounding harness can audit claims, and Report History
can show pipeline metadata.

Reference: 3.24.26 build plan UI screenshots (Updated_AI_Reports_Analysis_Canvas.png)
and the R1 spec in conversation history (2026-05-06).

Responsibilities
----------------
- Define the wire format the LLM produces (JSON-fenced blocks).
- Define the in-memory dataclasses the UI consumes.
- Provide round-trip helpers (`Finding.from_dict`, `Report.to_dict`) so
  `analysis_reports.findings_json` can persist + rehydrate cleanly.
- Provide `JSON_SCHEMA` for the parser + the prompt templates.

Public API
----------
- `Severity` enum
- `EvidenceChip`, `TrendPoint`, `Finding`, `Report` dataclasses
- `JSON_SCHEMA` constant (dict, JSON-Schema draft 7)
- `Report.to_dict()` / `Report.from_dict(d)` round-trip

Dependencies
------------
- stdlib only: dataclasses, enum, json, datetime, typing

Dependents
----------
- `src/data/report_parser.py` — parses LLM output into Reports
- `src/data/ai_report_pipeline.py` — emits Reports
- `src/data/report_grounding.py` — audits Findings
- `src/ui/widgets/finding_card.py` — renders Findings
- `src/ui/widgets/report_canvas.py` — lays out Reports

See `docs/AI_REPORTS.md` for the rendering contract.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any


# ──────────────────────────────────────────────────────────────────────
# Enums
# ──────────────────────────────────────────────────────────────────────

class Severity(str, Enum):
    """Finding severity. Maps to UI badge colors."""
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"

    @classmethod
    def from_str(cls, value: Any) -> "Severity":
        """Tolerant parse — case-insensitive, falls back to INFO."""
        if isinstance(value, cls):
            return value
        if not value:
            return cls.INFO
        try:
            return cls(str(value).strip().lower())
        except ValueError:
            return cls.INFO


class ChipKind(str, Enum):
    """Evidence chip variant — drives icon + color."""
    METRIC = "metric"
    TREND = "trend"
    SOURCE = "source"
    COHORT = "cohort"
    DRIVER = "driver"


# ──────────────────────────────────────────────────────────────────────
# Dataclasses
# ──────────────────────────────────────────────────────────────────────

@dataclass
class EvidenceChip:
    """A single supporting fact attached to a finding (e.g. '275 tickets')."""
    label: str
    value: str
    kind: ChipKind = ChipKind.METRIC

    @classmethod
    def from_dict(cls, d: dict) -> "EvidenceChip":
        return cls(
            label=str(d.get("label", "")),
            value=str(d.get("value", "")),
            kind=ChipKind(d.get("kind", "metric")) if d.get("kind") else ChipKind.METRIC,
        )

    def to_dict(self) -> dict:
        return {"label": self.label, "value": self.value, "kind": self.kind.value}


@dataclass
class TrendPoint:
    """One bucket of a finding's trend sparkline."""
    period: str       # e.g. "2026-W14"
    value: float

    @classmethod
    def from_dict(cls, d: dict) -> "TrendPoint":
        return cls(period=str(d.get("period", "")), value=float(d.get("value", 0.0)))

    def to_dict(self) -> dict:
        return {"period": self.period, "value": self.value}


@dataclass
class Finding:
    """One structured finding inside a report.

    Stable `finding_id` so the UI can route clicks back to the same row in
    `analysis_reports.findings_json`. The pipeline assigns IDs as
    `f-{report_short}-{idx}`; the parser preserves them on round-trip.
    """
    finding_id: str
    title: str
    summary: str
    severity: Severity = Severity.INFO
    confidence: float = 0.0
    supporting_ticket_ids: list[str] = field(default_factory=list)
    evidence_chips: list[EvidenceChip] = field(default_factory=list)
    trend: list[TrendPoint] = field(default_factory=list)
    body_md: str = ""           # multi-line markdown drilldown
    trcs_touched: list[str] = field(default_factory=list)
    cohort: str = ""            # short cohort label, e.g. "Cigna, Humana, BCBS"

    @classmethod
    def from_dict(cls, d: dict) -> "Finding":
        return cls(
            finding_id=str(d.get("finding_id", "")),
            title=str(d.get("title", "")),
            summary=str(d.get("summary", "")),
            severity=Severity.from_str(d.get("severity")),
            confidence=_safe_float(d.get("confidence")),
            supporting_ticket_ids=[str(t) for t in d.get("supporting_ticket_ids", [])],
            evidence_chips=[EvidenceChip.from_dict(c) for c in d.get("evidence_chips", [])],
            trend=[TrendPoint.from_dict(t) for t in d.get("trend", [])],
            body_md=str(d.get("body_md", "")),
            trcs_touched=[str(t) for t in d.get("trcs_touched", [])],
            cohort=str(d.get("cohort", "")),
        )

    def to_dict(self) -> dict:
        return {
            "finding_id": self.finding_id,
            "title": self.title,
            "summary": self.summary,
            "severity": self.severity.value,
            "confidence": self.confidence,
            "supporting_ticket_ids": list(self.supporting_ticket_ids),
            "evidence_chips": [c.to_dict() for c in self.evidence_chips],
            "trend": [t.to_dict() for t in self.trend],
            "body_md": self.body_md,
            "trcs_touched": list(self.trcs_touched),
            "cohort": self.cohort,
        }


@dataclass
class Report:
    """A full structured report. The pipeline returns this; the UI renders it.

    `pipeline_kind` is `"single_pass"` (one Gemini call) or `"multi_bridge"`
    (specialist fan-out → convergence). `bridges_used` and
    `specialist_results` are populated only on the multi-bridge path.
    """
    report_id: str
    title: str
    executive_summary: str = ""
    findings: list[Finding] = field(default_factory=list)
    generated_at: str = ""
    scope: dict = field(default_factory=dict)        # {tickets, sources, trcs, date_range}
    pipeline_kind: str = "single_pass"
    specialist_count: int = 0
    bridges_used: int = 1
    cost_usd: float = 0.0
    duration_sec: float = 0.0
    accuracy_score: float | None = None              # populated by report_grounding
    accuracy_flags: list[str] = field(default_factory=list)
    raw_markdown: str = ""                            # legacy fallback; preserved for rehydration

    @classmethod
    def empty(cls, report_id: str = "", title: str = "") -> "Report":
        return cls(
            report_id=report_id or f"r-{int(datetime.now(timezone.utc).timestamp())}",
            title=title or "Report",
            generated_at=datetime.now(timezone.utc).isoformat(),
        )

    @classmethod
    def from_dict(cls, d: dict) -> "Report":
        return cls(
            report_id=str(d.get("report_id", "")),
            title=str(d.get("title", "")),
            executive_summary=str(d.get("executive_summary", "")),
            findings=[Finding.from_dict(f) for f in d.get("findings", [])],
            generated_at=str(d.get("generated_at", "")),
            scope=dict(d.get("scope") or {}),
            pipeline_kind=str(d.get("pipeline_kind", "single_pass")),
            specialist_count=int(d.get("specialist_count", 0) or 0),
            bridges_used=int(d.get("bridges_used", 1) or 1),
            cost_usd=float(d.get("cost_usd", 0.0) or 0.0),
            duration_sec=float(d.get("duration_sec", 0.0) or 0.0),
            accuracy_score=(
                float(d["accuracy_score"]) if d.get("accuracy_score") is not None else None
            ),
            accuracy_flags=[str(f) for f in d.get("accuracy_flags", [])],
            raw_markdown=str(d.get("raw_markdown", "")),
        )

    def to_dict(self) -> dict:
        return {
            "report_id": self.report_id,
            "title": self.title,
            "executive_summary": self.executive_summary,
            "findings": [f.to_dict() for f in self.findings],
            "generated_at": self.generated_at,
            "scope": dict(self.scope),
            "pipeline_kind": self.pipeline_kind,
            "specialist_count": self.specialist_count,
            "bridges_used": self.bridges_used,
            "cost_usd": self.cost_usd,
            "duration_sec": self.duration_sec,
            "accuracy_score": self.accuracy_score,
            "accuracy_flags": list(self.accuracy_flags),
            "raw_markdown": self.raw_markdown,
        }

    def to_json(self) -> str:
        """Compact JSON for `analysis_reports.findings_json` persistence."""
        return json.dumps(self.to_dict(), separators=(",", ":"))

    @classmethod
    def from_json(cls, s: str) -> "Report":
        """Rehydrate from `analysis_reports.findings_json`."""
        if not s:
            return cls.empty()
        return cls.from_dict(json.loads(s))


# ──────────────────────────────────────────────────────────────────────
# JSON-Schema spec (driven into prompts + parser)
# ──────────────────────────────────────────────────────────────────────

# JSON Schema draft 7. Embedded in prompt templates verbatim so the LLM
# knows the contract; also drives optional jsonschema validation in the
# parser. We keep this hand-authored (rather than derived) so changes
# require deliberate edits to both the schema and the prompts.
JSON_SCHEMA: dict = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "AlmaReport",
    "type": "object",
    "required": ["title", "findings"],
    "properties": {
        "title": {"type": "string", "minLength": 1, "maxLength": 200},
        "executive_summary": {"type": "string", "maxLength": 4000},
        "findings": {
            "type": "array",
            "minItems": 1,
            "maxItems": 25,
            "items": {
                "type": "object",
                "required": ["title", "summary", "severity"],
                "properties": {
                    "title": {"type": "string", "minLength": 1, "maxLength": 160},
                    "summary": {"type": "string", "minLength": 1, "maxLength": 2000},
                    "severity": {"type": "string", "enum": ["high", "medium", "low", "info"]},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "supporting_ticket_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "maxItems": 50,
                    },
                    "evidence_chips": {
                        "type": "array",
                        "maxItems": 12,
                        "items": {
                            "type": "object",
                            "required": ["label", "value"],
                            "properties": {
                                "label": {"type": "string", "maxLength": 60},
                                "value": {"type": "string", "maxLength": 60},
                                "kind": {
                                    "type": "string",
                                    "enum": ["metric", "trend", "source", "cohort", "driver"],
                                },
                            },
                        },
                    },
                    "trend": {
                        "type": "array",
                        "maxItems": 26,
                        "items": {
                            "type": "object",
                            "required": ["period", "value"],
                            "properties": {
                                "period": {"type": "string"},
                                "value": {"type": "number"},
                            },
                        },
                    },
                    "body_md": {"type": "string", "maxLength": 8000},
                    "trcs_touched": {
                        "type": "array",
                        "items": {"type": "string"},
                        "maxItems": 25,
                    },
                    "cohort": {"type": "string", "maxLength": 240},
                },
            },
        },
    },
}


# ──────────────────────────────────────────────────────────────────────
# Convenience: prompt-friendly schema fragment
# ──────────────────────────────────────────────────────────────────────

def schema_for_prompt() -> str:
    """Compact JSON-Schema string for embedding in prompt templates.

    Templates inline this so the LLM has the exact contract. Indented +
    sorted-keys for stable diffing across prompt edits.
    """
    return json.dumps(JSON_SCHEMA, indent=2, sort_keys=True)


def _safe_float(v) -> float:
    """Coerce v to float; returns 0.0 on TypeError/ValueError.

    Used by Finding.from_dict so DB rehydration tolerates bad confidence
    values (e.g. NULL, empty string) without raising during report-history
    rendering. The parser layer applies range clamping ([0,1]); we only
    need null-safety here.
    """
    if v is None:
        return 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0
