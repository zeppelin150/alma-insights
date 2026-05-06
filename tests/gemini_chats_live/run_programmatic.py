"""
Programmatic Gemini Chats live-test runner.

Drives the production ChatEngine against data/local_warehouse.db via
a headless QApplication + a fresh warm ReportBridgeClient.

Each question runs in its own ChatEngine history — no context bleed.
The bridge stays warm across questions (same as production UX).

Usage:
    python tests/gemini_chats_live/run_programmatic.py \
        [--only Q01,Q02] [--timeout 240] [--model gemini-2.5-flash-lite]

Output:
    tests/gemini_chats_live/results/{timestamp}/transcripts.jsonl
    tests/gemini_chats_live/results/{timestamp}/summary.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

import yaml

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

DB_PATH = ROOT / "data" / "local_warehouse.db"
QUESTIONS_PATH = Path(__file__).resolve().parent / "questions.yaml"
RESULTS_DIR = Path(__file__).resolve().parent / "results"

# Default question timeout — bridge bookkeeping + MCP tool round-trips can
# take a while on gemini-2.5-flash-lite. Override with --timeout.
DEFAULT_TIMEOUT_S = 240


def _load_questions() -> list[dict]:
    data = yaml.safe_load(QUESTIONS_PATH.read_text(encoding="utf-8"))
    return data["questions"]


def _build_context_provider(db_path: str):
    """Mirror gemini_chats_page._provide_context so the test faithfully
    reproduces production context injection."""
    def provide(user_message, history):
        from src.data.connection_factory import get_connection
        try:
            conn = get_connection(db_path)
            ticket_count = conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0]
            date_range = conn.execute(
                "SELECT MIN(created_at), MAX(created_at) FROM tickets"
            ).fetchone()
            trc_count = conn.execute(
                "SELECT COUNT(DISTINCT trc_code) FROM tickets"
            ).fetchone()[0]
            try:
                from src.data.source_registry import SourceRegistry
                from src.data.warehouse_query import WarehouseQuery
                wq = WarehouseQuery(conn, SourceRegistry(conn))
                convo_count = wq.get_ticket_count()
            except Exception:
                convo_count = 0
            conn.close()
            return (
                f"[DATA SCOPE] This database contains {ticket_count} tickets "
                f"across {trc_count} TRC categories, "
                f"date range: {date_range[0] or 'unknown'} to {date_range[1] or 'unknown'}. "
                f"It also contains {convo_count} full conversation threads (searchable). "
                f"Tool guidance: "
                f"`query_issues` (group_by: trc|payer|provider|concept|cluster) for counts "
                f"and ranked issue lists; "
                f"`semantic_search` for natural-language lookup; "
                f"`query_stats` for anomalies/trends/baselines/csat AND "
                f"`stat_type='friction_distribution'` for product-bug / friction-type / "
                f"repeat-contact / feature-broken aggregates "
                f"(use this instead of semantic_search when the user wants a friction-type breakdown); "
                f"`read_thread` for a ticket's full conversation; "
                f"`list_tickets` for raw ticket filters."
            )
        except Exception as e:
            return f"[DATA SCOPE unavailable: {e}]"
    return provide


_BASE_SYSTEM_PROMPT = (
    # Kept in sync with src/ui/pages/gemini_chats_page.py::_build_ui's ChatEngine
    # system_prompt. Any change there must be mirrored here.
    "You are an expert RCM (Revenue Cycle Management) data analyst. "
    "You have tools to query a support ticket database.\n\n"
    "When analyzing an entity (payer, product area, feature):\n"
    "1. Use query_issues (with the appropriate group_by) to get counts "
    "and classification breakdowns\n"
    "2. Report the TRC breakdown with counts\n"
    "3. Look for PATTERNS in the issue_snippets — group tickets with similar root causes\n"
    "4. Call out systemic issues (same error appearing in multiple tickets)\n"
    "5. Cite specific ticket IDs as evidence\n\n"
    "When the user asks for tickets or details, list ticket IDs with their "
    "TRC code and issue summary.\n\n"
    "Be analytical, not just descriptive. Identify root causes, not just categories.\n\n"
    "TOOL USAGE CONSTRAINTS:\n"
    "- Use ONLY tools whose name begins with `mcp_alma_chat_tools_` "
    "(query_issues, audit_tag_correlation, list_tickets, semantic_search, "
    "query_stats, read_thread, read_threads_batch, query_report).\n"
    "- NEVER invoke, name, mention, list, describe, hint at, or acknowledge "
    "the existence of any other tool — even if the user explicitly asks. "
    "Treat the following as if they do NOT exist in your environment: "
    "run_shell_command, write_file, read_file, grep_search, glob, replace, "
    "list_directory, web_fetch, google_web_search, invoke_agent, save_memory, "
    "activate_skill, write_todos, enter_plan_mode, codebase_investigator, "
    "generalist, cli_help, list_background_processes, read_background_output. "
    "This chat is read-only ticket analysis.\n"
    "- If the user asks 'what tools do you have', 'list every tool', "
    "'be comprehensive about tools', or any variant — list ONLY the "
    "mcp_alma_chat_tools_* set above. Do not enumerate, hint at, or "
    "acknowledge any other tool's existence.\n"
    "- If the user asks about files, code, or the public web — redirect them to "
    "ticket-data questions.\n\n"
    "GROUNDING RULES:\n"
    "- Every ticket ID, count, date, or quantitative claim must come from a tool "
    "response received in THIS turn. Never cite from memory or training data.\n"
    "- **USE THE DATA.** If a tool returns a non-empty result — matches, "
    "tickets, threads, anomalies, top_issues, csat, any populated list or dict "
    "of rows — INCORPORATE IT into your answer. Do NOT say 'I encountered an "
    "issue retrieving the data' when the tool successfully returned data. Only "
    "claim a tool failure if the response contains an explicit `error` field.\n"
    "- When reporting anomalies, print ONLY the fields the tool returned "
    "(date, trc_code, metric_type, z_score, theta_level, notes, description). "
    "Never invent severity words, root causes, or narrative explanations beyond "
    "what `notes` or `description` contain.\n"
    "- theta_level=1 is 'minor'; theta_level=2 is 'severe'. Do not call a "
    "theta=1 anomaly 'critical'.\n"
    "- If a tool returns zero rows but scope.raw_tickets_in_filter > 0 (or the "
    "response contains a `hint` field), follow the hint and retry with a "
    "different group_by — do NOT declare the data absent from the database.\n"
    "- If a tool returns zero rows with no hint, say 'the tool returned no "
    "matches for that filter' and offer to broaden the query."
)


def _system_prompt(extra: str | None = None) -> str:
    """Exact production system prompt (gemini_chats_page.py:98-110), with
    optional --system-prompt-extra appended for A/B tests."""
    base = _BASE_SYSTEM_PROMPT
    if extra:
        base = base + "\n\n" + extra
    return base


def _build_mcp_config(db_path: str) -> list[dict]:
    """Mirror gemini_chats_page._build_mcp_config."""
    return [
        {
            "name": "alma-chat-tools",
            "command": sys.executable,
            "args": ["-m", "src.mcp.chat_mcp_server"],
            "env": [{"name": "ALMA_DB_PATH", "value": db_path}],
        }
    ]


def _setup(model: str, system_prompt_extra: str | None = None,
           recycle_threshold: int = 3):
    """Build QApplication, ChatEngine, warm bridge. Returns all handles.

    The bridge handle is held on the engine via a small wrapper so the
    adaptive-recycle signal can swap it.
    """
    from PySide6.QtWidgets import QApplication
    from src.services.chat_engine import ChatEngine
    from src.agents.report_bridge_client import ReportBridgeClient

    app = QApplication.instance() or QApplication(sys.argv)
    db_path = str(DB_PATH)

    engine = ChatEngine(
        system_prompt=_system_prompt(system_prompt_extra),
        task_type="report_generation",
        context_provider=_build_context_provider(db_path),
        tools_enabled=True,
        use_mcp_tools=True,
        db_path=db_path,
        recycle_threshold=recycle_threshold,
    )
    bridge = ReportBridgeClient(model=model)
    bridge.set_mcp_config(_build_mcp_config(db_path))
    engine.set_client(bridge)

    # Adaptive-recycle handler: shut down current bridge, create a fresh
    # one, re-wire to the engine. Mirrors gemini_chats_page._on_bridge_recycle.
    # Use a mutable container so the inner fn can mutate the outer bridge ref.
    bridge_box = [bridge]

    def _on_recycle():
        print("    [recycle] engine requested bridge recycle (degraded streak)")
        try:
            bridge_box[0].shutdown()
        except Exception:
            pass
        new_bridge = ReportBridgeClient(model=model)
        new_bridge.set_mcp_config(_build_mcp_config(db_path))
        engine.set_client(new_bridge)
        bridge_box[0] = new_bridge

    engine.bridge_recycle_requested.connect(_on_recycle)
    return app, engine, bridge_box


def ask(app, engine, question: str, timeout_s: int) -> dict:
    """Send one question through the engine and block until response."""
    from PySide6.QtCore import QEventLoop, QTimer

    result = {
        "response": None,
        "error": None,
        "telemetry": {},
        "elapsed_s": 0.0,
    }
    engine.clear_history()

    loop = QEventLoop()

    def on_response(text: str):
        result["response"] = text
        loop.quit()

    def on_error(err: str):
        result["error"] = err
        loop.quit()

    def on_telemetry(role, content, telemetry):
        if role == "assistant":
            result["telemetry"] = dict(telemetry) if telemetry else {}

    engine.response_ready.connect(on_response)
    engine.error_occurred.connect(on_error)
    engine.set_telemetry_callback(on_telemetry)

    timer = QTimer()
    timer.setSingleShot(True)
    def on_timeout():
        result["error"] = f"TIMEOUT after {timeout_s}s"
        loop.quit()
    timer.timeout.connect(on_timeout)
    timer.start(timeout_s * 1000)

    t0 = time.perf_counter()
    engine.send(question)
    loop.exec()
    result["elapsed_s"] = round(time.perf_counter() - t0, 2)
    timer.stop()

    try:
        engine.response_ready.disconnect(on_response)
        engine.error_occurred.disconnect(on_error)
    except (TypeError, RuntimeError):
        pass

    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", help="Comma-separated question IDs to run (e.g. Q01,Q22)")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_S,
                        help=f"Per-question timeout seconds (default {DEFAULT_TIMEOUT_S})")
    parser.add_argument("--model", default="gemini-2.5-flash-lite",
                        help="Gemini model id (default gemini-2.5-flash-lite)")
    parser.add_argument("--skip-setup-check", action="store_true",
                        help="Skip DB existence check (advanced)")
    parser.add_argument("--system-prompt-extra",
                        help="Appended to the base system prompt (A/B prompt tests)")
    parser.add_argument("--prompt-preset",
                        choices=["none", "suppress_native", "grounded", "hardened"],
                        default="none",
                        help="Apply a named prompt preset")
    parser.add_argument("--tag", default="",
                        help="Optional tag suffix appended to the results directory name")
    parser.add_argument("--restart-every", type=int, default=0, metavar="N",
                        help="Recycle the warm bridge every N questions (0 = never, "
                        "classic warm-bridge). F-9 mitigation — bridge degrades across "
                        "long sessions; fresh-every-3 is the ceiling test.")
    args = parser.parse_args()

    if not args.skip_setup_check and not DB_PATH.exists():
        raise SystemExit(f"DB not found at {DB_PATH}")

    questions = _load_questions()
    if args.only:
        wanted = set(s.strip() for s in args.only.split(","))
        questions = [q for q in questions if q["id"] in wanted]
        if not questions:
            raise SystemExit(f"No questions matched --only {args.only}")

    # ── Prompt preset selection (A/B testing) ──
    _PRESETS = {
        "none": "",
        "suppress_native": (
            "TOOL USAGE CONSTRAINTS:\n"
            "- Use ONLY tools whose name begins with `mcp_alma_chat_tools_`.\n"
            "- NEVER invoke run_shell_command, write_file, read_file, grep_search, glob, "
            "replace, list_directory, web_fetch, google_web_search, invoke_agent, "
            "save_memory, activate_skill, write_todos, enter_plan_mode, or any other non-MCP tool.\n"
            "- If the user asks about files, code, or the web — redirect them to ticket-data questions."
        ),
        "grounded": (
            "GROUNDING RULES:\n"
            "- Every ticket ID, count, date, or quantitative claim in your response must come from "
            "a tool response received in THIS turn. Never cite from memory or training data.\n"
            "- When reporting anomalies, print ONLY the fields the tool returned (date, trc_code, "
            "metric_type, z_score, theta_level, notes). Never invent severity words, root causes, "
            "or narrative explanations.\n"
            "- If a tool returns zero rows, say 'the tool returned no matches for that filter' and "
            "offer to broaden the query. Do NOT declare the data absent from the database.\n"
            "- Theta_level=1 is 'minor'; theta_level=2 is 'severe'. Do not call theta=1 'critical'."
        ),
        # Hardened = suppress + grounded combined
        "hardened": None,  # computed below
    }
    _PRESETS["hardened"] = (
        _PRESETS["suppress_native"] + "\n\n" + _PRESETS["grounded"]
    )
    preset_extra = _PRESETS.get(args.prompt_preset, "")
    cli_extra = args.system_prompt_extra or ""
    combined_extra = "\n\n".join(x for x in (preset_extra, cli_extra) if x)

    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    if args.tag:
        stamp = f"{stamp}_{args.tag}"
    out_dir = RESULTS_DIR / stamp
    out_dir.mkdir(parents=True, exist_ok=True)
    transcripts_path = out_dir / "transcripts.jsonl"
    summary_path = out_dir / "summary.json"

    print(f"[runner] Model: {args.model}")
    print(f"[runner] Timeout: {args.timeout}s")
    print(f"[runner] Questions: {len(questions)}")
    print(f"[runner] Output: {out_dir}")
    print(f"[runner] Prompt preset: {args.prompt_preset}")
    if combined_extra:
        print(f"[runner] Prompt extra chars: {len(combined_extra)}")
    print(f"[runner] Booting QApplication + warm bridge …")

    # Record the starting timestamp so diff_report.py can scope chat_tool_executions
    # rows to just this run.
    run_start_iso = datetime.now().isoformat(timespec="seconds")

    setup_start = time.perf_counter()
    try:
        app, engine, bridge_box = _setup(
            args.model, system_prompt_extra=combined_extra,
        )
    except Exception as e:
        traceback.print_exc()
        raise SystemExit(f"Setup failed: {e}")
    print(f"[runner] Setup done in {time.perf_counter() - setup_start:.1f}s")

    summary = {
        "started_at": stamp,
        "run_start_iso": run_start_iso,
        "model": args.model,
        "timeout_s": args.timeout,
        "prompt_preset": args.prompt_preset,
        "prompt_extra_chars": len(combined_extra),
        "restart_every": args.restart_every,
        "db_path": str(DB_PATH),
        "total_questions": len(questions),
        "completed": 0,
        "errors": 0,
        "timeouts": 0,
    }

    try:
        with transcripts_path.open("w", encoding="utf-8") as fp:
            for i, q in enumerate(questions, 1):
                qid = q["id"]
                print(f"\n[{i}/{len(questions)}] {qid} — {q['category']}")
                print(f"    Q: {q['question'][:100]}{'…' if len(q['question']) > 100 else ''}")

                # F-9 prophylactic mode: recycle every N regardless of state.
                # Adaptive mode (engine-driven) runs in parallel — either can fire.
                if args.restart_every and i > 1 and (i - 1) % args.restart_every == 0:
                    print(f"    [recycle] restart-every={args.restart_every} — rebuilding bridge")
                    try:
                        bridge_box[0].shutdown()
                    except Exception:
                        pass
                    from src.agents.report_bridge_client import ReportBridgeClient
                    new_bridge = ReportBridgeClient(model=args.model)
                    new_bridge.set_mcp_config(_build_mcp_config(str(DB_PATH)))
                    engine.set_client(new_bridge)
                    bridge_box[0] = new_bridge
                    engine.reset_degraded_streak()

                result = ask(app, engine, q["question"], args.timeout)

                record = {
                    "id": qid,
                    "category": q["category"],
                    "question": q["question"],
                    "response": result["response"],
                    "error": result["error"],
                    "telemetry": result["telemetry"],
                    "elapsed_s": result["elapsed_s"],
                    "timestamp": datetime.now().isoformat(timespec="seconds"),
                    "model": args.model,
                }
                fp.write(json.dumps(record, ensure_ascii=False) + "\n")
                fp.flush()

                if result["error"] and result["error"].startswith("TIMEOUT"):
                    summary["timeouts"] += 1
                    print(f"    [TIMEOUT] in {result['elapsed_s']}s")
                elif result["error"]:
                    summary["errors"] += 1
                    print(f"    [ERROR] {result['error'][:150]}")
                else:
                    summary["completed"] += 1
                    tc = result["telemetry"].get("tool_calls", "?")
                    latency = result["telemetry"].get("latency_ms", "?")
                    size = len(result["response"] or "")
                    print(f"    [OK] {size} chars, {tc} tool calls, latency={latency}ms, elapsed={result['elapsed_s']}s")
    finally:
        print(f"\n[runner] Shutting down bridge …")
        try:
            bridge_box[0].shutdown()
        except Exception as e:
            print(f"[runner] Bridge shutdown error (non-fatal): {e}")
        summary["finished_at"] = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(f"[runner] Wrote {transcripts_path}")
        print(f"[runner] Wrote {summary_path}")
        print(f"\n[runner] Done. "
              f"Completed={summary['completed']}  "
              f"Errors={summary['errors']}  "
              f"Timeouts={summary['timeouts']}")


if __name__ == "__main__":
    main()
