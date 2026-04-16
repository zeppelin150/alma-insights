"""
Alma Insights — Analyst Report Formatter (Phase 5.5A)

Parses JSON content from the analyst_reports table into readable markdown
for display across reporting pages.

The analyst agent produces 4 report types:
  - synthesis: Cross-TRC shared root causes, systemic issues, correlations
  - audit: Quality grades, quality_score, common errors
  - novelty: VALID/DUPLICATE/MERGE verdicts per ticket
  - merge: Pattern merge suggestions with confidence scores

Usage:
    reports = db.get_analyst_reports(scan_id="abc")
    md = format_analyst_reports_as_markdown(reports)
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger("alma.analyst_formatter")


def format_analyst_reports_as_markdown(reports: list[dict]) -> str:
    """Convert a list of analyst_reports rows into a single markdown document.

    Args:
        reports: List of dicts with keys: report_type, content, metrics, created_at

    Returns:
        Formatted markdown string with sections for each report type found.
    """
    if not reports:
        return ""

    sections = []
    sections.append("## Analyst Reports\n")

    # Group by report type (in presentation order)
    type_order = ["synthesis", "audit", "novelty", "merge"]
    by_type = {}
    for r in reports:
        rt = r.get("report_type", "unknown")
        by_type.setdefault(rt, []).append(r)

    for rt in type_order:
        if rt not in by_type:
            continue
        # Use the most recent report of each type
        report = by_type[rt][-1]
        content = _parse_content(report.get("content"))
        if content is None:
            continue

        formatter = _FORMATTERS.get(rt)
        if formatter:
            section = formatter(content)
            if section:
                sections.append(section)

    return "\n".join(sections) if len(sections) > 1 else ""


def get_latest_analyst_summary(db: object, scan_id: str | None = None) -> tuple[str, dict]:
    """Get formatted markdown + raw metrics for the latest completed scan.

    Args:
        db: DatabaseManager instance
        scan_id: Specific scan ID. If None, uses the most recent scan.

    Returns:
        (markdown_text, raw_metrics_dict)
    """
    try:
        if not scan_id:
            row = db.conn.execute(
                "SELECT scan_id FROM nlp_scan_runs ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            if not row:
                return "", {}
            scan_id = row["scan_id"]

        rows = db.conn.execute(
            "SELECT report_type, content, metrics, created_at "
            "FROM analyst_reports WHERE scan_id = ? ORDER BY created_at",
            (scan_id,),
        ).fetchall()
        reports = [dict(r) for r in rows]

        md = format_analyst_reports_as_markdown(reports)

        # Aggregate metrics
        metrics = {}
        for r in reports:
            m = _parse_content(r.get("metrics"))
            if m:
                metrics[r["report_type"]] = m

        return md, metrics

    except Exception as e:
        logger.debug(f"get_latest_analyst_summary failed: {e}")
        return "", {}


# ══════════════════════════════════════════════════════════════════════
# Per-type formatters
# ══════════════════════════════════════════════════════════════════════

def _format_synthesis(content: dict) -> str:
    """Format synthesis report: shared root causes, systemic issues, correlations."""
    parts = ["### Cross-TRC Synthesis\n"]

    summary = content.get("summary")
    if summary:
        parts.append(f"> {summary}\n")

    # Shared root causes
    causes = content.get("shared_root_causes", [])
    if causes:
        parts.append("**Shared Root Causes**\n")
        for c in causes:
            cause_text = c.get("cause", "Unknown")
            affected = c.get("affected_trcs", [])
            evidence = c.get("evidence", "")
            trc_tags = ", ".join(f"`{t}`" for t in affected) if affected else ""
            parts.append(f"- **{cause_text}**")
            if trc_tags:
                parts.append(f"  - Affected TRCs: {trc_tags}")
            if evidence:
                parts.append(f"  - Evidence: {evidence}")
        parts.append("")

    # Systemic issues
    issues = content.get("systemic_issues", [])
    if issues:
        parts.append("**Systemic Issues**\n")
        parts.append("| Issue | Scope | Severity |")
        parts.append("|-------|-------|----------|")
        for issue in issues:
            parts.append(
                f"| {issue.get('issue', '—')} "
                f"| {issue.get('scope', '—')} "
                f"| {issue.get('severity', '—')} |"
            )
        parts.append("")

    # Correlations
    corrs = content.get("correlations", [])
    if corrs:
        parts.append("**Cross-TRC Correlations**\n")
        for c in corrs:
            a = c.get("trc_a", "?")
            b = c.get("trc_b", "?")
            desc = c.get("correlation", "")
            parts.append(f"- `{a}` ↔ `{b}`: {desc}")
        parts.append("")

    return "\n".join(parts)


def _format_audit(content: dict) -> str:
    """Format audit report: quality score, grades, common errors."""
    parts = ["### Quality Audit\n"]

    score = content.get("quality_score")
    if score is not None:
        pct = f"{score * 100:.0f}%"
        parts.append(f"**Overall Quality Score**: {pct}\n")

    # Grade distribution
    grades = content.get("grades", [])
    if grades:
        dist = {}
        for g in grades:
            grade = g.get("grade", "UNKNOWN")
            dist[grade] = dist.get(grade, 0) + 1

        parts.append("**Grade Distribution**\n")
        parts.append("| Grade | Count |")
        parts.append("|-------|-------|")
        for grade in ["CORRECT", "PARTIAL", "INCORRECT", "UNCERTAIN"]:
            if grade in dist:
                parts.append(f"| {grade} | {dist[grade]} |")
        parts.append("")

    # Common errors
    errors = content.get("common_errors", [])
    if errors:
        parts.append("**Common Errors**\n")
        for err in errors:
            parts.append(f"- {err}")
        parts.append("")

    # Recommendations
    recs = content.get("recommendations", [])
    if recs:
        parts.append("**Recommendations**\n")
        for rec in recs:
            parts.append(f"- {rec}")
        parts.append("")

    return "\n".join(parts)


def _format_novelty(content: dict) -> str:
    """Format novelty report: validation verdicts summary."""
    parts = ["### Novelty Validation\n"]

    summary = content.get("summary", {})
    if summary:
        validated = summary.get("validated", 0)
        rejected = summary.get("rejected", 0)
        merged = summary.get("merged", 0)
        total = validated + rejected + merged
        parts.append(f"**Results**: {total} patterns evaluated\n")
        parts.append("| Verdict | Count |")
        parts.append("|---------|-------|")
        parts.append(f"| VALID | {validated} |")
        parts.append(f"| DUPLICATE | {rejected} |")
        parts.append(f"| MERGE | {merged} |")
        parts.append("")

    # Show some detail for non-valid items
    validations = content.get("validations", [])
    notable = [v for v in validations if v.get("verdict") != "VALID"]
    if notable:
        parts.append("**Notable Findings**\n")
        for v in notable[:10]:  # cap at 10
            verdict = v.get("verdict", "?")
            ticket = v.get("ticket_id", "?")
            match = v.get("existing_match", "")
            notes = v.get("notes", "")
            line = f"- `{ticket}` → **{verdict}**"
            if match:
                line += f" (matches: {match})"
            if notes:
                line += f" — {notes}"
            parts.append(line)
        if len(notable) > 10:
            parts.append(f"- *...and {len(notable) - 10} more*")
        parts.append("")

    return "\n".join(parts)


def _format_merge(content: dict) -> str:
    """Format merge suggestions report."""
    parts = ["### Pattern Merge Suggestions\n"]

    suggestions = content.get("merge_suggestions", [])
    count = content.get("count", len(suggestions))
    if not suggestions:
        parts.append("*No merge suggestions for this scan.*\n")
        return "\n".join(parts)

    parts.append(f"**{count} merge suggestion(s) identified**\n")
    for s in suggestions:
        primary = s.get("primary_label", "?")
        candidates = s.get("merge_candidates", [])
        rationale = s.get("rationale", "")
        confidence = s.get("confidence")

        cand_list = ", ".join(f"`{c}`" for c in candidates) if candidates else "—"
        conf_str = f" ({confidence:.0%} confidence)" if confidence else ""

        parts.append(f"- **Keep**: `{primary}` ← merge {cand_list}{conf_str}")
        if rationale:
            parts.append(f"  - {rationale}")

    parts.append("")
    return "\n".join(parts)


# ── Formatter registry ──

_FORMATTERS = {
    "synthesis": _format_synthesis,
    "audit": _format_audit,
    "novelty": _format_novelty,
    "merge": _format_merge,
}


# ── Helpers ──

def _parse_content(raw) -> dict | list | None:
    """Parse JSON string or return dict/list as-is."""
    if raw is None:
        return None
    if isinstance(raw, (dict, list)):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return None
    return None
