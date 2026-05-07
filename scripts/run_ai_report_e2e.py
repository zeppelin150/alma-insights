"""In-app AI Reports E2E runner (2026-05-06).

Fires the full structured-output pipeline against the live warehouse,
persists the result to `analysis_reports` (with all new R1-R5 columns
populated), and dumps machine + human artifacts under
``tools/audit_reports/``. Use `--stub` for a fast wiring smoke (no real
LLM call); omit to run live through the configured bridge.

Usage::

    # Fast wiring smoke (~2s, stub bridge, NOT persisted to DB):
    python scripts/run_ai_report_e2e.py --stub

    # Live run, single_pass (~30-90s):
    python scripts/run_ai_report_e2e.py --kind single_pass

    # Live run, multi_bridge (default, ~15-30 min):
    python scripts/run_ai_report_e2e.py

    # Pick a different prompt:
    python scripts/run_ai_report_e2e.py --prompt "General Trend Analysis"

Outputs:
    tools/audit_reports/e2e_<run_id>.md     — readable Report markdown
    tools/audit_reports/e2e_<run_id>.json   — full Report.to_dict() payload
    + a row in analysis_reports             — view via Report History tab
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

# Repo root on path so `python scripts/run_ai_report_e2e.py` works
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.data.ai_report_pipeline import AIReportPipeline  # noqa: E402
from src.data.report_schema import Report  # noqa: E402


_AUDIT_DIR = _ROOT / "tools" / "audit_reports"


# ──────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────

def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="AI Reports E2E runner")
    p.add_argument("--prompt", default="Executive Summary",
                    help="Canned prompt name from prompt_library.")
    p.add_argument("--kind", choices=("multi_bridge", "single_pass"),
                    default=None, help="Override ai.report_pipeline.kind.")
    p.add_argument("--date-start", default=None,
                    help="YYYY-MM-DD; defaults to min(ticket_created_date).")
    p.add_argument("--date-end", default=None,
                    help="YYYY-MM-DD; defaults to today.")
    p.add_argument("--trc-filter", default=None,
                    help="Optional TRC code/label substring.")
    p.add_argument("--stub", action="store_true",
                    help="Use stub bridge (no real LLM, not persisted).")
    p.add_argument("--no-persist", action="store_true",
                    help="Skip writing to analysis_reports.")
    p.add_argument("--model", default=None,
                    help="Model override (e.g. gemini-2.5-flash, "
                         "gemini-3-flash-preview). Bypasses settings.yaml.")
    p.add_argument("--timeout", type=int, default=240,
                    help="Per-LLM-call timeout in seconds (default 240).")
    return p.parse_args(argv)


# ──────────────────────────────────────────────────────────────────────
# Pre-flight + helpers
# ──────────────────────────────────────────────────────────────────────

def _preflight() -> None:
    """Verify warehouse is populated; create audit dir."""
    _AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    from src.data.db_manager import DB_PATH
    import sqlite3
    conn = sqlite3.connect(str(DB_PATH))
    n = conn.execute("SELECT COUNT(*) FROM ticket_index").fetchone()[0]
    conn.close()
    if n == 0:
        raise SystemExit(
            "Pre-flight failed: ticket_index is empty. Import data first."
        )
    print(f"[preflight] ticket_index rows: {n}")


def _resolve_date_range(args, db) -> tuple[str, str]:
    """Pick sane defaults from ticket_index when caller didn't specify."""
    if args.date_start and args.date_end:
        return args.date_start, args.date_end
    row = db.conn.execute(
        "SELECT MIN(ticket_created_date) AS lo, MAX(ticket_created_date) AS hi "
        "FROM ticket_index WHERE ticket_created_date IS NOT NULL"
    ).fetchone()
    lo = (args.date_start or (row["lo"] or "")[:10] or "2026-01-01")
    hi = (args.date_end or (row["hi"] or "")[:10]
           or datetime.now().strftime("%Y-%m-%d"))
    return lo, hi


def _load_prompt(db, name: str) -> dict:
    """Pull a canned/custom prompt by name from prompt_library."""
    rows = db.conn.execute(
        "SELECT name, description, prompt_text, system_prompt "
        "FROM prompt_library WHERE name = ? AND is_active = 1 LIMIT 1",
        (name,),
    ).fetchone()
    if not rows:
        raise SystemExit(f"Prompt not found in prompt_library: {name!r}")
    return dict(rows)


def _build_client(stub: bool, model: str | None = None, timeout: int = 240):
    """Return a (client, label) tuple. Stub mode uses canned output.

    For live mode we prefer ReportBridgeClient (persistent ACP bridge --
    eliminates the 17-20s CLI cold-start every call). The legacy
    GeminiClient subprocess path was hitting 120s timeouts on
    gemini-2.5-flash-lite (memory bridge_v5_rewrite.md flagged lite as
    broken). Caller can override the model via --model.
    """
    if stub:
        from tests.conftest_stub_bridge import StubReportBridgeClient
        return StubReportBridgeClient(), "stub"
    from src.agents.report_bridge_client import ReportBridgeClient
    from src.data.settings_manager import get_section
    chosen = (
        model
        or (get_section("ai", {}).get("report_pipeline") or {}).get("model")
        or get_section("ai", {}).get("active_model")
        or "gemini-2.5-flash"
    )
    # gemini-2.5-flash-lite is known broken -- fall back to flash if user picked lite.
    if "lite" in chosen:
        chosen = "gemini-2.5-flash"
    client = ReportBridgeClient(model=chosen, pii_redaction=True)
    if not client.is_available():
        raise SystemExit("Gemini CLI not found -- check Settings -> AI Provider.")
    return client, f"live[{chosen}]"


