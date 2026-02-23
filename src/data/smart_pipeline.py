"""
Alma Insights — Smart Reporting Pipeline (Pass 3.0 / 4.1-CLI)
End-to-end pipeline: data pull → analytics → NLP scan → meta-analysis
→ AI report → save → Drive export.

CLI entry point:
    python -m src.data.smart_pipeline --export-drive

Exit codes:
    0 = success
    1 = no data
    2 = analytics failure
    3 = Gemini failure
    4 = export failure
"""

import sys
import json
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path

log = logging.getLogger(__name__)


def run_pipeline(config=None, progress_cb=None):
    """Run the full smart reporting pipeline.

    Args:
        config: dict with keys:
            - db_path: str (path to SQLite DB)
            - prompt_id: int or None (prompt_library ID; None = default)
            - lookback_days: int (default 30)
            - trc_filter: str or None
            - export_drive: bool (default False)
            - dataset_id: int or None
            - nlp_scan: bool (default False) — run NLP scan stage
            - nlp_batch_size: int (default 700)
            - nlp_budget_cap: float (default 50.0)
            - nlp_workers: int (default 1)
        progress_cb: callable(step, total, message) or None

    Returns dict:
        {
            "success": bool,
            "report_text": str,
            "report_id": int or None,
            "duration_ms": int,
            "steps_completed": int,
            "error": str or None,
            "nlp_scan_id": str or None,
        }
    """
    config = config or {}
    nlp_enabled = config.get("nlp_scan", False)
    total_steps = 12 if nlp_enabled else 10
    start_time = time.time()

    def _prog(step, msg):
        if progress_cb:
            progress_cb(step, total_steps, msg)

    db_path = config.get("db_path", "")
    if not db_path:
        # Default path
        db_path = str(Path(__file__).parent.parent.parent / "data" / "local_warehouse.db")

    lookback = config.get("lookback_days", 30)
    trc_filter = config.get("trc_filter")
    export_drive = config.get("export_drive", False)
    prompt_id = config.get("prompt_id")
    dataset_id = config.get("dataset_id")

    result = {
        "success": False,
        "report_text": "",
        "report_id": None,
        "nlp_scan_id": None,
        "duration_ms": 0,
        "steps_completed": 0,
        "error": None,
    }

    try:
        # ── Step 1: Initialize DB ──
        _prog(1, "Connecting to database...")
        from src.data.db_manager import DatabaseManager
        db = DatabaseManager(db_path)
        db.initialize()
        result["steps_completed"] = 1

        # ── Step 2: Determine date range ──
        _prog(2, "Determining date range...")
        date_end = _get_latest_date(db)
        if not date_end:
            result["error"] = "No data found in database"
            return result

        d_end = datetime.strptime(date_end, "%Y-%m-%d")
        d_start = d_end - timedelta(days=lookback)
        date_start = d_start.strftime("%Y-%m-%d")
        result["steps_completed"] = 2

        # ── Step 3: Populate count tables ──
        _prog(3, "Materializing count tables...")
        try:
            db.populate_daily_counts()
            db.populate_hourly_counts()
        except Exception:
            pass
        result["steps_completed"] = 3

        # ── Step 4: Run incident scan ──
        _prog(4, "Running incident scan...")
        try:
            from src.data.incident_engine import run_incident_scan
            run_incident_scan(db, target_date=date_end)
        except Exception as e:
            log.warning(f"Incident scan failed: {e}")
        result["steps_completed"] = 4

        # ── Step 5 (conditional): NLP Scan ──
        if nlp_enabled:
            _prog(5, "Running NLP scan (this may take a while)...")
            nlp_result = _run_nlp_scan(config, db_path, date_start, date_end,
                                       trc_filter, progress_cb=_prog)
            if nlp_result.get("error"):
                log.warning(f"NLP scan issue: {nlp_result['error']}")
            else:
                result["nlp_scan_id"] = nlp_result.get("scan_id")
            result["steps_completed"] = 5

            # ── Step 6 (conditional): NLP Meta-analysis ──
            _prog(6, "Running NLP meta-analysis...")
            scan_id = result.get("nlp_scan_id")
            if scan_id:
                try:
                    _run_meta_analysis(db, scan_id)
                except Exception as e:
                    log.warning(f"NLP meta-analysis failed: {e}")
            result["steps_completed"] = 6

        # ── Step N+1: Run TRC analytics (trending engine) ──
        step_offset = 2 if nlp_enabled else 0
        _prog(5 + step_offset, "Running TRC analytics...")
        # This is done as part of build_data_block
        result["steps_completed"] = 5 + step_offset

        # ── Step N+2: AI smoothing (if enabled) ──
        _prog(6 + step_offset, "Checking AI enhancements...")
        import yaml
        config_path = Path(__file__).parent.parent.parent / "config" / "settings.yaml"
        ai_smoothing = False
        try:
            if config_path.exists():
                with open(config_path, encoding="utf-8") as f:
                    cfg = yaml.safe_load(f) or {}
                ai_cfg = cfg.get("ai_enhancements", {})
                ai_smoothing = ai_cfg.get("smoothing", False)
        except Exception:
            pass
        result["steps_completed"] = 6 + step_offset

        # ── Step N+3: Build data block ──
        _prog(7 + step_offset, "Building data block...")
        from src.data.report_builder import (
            build_data_block, replace_prompt_variables, _validate_prompt_before_send,
            format_data_block_for_prompt,
        )
        data_block = build_data_block(
            db, date_start, date_end,
            trc_filter=trc_filter, dataset_id=dataset_id,
        )
        if data_block.get("ticket_count", 0) == 0:
            result["error"] = "No tickets found in date range"
            return result
        result["steps_completed"] = 7 + step_offset

        # ── Step N+4: Load prompt + generate report ──
        _prog(8 + step_offset, "Generating AI report...")

        # Load prompt
        prompt_text = ""
        if prompt_id:
            prompt_data = db.get_prompt(prompt_id)
            if prompt_data:
                prompt_text = prompt_data.get("prompt_text", "")
        if not prompt_text:
            # Default to general_trend
            try:
                general_path = Path(__file__).parent.parent.parent / "config" / "prompts" / "general_trend.txt"
                if general_path.exists():
                    prompt_text = general_path.read_text(encoding="utf-8")
            except Exception:
                prompt_text = "Analyze this ticket data:\n\n{data_block}"

        # Replace variables
        filled_prompt = replace_prompt_variables(prompt_text, data_block)
        _validate_prompt_before_send(filled_prompt)

        # Build Gemini client and generate
        gemini_client = _build_gemini_client()
        if not gemini_client:
            result["error"] = "Gemini not configured"
            return result

        report_text = gemini_client.generate(filled_prompt)
        if not report_text:
            result["error"] = "Empty response from Gemini"
            return result

        result["report_text"] = report_text
        result["steps_completed"] = 8 + step_offset

        # ── Step N+5: Save report ──
        _prog(9 + step_offset, "Saving report...")
        report_id = db.save_report(
            page="ai_reports",
            parameters={
                "date_start": date_start,
                "date_end": date_end,
                "trc_filter": trc_filter or "",
                "prompt_id": prompt_id,
                "pipeline": "smart",
            },
            summary={"ticket_count": data_block["ticket_count"]},
            full_results=report_text,
            ticket_count=data_block["ticket_count"],
            duration_ms=int((time.time() - start_time) * 1000),
            report_type="smart",
        )
        result["report_id"] = report_id
        result["steps_completed"] = 9 + step_offset

        # Record smart run
        run_id = db.start_smart_run({
            "trigger_source": "pipeline",
            "ticket_count": data_block["ticket_count"],
            "config_snapshot": json.dumps(config, default=str),
        })

        # ── Step N+6: Export to Drive (if enabled) ──
        _prog(10 + step_offset, "Finishing up...")
        if export_drive:
            try:
                _export_to_drive(report_text, date_start, date_end, config_path)
                if report_id:
                    db.update_report_exported(report_id)
            except Exception as e:
                log.warning(f"Drive export failed: {e}")

        if run_id:
            db.complete_smart_run(run_id, {
                "report_id": report_id,
                "ticket_count": data_block["ticket_count"],
            })

        result["steps_completed"] = 10 + step_offset
        result["success"] = True
        result["duration_ms"] = int((time.time() - start_time) * 1000)

        db.close()
        return result

    except Exception as e:
        import traceback
        result["error"] = f"{e}\n{traceback.format_exc()}"
        result["duration_ms"] = int((time.time() - start_time) * 1000)
        return result


