"""
Alma Insights — Post-Scan Auto-Report Builder

Assembles a structured markdown report from DB data after an NLP scan
completes.  Pure Python — no LLM calls.  Designed to run in the
orchestrator's background thread with its own sqlite3 connection.

Usage (from scan_orchestrator after _finalize_scan):
    builder = ScanReportBuilder(db_path)
    builder.build_and_save(scan_id)
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path

from src.data.connection_factory import get_connection

logger = logging.getLogger("alma.scan_report_builder")


class ScanReportBuilder:
    """Builds and persists a post-scan summary report from DB data."""

    def __init__(self, db_path: str):
        self._db_path = str(Path(db_path).resolve())

    # ── Public API ───────────────────────────────────────────────────

    def build_and_save(self, scan_id: str) -> None:
        """Build report markdown and persist to analysis_reports."""
        conn = get_connection(self._db_path)
        try:
            scan = self._get_scan(conn, scan_id)
            if not scan:
                logger.warning("No scan found for %s — skipping report", scan_id[:8])
                return

            markdown = self._assemble_markdown(conn, scan, scan_id)
            summary = self._build_summary_json(conn, scan, scan_id)

            # Compute duration
            duration_ms = 0
            try:
                created = datetime.fromisoformat(scan["created_at"])
                completed = datetime.fromisoformat(scan["completed_at"])
                duration_ms = int((completed - created).total_seconds() * 1000)
            except (TypeError, ValueError):
                pass

            ticket_count = scan.get("total_tickets") or 0

            # Persist via raw INSERT (matching db_manager.save_report schema)
            conn.execute("""
                INSERT INTO analysis_reports
                    (page, run_at, parameters, summary, full_results,
                     ticket_count, duration_ms, notes, report_type, chat_history)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                "nlp_scanner",
                datetime.now().isoformat(),
                json.dumps({"scan_id": scan_id}),
                json.dumps(summary),
                markdown,
                ticket_count,
                duration_ms,
                "",
                "auto_scan_report",
                "",
            ))
            conn.commit()
            logger.info("Scan %s: auto-report saved (%d chars)",
                        scan_id[:8], len(markdown))
        finally:
            conn.close()

    # ── Data Access ──────────────────────────────────────────────────

    @staticmethod
    def _get_scan(conn, scan_id):
        row = conn.execute(
            "SELECT * FROM nlp_scan_runs WHERE scan_id = ?", (scan_id,)
        ).fetchone()
        return dict(row) if row else None

    @staticmethod
    def _get_batch_stats(conn, scan_id):
        """Return per-status batch counts, total retries, latency stats."""
        rows = conn.execute("""
            SELECT status,
                   COUNT(*)              AS cnt,
                   COALESCE(SUM(retry_count), 0) AS retries,
                   COALESCE(AVG(latency_ms), 0)  AS avg_latency,
                   COALESCE(MAX(latency_ms), 0)  AS max_latency
            FROM nlp_batches WHERE scan_id = ?
            GROUP BY status
        """, (scan_id,)).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def _get_error_breakdown(conn, scan_id):
        rows = conn.execute("""
            SELECT error_message, COUNT(*) AS cnt
            FROM nlp_batches
            WHERE scan_id = ? AND error_message IS NOT NULL
            GROUP BY error_message
            ORDER BY cnt DESC
            LIMIT 10
        """, (scan_id,)).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def _get_findings(conn, scan_id, limit=10):
        rows = conn.execute("""
            SELECT * FROM nlp_findings
            WHERE scan_id = ?
            ORDER BY impact_score DESC
            LIMIT ?
        """, (scan_id, limit)).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def _get_ledger(conn, scan_id, limit=50):
        rows = conn.execute("""
            SELECT trc, sub_cluster, friction_type, root_cause_hint,
                   COUNT(*) AS frequency
            FROM nlp_ticket_classifications
            WHERE scan_id = ?
            GROUP BY trc, sub_cluster, friction_type, root_cause_hint
            ORDER BY frequency DESC
            LIMIT ?
        """, (scan_id, limit)).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def _get_previous_scan(conn, current_scan_id):
        row = conn.execute("""
            SELECT scan_id FROM nlp_scan_runs
            WHERE status IN ('completed', 'completed_with_errors')
              AND scan_id != ?
            ORDER BY created_at DESC LIMIT 1
        """, (current_scan_id,)).fetchone()
        return dict(row) if row else None

    @staticmethod
    def _get_trc_counts(conn, scan_id):
        """TRC → ticket count for a scan."""
        rows = conn.execute("""
            SELECT trc, COUNT(*) AS cnt
            FROM nlp_ticket_classifications
            WHERE scan_id = ?
            GROUP BY trc
            ORDER BY cnt DESC
        """, (scan_id,)).fetchall()
        return {r["trc"]: r["cnt"] for r in rows}

    # ── Markdown Assembly ────────────────────────────────────────────

    def _assemble_markdown(self, conn, scan, scan_id):
        parts = []

        # Header
        status = scan.get("status", "unknown")
        date_start = scan.get("date_range_start", "?")
        date_end = scan.get("date_range_end", "?")
        completed = scan.get("completed_at", "N/A")
        total_tickets = scan.get("total_tickets") or 0

        parts.append(f"# Scan Report — `{scan_id[:12]}`\n")
        parts.append(f"**Status**: {status}  ")
        parts.append(f"**Completed**: {completed}  ")
        parts.append(f"**Date Range**: {date_start} → {date_end}  ")
        parts.append(f"**Total Tickets**: {total_tickets:,}\n")

        # Sections
        parts.append(self._build_cost_section(scan))
        parts.append(self._build_diagnostics_section(conn, scan, scan_id))
        parts.append(self._build_bridge_section(conn, scan_id))
        parts.append(self._build_findings_section(conn, scan_id))
        parts.append(self._build_ledger_section(conn, scan_id))
        parts.append(self._build_trend_section(conn, scan_id))

        return "\n".join(parts)

    # ── Section Builders ─────────────────────────────────────────────

    @staticmethod
    def _build_cost_section(scan):
        tokens_in = scan.get("total_input_tokens") or 0
        tokens_out = scan.get("total_output_tokens") or 0
        cost = scan.get("actual_cost_usd") or 0.0
        budget = scan.get("budget_cap_usd") or 0.0
        total_tickets = scan.get("total_tickets") or 0
        cost_per_ticket = (cost / total_tickets) if total_tickets > 0 else 0.0

        lines = [
            "## Cost Breakdown\n",
            "| Metric | Value |",
            "|--------|-------|",
            f"| Input Tokens | {tokens_in:,} |",
            f"| Output Tokens | {tokens_out:,} |",
            f"| Total Tokens | {tokens_in + tokens_out:,} |",
            f"| Total Cost | ${cost:.4f} |",
            f"| Cost / Ticket | ${cost_per_ticket:.5f} |",
        ]
        if budget > 0:
            pct = (cost / budget) * 100 if budget else 0
            lines.append(f"| Budget Utilization | {pct:.1f}% of ${budget:.2f} |")
        lines.append("")
        return "\n".join(lines)

    def _build_diagnostics_section(self, conn, scan, scan_id):
        """Diagnostics section surfacing telemetry from D2-D7."""
        lines = ["## Scan Diagnostics\n"]
        lines.append(self._diag_model(scan))
        lines.append(self._diag_probe_latency(conn, scan_id))
        lines.append(self._diag_latency_trend(conn, scan_id))
        lines.append(self._diag_retry_summary(conn, scan_id))
        lines.append(self._diag_worker_health(conn, scan_id))
        lines.append(self._diag_bridge_events(conn, scan_id))
        lines.append(self._diag_rate_governor(conn, scan_id))
        return "\n".join(s for s in lines if s)

    def _diag_model(self, scan):
        try:
            cfg = json.loads(scan.get("config_snapshot") or "{}")
            model = cfg.get("model", "unknown")
        except Exception:
            model = "unknown"
        return f"**Model**: `{model}`\n"

    def _diag_probe_latency(self, conn, scan_id):
        try:
            probes = conn.execute("""
                SELECT latency_ms FROM probe_history
                WHERE scan_id = ? AND status = 'success'
                ORDER BY latency_ms
            """, (scan_id,)).fetchall()
            if probes:
                lats = [p["latency_ms"] for p in probes]
                p50 = lats[len(lats) // 2]
                p95 = lats[int(len(lats) * 0.95)] if len(lats) > 1 else lats[-1]
                return f"**Probe Latency**: P50={p50}ms, P95={p95}ms ({len(lats)} probes)\n"
        except Exception:
            pass
        return ""

    def _diag_latency_trend(self, conn, scan_id):
        try:
            batches = conn.execute("""
                SELECT batch_number, latency_ms FROM nlp_batches
                WHERE scan_id = ? AND status = 'completed' AND latency_ms > 0
                ORDER BY batch_number
            """, (scan_id,)).fetchall()
            if len(batches) >= 4:
                mid = len(batches) // 2
                first_half = [b["latency_ms"] for b in batches[:mid]]
                second_half = [b["latency_ms"] for b in batches[mid:]]
                avg_first = sum(first_half) / len(first_half)
                avg_second = sum(second_half) / len(second_half)
                ratio = avg_second / avg_first if avg_first > 0 else 0
                trend = "stable"
                if ratio > 1.5:
                    trend = f"degrading ({ratio:.1f}x slower)"
                elif ratio < 0.7:
                    trend = f"improving ({ratio:.1f}x faster)"
                return (
                    f"**Latency Trend**: first-half avg {avg_first/1000:.1f}s, "
                    f"second-half avg {avg_second/1000:.1f}s -- {trend}\n"
                )
        except Exception:
            pass
        return ""

    def _diag_retry_summary(self, conn, scan_id):
        try:
            retries = conn.execute("""
                SELECT COUNT(*) as cnt FROM scan_events
                WHERE scan_id = ? AND event_type = 'batch_retry'
            """, (scan_id,)).fetchone()
            retry_cnt = retries["cnt"] if retries else 0
            if retry_cnt > 0:
                retry_events = conn.execute("""
                    SELECT metadata_json FROM scan_events
                    WHERE scan_id = ? AND event_type = 'batch_retry'
                """, (scan_id,)).fetchall()
                error_counts = {}
                for ev in retry_events:
                    try:
                        md = json.loads(ev["metadata_json"] or "{}")
                        err = md.get("error_code", md.get("error", "unknown"))
                        error_counts[err] = error_counts.get(err, 0) + 1
                    except Exception:
                        pass
                breakdown = ", ".join(f"{e}: {c}" for e, c in error_counts.items())
                return f"**Retries**: {retry_cnt} total ({breakdown})\n"
            else:
                return "**Retries**: 0\n"
        except Exception:
            pass
        return ""

    def _diag_worker_health(self, conn, scan_id):
        try:
            workers = conn.execute("""
                SELECT worker_id,
                       COUNT(*) as total,
                       SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) as ok,
                       SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) as fail,
                       SUM(COALESCE(retry_count, 0)) as retries
                FROM nlp_batches WHERE scan_id = ?
                GROUP BY worker_id
            """, (scan_id,)).fetchall()
            if workers:
                lines = ["### Worker Health\n"]
                lines.append("| Worker | Batches | OK | Failed | Retries |")
                lines.append("|--------|---------|----|----|---------|")
                for w in workers:
                    wid = str(w["worker_id"] or "unassigned")
                    lines.append(
                        f"| {wid} | {w['total']} | {w['ok']} | "
                        f"{w['fail']} | {w['retries']} |"
                    )
                lines.append("")
                return "\n".join(lines)
        except Exception:
            pass
        return ""

    def _diag_bridge_events(self, conn, scan_id):
        try:
            bridge_events = conn.execute("""
                SELECT timestamp, message, metadata_json FROM scan_events
                WHERE scan_id = ? AND (
                    message LIKE '%bridge died%' OR
                    message LIKE '%Stall escalation%' OR
                    message LIKE '%bridge dead%'
                )
                ORDER BY timestamp
            """, (scan_id,)).fetchall()
            if bridge_events:
                lines = ["### Bridge Events\n"]
                for ev in bridge_events:
                    ts = (ev["timestamp"] or "?")[11:19]
                    lines.append(f"- `{ts}` {ev['message'][:100]}")
                lines.append("")
                return "\n".join(lines)
        except Exception:
            pass
        return ""

    def _diag_rate_governor(self, conn, scan_id):
        try:
            rg_event = conn.execute("""
                SELECT metadata_json FROM scan_events
                WHERE scan_id = ? AND message LIKE '%Rate governor final%'
                ORDER BY timestamp DESC LIMIT 1
            """, (scan_id,)).fetchone()
            if rg_event and rg_event["metadata_json"]:
                md = json.loads(rg_event["metadata_json"])
                return (
                    f"**Rate Governor**: interval={md.get('min_interval', '?')}s, "
                    f"throughput={md.get('throughput', '?')}/min, "
                    f"floor={md.get('probe_floor', '?')}s, "
                    f"calls={md.get('total_calls', '?')}\n"
                )
        except Exception:
            pass
        return ""

    def _build_bridge_section(self, conn, scan_id):
        stats = self._get_batch_stats(conn, scan_id)
        errors = self._get_error_breakdown(conn, scan_id)

        completed = 0
        failed = 0
        total_retries = 0
        avg_latency = 0.0
        max_latency = 0.0
        total_batches = 0

        for s in stats:
            cnt = s["cnt"]
            total_batches += cnt
            if s["status"] == "completed":
                completed = cnt
                avg_latency = s["avg_latency"]
                max_latency = s["max_latency"]
            elif s["status"] == "failed":
                failed = cnt
            total_retries += s["retries"]

        lines = [
            "## Bridge Status Report\n",
            f"**{completed}/{total_batches}** batches succeeded",
        ]
        if failed > 0:
            lines[-1] += f", **{failed} failed**"
        if total_retries > 0:
            lines[-1] += f", {total_retries} total retries"
        lines[-1] += "\n"

        if avg_latency > 0:
            lines.append(
                f"Avg latency: **{avg_latency / 1000:.1f}s** · "
                f"Max: **{max_latency / 1000:.1f}s**\n"
            )

        if errors:
            lines.append("### Error Breakdown\n")
            lines.append("| Error Type | Count |")
            lines.append("|------------|-------|")
            for e in errors:
                msg = (e["error_message"] or "unknown")[:60]
                lines.append(f"| {msg} | {e['cnt']} |")
            lines.append("")
        elif failed == 0:
            lines.append("All batches completed successfully.\n")

        return "\n".join(lines)

    def _build_findings_section(self, conn, scan_id):
        findings = self._get_findings(conn, scan_id, limit=10)

        lines = ["## Key Findings\n"]
        if not findings:
            lines.append("No findings generated for this scan.\n")
            return "\n".join(lines)

        for i, f in enumerate(findings, 1):
            title = f.get("title", "Untitled")
            tickets = f.get("ticket_count") or 0
            friction = f.get("dominant_friction_type", "—")
            score = f.get("impact_score") or 0
            trend = f.get("temporal_trend", "")

            trend_icon = ""
            if trend == "new":
                trend_icon = " 🆕"
            elif trend == "rising":
                trend_icon = " ↑"
            elif trend == "declining":
                trend_icon = " ↓"

            lines.append(
                f"{i}. **{title}** — {tickets:,} tickets · "
                f"*{friction}* · impact {score:.0f}{trend_icon}"
            )

        lines.append("")
        return "\n".join(lines)

    def _build_ledger_section(self, conn, scan_id):
        ledger = self._get_ledger(conn, scan_id, limit=50)

        lines = ["## Friction Ledger\n"]
        if not ledger:
            lines.append("No classification data available.\n")
            return "\n".join(lines)

        lines.append("| TRC | Sub-Cluster | Freq | Friction Type | Root Cause |")
        lines.append("|-----|-------------|------|---------------|------------|")
        for row in ledger:
            trc = (row.get("trc") or "—")[:40]
            cluster = (row.get("sub_cluster") or "—")[:40]
            freq = row.get("frequency") or 0
            friction = (row.get("friction_type") or "—")[:25]
            root = (row.get("root_cause_hint") or "—")[:40]
            lines.append(f"| {trc} | {cluster} | {freq} | {friction} | {root} |")

        lines.append("")
        return "\n".join(lines)

    def _build_trend_section(self, conn, scan_id):
        prev = self._get_previous_scan(conn, scan_id)
        lines = ["## Trend Summary\n"]

        if not prev:
            lines.append("*First scan — no previous data for comparison.*\n")
            return "\n".join(lines)

        prev_id = prev["scan_id"]
        current_counts = self._get_trc_counts(conn, scan_id)
        prev_counts = self._get_trc_counts(conn, prev_id)

        # Union of all TRCs
        all_trcs = sorted(
            set(current_counts.keys()) | set(prev_counts.keys()),
            key=lambda t: current_counts.get(t, 0),
            reverse=True,
        )

        if not all_trcs:
            lines.append("No TRC-level data to compare.\n")
            return "\n".join(lines)

        lines.append("| TRC | This Scan | Previous | Δ | Direction |")
        lines.append("|-----|-----------|----------|---|-----------|")
        for trc in all_trcs[:25]:
            curr = current_counts.get(trc, 0)
            prev_n = prev_counts.get(trc, 0)
            delta = curr - prev_n
            if delta > 0:
                direction = f"↑ +{delta}"
            elif delta < 0:
                direction = f"↓ {delta}"
            else:
                direction = "—"
            trc_short = trc[:45] if trc else "—"
            lines.append(
                f"| {trc_short} | {curr} | {prev_n} | {delta:+d} | {direction} |"
            )

        lines.append("")
        return "\n".join(lines)

    # ── Summary JSON (for KPI cards) ─────────────────────────────────

    def _build_summary_json(self, conn, scan, scan_id):
        stats = self._get_batch_stats(conn, scan_id)
        findings = self._get_findings(conn, scan_id, limit=1)
        trc_counts = self._get_trc_counts(conn, scan_id)

        completed = sum(s["cnt"] for s in stats if s["status"] == "completed")
        failed = sum(s["cnt"] for s in stats if s["status"] == "failed")
        total_retries = sum(s["retries"] for s in stats)

        # Latency stats from completed batches
        avg_latency = 0.0
        max_latency = 0.0
        for s in stats:
            if s["status"] == "completed":
                avg_latency = s["avg_latency"]
                max_latency = s["max_latency"]

        # Model from config
        model = None
        try:
            cfg = json.loads(scan.get("config_snapshot") or "{}")
            model = cfg.get("model")
        except Exception:
            pass

        # Probe P50/P95
        probe_p50 = None
        probe_p95 = None
        try:
            probes = conn.execute("""
                SELECT latency_ms FROM probe_history
                WHERE scan_id = ? AND status = 'success'
                ORDER BY latency_ms
            """, (scan_id,)).fetchall()
            if probes:
                lats = [p["latency_ms"] for p in probes]
                probe_p50 = lats[len(lats) // 2]
                probe_p95 = lats[int(len(lats) * 0.95)] if len(lats) > 1 else lats[-1]
        except Exception:
            pass

        return {
            "total_tickets": scan.get("total_tickets") or 0,
            "total_cost_usd": scan.get("actual_cost_usd") or 0.0,
            "batches_succeeded": completed,
            "batches_failed": failed,
            "finding_count": len(self._get_findings(conn, scan_id, limit=100)),
            "top_finding": findings[0]["title"] if findings else None,
            "trc_count": len(trc_counts),
            "model": model,
            "total_retries": total_retries,
            "avg_latency_ms": avg_latency,
            "max_latency_ms": max_latency,
            "probe_p50_ms": probe_p50,
            "probe_p95_ms": probe_p95,
        }