def _override_kind(kind: str | None) -> None:
    """If --kind given, monkey-patch the pipeline's settings reader."""
    if not kind:
        return
    import src.data.ai_report_pipeline as pipe
    base_get = pipe._get_pipeline_params

    def _override():
        params = base_get()
        params["kind"] = kind
        return params
    pipe._get_pipeline_params = _override
    print(f"[runner] pipeline kind override = {kind}")


# ──────────────────────────────────────────────────────────────────────
# Persistence + artifacts
# ──────────────────────────────────────────────────────────────────────

def _persist_report(db, prompt_name: str, report: Report, full_md: str,
                    date_start: str, date_end: str, trc_filter: str | None,
                    duration_ms: int) -> None:
    parameters = {
        "prompt": prompt_name,
        "date_from": date_start, "date_to": date_end,
        "trc_filter": trc_filter or "all",
        "pipeline_kind": report.pipeline_kind,
        "bridges_used": report.bridges_used,
        "report_run_id": report.report_id,
    }
    full_results = json.dumps({
        "report_text": full_md,
        "report_struct": report.to_dict(),
    })
    db.save_report(
        page="ai_reports",
        parameters=parameters,
        summary=(report.executive_summary or full_md[:500])[:500],
        full_results=full_results,
        ticket_count=int(report.scope.get("ticket_count") or 0),
        duration_ms=duration_ms,
        notes=f"E2E runner; flags={','.join(report.accuracy_flags) or 'none'}",
        report_type="standard",
        chat_history="",
        findings_json=report.to_json(),
        pipeline_kind=report.pipeline_kind,
        specialist_count=report.specialist_count,
        accuracy_score=report.accuracy_score,
        cost_usd=report.cost_usd,
    )
    print(f"[persist] analysis_reports row written; report_id={report.report_id}")


def _dump_artifacts(report: Report, full_md: str) -> tuple[Path, Path]:
    md_path = _AUDIT_DIR / f"e2e_{report.report_id}.md"
    json_path = _AUDIT_DIR / f"e2e_{report.report_id}.json"
    md_path.write_text(full_md, encoding="utf-8")
    json_path.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
    return md_path, json_path


# ──────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    _preflight()
    _override_kind(args.kind)

    from src.data.db_manager import DatabaseManager, DB_PATH
    db = DatabaseManager(DB_PATH)
    db.initialize()

    date_start, date_end = _resolve_date_range(args, db)
    prompt_data = _load_prompt(db, args.prompt)
    # Thread the runner's --timeout into the pipeline _call_client step
    prompt_data["timeout"] = args.timeout
    client, label = _build_client(args.stub, model=args.model, timeout=args.timeout)
    print(f"[runner] prompt={args.prompt!r} | bridge={label} | "
          f"window={date_start}/{date_end} | trc={args.trc_filter or 'all'} | "
          f"timeout={args.timeout}s")

    pipeline = AIReportPipeline(
        db, gemini_client=client,
        progress_cb=lambda msg: print(f"[pipeline] {msg}"),
    )
    t0 = time.time()
    result = pipeline.run(
        prompt_data=prompt_data,
        date_start=date_start, date_end=date_end,
        trc_filter=args.trc_filter,
    )
    duration_ms = int((time.time() - t0) * 1000)
    report: Report = result["report"]
    full_md = result["report_md"] or ""

    print()
    print("=" * 70)
    print(f"  Report:        {report.title}")
    print(f"  Run ID:        {report.report_id}")
    print(f"  Pipeline:      {report.pipeline_kind} ({report.bridges_used} bridge(s),"
          f" {report.specialist_count} specialist(s))")
    print(f"  Findings:      {len(report.findings)}")
    print(f"  Accuracy:      "
          f"{(report.accuracy_score * 100):.1f}%" if report.accuracy_score is not None
          else "  Accuracy:      n/a")
    print(f"  Cost (USD):    ${report.cost_usd:.4f}")
    print(f"  Duration:      {duration_ms / 1000:.1f}s")
    print(f"  Flags:         {', '.join(report.accuracy_flags) or 'none'}")
    print("=" * 70)

    # Top-3 finding preview
    for i, f in enumerate(report.findings[:3], 1):
        print(f"  [{i}] [{f.severity.value.upper()}] {f.title}")
        print(f"      {f.summary[:120]}")

    md_path, json_path = _dump_artifacts(report, full_md)
    print()
    print(f"[artifacts] {md_path}")
    print(f"[artifacts] {json_path}")

    if not args.no_persist and not args.stub:
        _persist_report(db, args.prompt, report, full_md,
                         date_start, date_end, args.trc_filter, duration_ms)
        print()
        print(">> Launch the app: python main.py")
        print(">> Navigate: AI Reports -> Report History -> click the most recent row.")
    elif args.stub:
        print()
        print("[stub] persistence skipped -- stub run is not written to analysis_reports.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