def _run_nlp_scan(config, db_path, date_start, date_end,
                  trc_filter, progress_cb=None):
    """Launch NLP scan via ScanOrchestrator (agentic pipeline 5.0).

    Workers run as persistent bridge-backed agents. We poll the
    nlp_scan_runs table every 10 seconds until status leaves 'running',
    or a hard 4-hour timeout.

    Falls back to ScanWorkerManager if agentic pipeline fails to import.
    """
    import sqlite3

    try:
        from src.agents.scan_orchestrator import ScanOrchestrator
        mgr = ScanOrchestrator(db_path=db_path)

        trc_list = None
        if trc_filter:
            trc_list = [trc_filter] if isinstance(trc_filter, str) else trc_filter

        result = mgr.start_scan(
            date_start=date_start,
            date_end=date_end,
            trc_filter=trc_list,
            batch_size=config.get("nlp_batch_size", 700),
            budget_cap=config.get("nlp_budget_cap", 50.0),
            parallel_workers=config.get("nlp_workers", 1),
            mode="full",
        )

        if "error" in result:
            return result

        scan_id = result["scan_id"]
        log.info(f"NLP scan started: {scan_id[:8]}... "
                 f"({result['total_batches']} batches, "
                 f"{result['total_tickets']:,} tickets)")

        # Poll SQLite until scan finishes (max 4 hours)
        max_polls = 4 * 60 * 6  # 4 hours at 10s intervals
        for i in range(max_polls):
            time.sleep(10)
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT status, completed_batches, total_batches "
                "FROM nlp_scan_runs WHERE scan_id = ?",
                (scan_id,)
            ).fetchone()
            conn.close()

            if not row:
                return {"error": "scan record disappeared", "scan_id": scan_id}

            status = row["status"]
            completed = row["completed_batches"] or 0
            total = row["total_batches"] or 1

            if progress_cb:
                pct = int(completed / total * 100)
                progress_cb(5, 12, f"NLP scan: {completed}/{total} batches ({pct}%)")

            if status in ("scan_complete", "analysis_complete",
                          "completed", "failed", "cancelled"):
                if status in ("failed", "cancelled"):
                    return {"error": f"scan ended with status: {status}",
                            "scan_id": scan_id}
                return {"scan_id": scan_id, "status": status}

        return {"error": "NLP scan timed out after 4 hours", "scan_id": scan_id}

    except Exception as e:
        log.error(f"NLP scan failed: {e}")
        return {"error": str(e)}


