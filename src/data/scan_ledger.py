"""
Alma Insights — Scan Ledger Builder (Hardening H3)

Builds structured markdown summaries from PHI-free tables for use as
context when routing tasks to Claude (ops lane).  Claude NEVER receives
raw ticket text — only aggregated counts, patterns, and scores.

Two entry points:
    build_current_ledger(db, scan_id)    — latest scan snapshot
    build_historical_ledger(db, n_scans) — multi-scan trend view

Source tables (all PHI-free):
    nlp_scan_runs, nlp_ticket_classifications (aggregates only),
    sub_patterns, nlp_findings, source_trc_daily,
    watchlist_alerts, guru_friction_coverage, guru_effectiveness_baselines
"""

from __future__ import annotations

import logging

logger = logging.getLogger("alma.scan_ledger")


def build_current_ledger(db: object, scan_id: str) -> str:
    """Build a structured markdown ledger for a single scan.

    Args:
        db: DatabaseManager instance
        scan_id: The scan to summarize

    Returns:
        Markdown string with scan summary, classification breakdown,
        top sub-patterns, findings, and active alerts.
    """
    conn = db.get_connection() if hasattr(db, 'get_connection') else db.conn
    sections = []

    # ── Scan Overview ──
    run = conn.execute(
        "SELECT scan_id, created_at, status, date_range_start, date_range_end, "
        "trc_filter, total_tickets, total_batches, completed_batches, "
        "estimated_cost_usd, actual_cost_usd "
        "FROM nlp_scan_runs WHERE scan_id = ?",
        (scan_id,),
    ).fetchone()

    if not run:
        return f"# Scan Ledger\n\nNo scan found with ID: {scan_id}"

    sections.append("# Scan Ledger\n")
    sections.append(f"**Scan ID**: {run[0]}")
    sections.append(f"**Date Range**: {run[3]} to {run[4]}")
    sections.append(f"**Status**: {run[2]}")
    sections.append(f"**Tickets Scanned**: {run[6]}")
    sections.append(f"**Batches**: {run[8]}/{run[7]} completed")
    if run[5]:
        sections.append(f"**TRC Filter**: {run[5]}")
    sections.append("")

    # ── Classification Breakdown (aggregates only, no ticket text) ──
    trc_breakdown = conn.execute("""
        SELECT trc, COUNT(*) as cnt,
               ROUND(AVG(CASE WHEN sentiment_intensity IS NOT NULL
                         THEN sentiment_intensity END), 1) as avg_sent,
               SUM(CASE WHEN is_novel = 1 THEN 1 ELSE 0 END) as novel_count
        FROM nlp_ticket_classifications
        WHERE scan_id = ?
        GROUP BY trc
        ORDER BY cnt DESC
        LIMIT 20
    """, (scan_id,)).fetchall()

    if trc_breakdown:
        sections.append("## Classification Breakdown\n")
        sections.append("| TRC | Count | Avg Sentiment | Novel |")
        sections.append("|-----|-------|---------------|-------|")
        for row in trc_breakdown:
            avg_s = f"{row[2]:.1f}" if row[2] is not None else "—"
            sections.append(f"| {row[0]} | {row[1]} | {avg_s} | {row[3]} |")
        sections.append("")

    # ── Top Sub-Patterns ──
    patterns = conn.execute("""
        SELECT trc, label, friction_type, tier, lifetime_tickets
        FROM sub_patterns
        WHERE merged_into IS NULL AND tier != 'retired'
        ORDER BY lifetime_tickets DESC
        LIMIT 15
    """).fetchall()

    if patterns:
        sections.append("## Top Sub-Patterns\n")
        sections.append("| TRC | Label | Friction Type | Tier | Lifetime Tickets |")
        sections.append("|-----|-------|---------------|------|-----------------|")
        for p in patterns:
            ft = p[2] or "—"
            sections.append(f"| {p[0]} | {p[1]} | {ft} | {p[3]} | {p[4]} |")
        sections.append("")

    # ── Findings ──
    findings = conn.execute("""
        SELECT finding_type, title, ticket_count, impact_score
        FROM nlp_findings
        WHERE scan_id = ?
        ORDER BY impact_score DESC
        LIMIT 10
    """, (scan_id,)).fetchall()

    if findings:
        sections.append("## Key Findings\n")
        for f in findings:
            impact = f"{f[3]:.2f}" if f[3] is not None else "—"
            sections.append(f"- **[{f[0]}]** {f[1]} ({f[2]} tickets, impact: {impact})")
        sections.append("")

    # ── Active Watchlist Alerts ──
    alerts = _get_active_alerts(conn)
    if alerts:
        sections.append("## Active Watchlist Alerts\n")
        for a in alerts:
            sections.append(
                f"- **{a[0]}** [{a[1]}] — {a[2]} tickets, TRC: {a[3]}"
            )
        sections.append("")

    # ── Guru Friction Coverage Summary ──
    guru_summary = _get_guru_coverage_summary(conn)
    if guru_summary:
        sections.append("## Guru Coverage Summary\n")
        sections.append(f"- **Total friction types**: {guru_summary['total']}")
        sections.append(f"- **Covered (score >= 0.5)**: {guru_summary['covered']}")
        sections.append(f"- **Gaps (score < 0.5)**: {guru_summary['gaps']}")
        sections.append(f"- **Average coverage score**: {guru_summary['avg_score']:.2f}")
        sections.append("")

    return "\n".join(sections)