def _run_meta_analysis(db, scan_id):
    """Run NLP meta-analysis for a completed scan."""
    from src.data.nlp_meta_analyzer import NLPMetaAnalyzer
    analyzer = NLPMetaAnalyzer(db)
    analyzer.run_analysis(scan_id)
    log.info(f"NLP meta-analysis completed for scan {scan_id[:8]}...")


def _get_latest_date(db):
    """Get the latest date with data."""
    row = db.conn.execute(
        "SELECT MAX(created_at) as max_d FROM conversations"
    ).fetchone()
    if row and row["max_d"]:
        return row["max_d"][:10]
    return None


def _build_gemini_client():
    """Build a GeminiClient from settings."""
    import yaml
    from pathlib import Path

    config_path = Path(__file__).parent.parent.parent / "config" / "settings.yaml"
    try:
        if config_path.exists():
            with open(config_path, encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
        else:
            return None

        gemini_cfg = cfg.get("gemini", {})
        cli_path = gemini_cfg.get("cli_path", "")
        model = gemini_cfg.get("model", "gemini-2.5-flash")
        pii = gemini_cfg.get("pii_redaction", True)

        from src.data.pat_store import load_setting
        api_key = load_setting("gemini_api_key", "")

        from src.gemini.gemini_client import GeminiClient
        client = GeminiClient(cli_path=cli_path, model=model, aggressive_pii=pii)
        if api_key:
            client._api_key = api_key
        return client
    except Exception:
        return None


def _export_to_drive(report_text, date_start, date_end, config_path):
    """Upload report to Google Drive if configured."""
    import yaml

    try:
        with open(config_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
    except Exception:
        return

    gdrive = cfg.get("export", {}).get("google_drive", {})
    if not gdrive.get("enabled"):
        return

    from src.export.gdrive_export import GoogleDriveExporter
    exporter = GoogleDriveExporter(
        credentials_path=gdrive.get("credentials_path", ""),
        folder_id=gdrive.get("folder_id", ""),
    )
    if not exporter.is_configured():
        return

    filename = f"alma_report_{date_start}_to_{date_end}.md"
    exporter.upload_report(filename, report_text)


# ── CLI entry point ──

def main():
    """CLI entry point for smart pipeline."""
    import argparse

    parser = argparse.ArgumentParser(description="Alma Insights Smart Pipeline")
    parser.add_argument("--db-path", default="", help="Path to SQLite database")
    parser.add_argument("--lookback", type=int, default=30, help="Lookback days")
    parser.add_argument("--trc-filter", default="", help="TRC code filter")
    parser.add_argument("--export-drive", action="store_true", help="Export to Google Drive")
    parser.add_argument("--prompt-id", type=int, default=None, help="Prompt library ID")
    parser.add_argument("--nlp-scan", action="store_true", help="Run NLP scan stage")
    parser.add_argument("--nlp-batch-size", type=int, default=700, help="NLP batch size")
    parser.add_argument("--nlp-budget", type=float, default=50.0, help="NLP budget cap USD")
    parser.add_argument("--nlp-workers", type=int, default=1, help="NLP parallel workers (1-3)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    config = {
        "db_path": args.db_path,
        "lookback_days": args.lookback,
        "trc_filter": args.trc_filter or None,
        "export_drive": args.export_drive,
        "prompt_id": args.prompt_id,
        "nlp_scan": args.nlp_scan,
        "nlp_batch_size": args.nlp_batch_size,
        "nlp_budget_cap": args.nlp_budget,
        "nlp_workers": args.nlp_workers,
    }

    def progress(step, total, msg):
        print(f"[{step}/{total}] {msg}")

    result = run_pipeline(config, progress_cb=progress)

    if result["success"]:
        print(f"\nPipeline completed in {result['duration_ms']}ms")
        print(f"Report ID: {result['report_id']}")
        total = 12 if config.get("nlp_scan") else 10
        print(f"Steps completed: {result['steps_completed']}/{total}")
        sys.exit(0)
    else:
        print(f"\nPipeline failed at step {result['steps_completed']}: {result['error']}")
        # Map failure to exit code
        step = result["steps_completed"]
        if step <= 2:
            sys.exit(1)  # No data
        elif step <= 6:
            sys.exit(2)  # Analytics failure
        elif step <= 8:
            sys.exit(3)  # Gemini failure
        else:
            sys.exit(4)  # Export failure


if __name__ == "__main__":
    main()