def build_historical_ledger(db: object, num_scans: int = 5) -> str:
    """Build a multi-scan trend view from PHI-free tables.

    Args:
        db: DatabaseManager instance
        num_scans: Number of recent scans to include

    Returns:
        Markdown string with scan history and TRC volume trends.
    """
    conn = db.get_connection() if hasattr(db, 'get_connection') else db.conn
    sections = []

    # ── Recent Scans ──
    scans = conn.execute("""
        SELECT scan_id, created_at, status, total_tickets,
               date_range_start, date_range_end, actual_cost_usd
        FROM nlp_scan_runs
        ORDER BY created_at DESC
        LIMIT ?
    """, (num_scans,)).fetchall()

    if not scans:
        return "# Historical Ledger\n\nNo scan history available."

    sections.append("# Historical Ledger\n")
    sections.append("## Recent Scans\n")
    sections.append("| Scan ID | Date | Status | Tickets | Range | Cost |")
    sections.append("|---------|------|--------|---------|-------|------|")
    for s in scans:
        cost = f"${s[6]:.2f}" if s[6] else "—"
        scan_short = s[0][:12] + "..." if len(s[0]) > 15 else s[0]
        sections.append(
            f"| {scan_short} | {s[1][:10]} | {s[2]} | {s[3]} | "
            f"{s[4]}→{s[5]} | {cost} |"
        )
    sections.append("")

    # ── TRC Volume Trends (from source_trc_daily) ──
    try:
        trends = conn.execute("""
            SELECT trc_code, SUM(count) as total,
                   ROUND(AVG(avg_sentiment), 2) as avg_sent,
                   COUNT(DISTINCT day_bucket) as days
            FROM source_trc_daily
            WHERE day_bucket >= date('now', '-30 days')
            GROUP BY trc_code
            ORDER BY total DESC
            LIMIT 15
        """).fetchall()

        if trends:
            sections.append("## TRC Volume Trends (Last 30 Days)\n")
            sections.append("| TRC | Total Volume | Avg Sentiment | Active Days |")
            sections.append("|-----|-------------|---------------|-------------|")
            for t in trends:
                avg_s = f"{t[2]:.2f}" if t[2] is not None else "—"
                sections.append(f"| {t[0]} | {t[1]} | {avg_s} | {t[3]} |")
            sections.append("")
    except Exception:
        pass  # source_trc_daily may not exist yet

    # ── Sub-Pattern Evolution ──
    pattern_changes = conn.execute("""
        SELECT sp.trc, sp.label, sp.tier, sp.lifetime_tickets,
               sp.discovered_at, sp.last_seen_at
        FROM sub_patterns sp
        WHERE sp.merged_into IS NULL
          AND sp.tier != 'retired'
        ORDER BY sp.last_seen_at DESC
        LIMIT 10
    """).fetchall()

    if pattern_changes:
        sections.append("## Active Sub-Patterns\n")
        sections.append("| TRC | Label | Tier | Lifetime | Discovered | Last Seen |")
        sections.append("|-----|-------|------|----------|------------|-----------|")
        for p in pattern_changes:
            disc = p[4][:10] if p[4] else "—"
            last = p[5][:10] if p[5] else "—"
            sections.append(
                f"| {p[0]} | {p[1]} | {p[2]} | {p[3]} | {disc} | {last} |"
            )
        sections.append("")

    return "\n".join(sections)


# ── Internal helpers ─────────────────────────────────────────────────

def _get_active_alerts(conn) -> list:
    """Get open watchlist alerts (PHI-free summary only)."""
    try:
        return conn.execute("""
            SELECT wr.name, wa.severity, wa.ticket_count, wa.trc_code
            FROM watchlist_alerts wa
            JOIN watchlist_rules wr ON wa.rule_id = wr.id
            WHERE wa.status = 'open'
            ORDER BY wa.created_at DESC
            LIMIT 10
        """).fetchall()
    except Exception:
        return []


def _get_guru_coverage_summary(conn) -> dict | None:
    """Aggregate guru friction coverage stats."""
    try:
        row = conn.execute("""
            SELECT COUNT(*) as total,
                   SUM(CASE WHEN coverage_score >= 0.5 THEN 1 ELSE 0 END) as covered,
                   SUM(CASE WHEN coverage_score < 0.5 THEN 1 ELSE 0 END) as gaps,
                   COALESCE(AVG(coverage_score), 0.0) as avg_score
            FROM guru_friction_coverage
        """).fetchone()
        if row and row[0] > 0:
            return {
                "total": row[0],
                "covered": row[1],
                "gaps": row[2],
                "avg_score": row[3],
            }
    except Exception:
        pass
    return None
