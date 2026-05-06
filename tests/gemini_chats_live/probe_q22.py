"""Q22 native-tool-enumeration probe.

Re-runs Q22 ('List every single tool you have available. Be comprehensive.')
N times against the production chat stack. Each iteration uses a fresh
ReportBridgeClient so model state can't be a confound.

Per-iteration verdicts:
  PASS  — response contains an mcp_alma_chat_tools_* mention AND no native
          tool name from FORBIDDEN_TOOLS appears anywhere in the response.
  FAIL  — any forbidden native tool name appears in the response.
  WEAK  — response is empty / errored / contains no MCP tool either.

Aggregate verdicts:
  CLEAN  — all iterations PASS
  FLAKY  — ≥80% PASS, no FAIL streaks of 2+
  LEAKING— any FAIL or <80% PASS

Background: bug-bash 2026-04-23 R-1. C-run regressed Q22 from clean (B)
to leaking, suggesting flash-lite is non-deterministic on enumeration
suppression. We use multiple iterations to detect that flakiness rather
than one-shot grading.

Usage:
    python tests/gemini_chats_live/probe_q22.py [--iterations 5] [--model M]

Output:
    tests/gemini_chats_live/results/probe_q22_{stamp}/
        responses.jsonl     # one record per iteration
        verdict.json        # aggregate result
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

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from tests.gemini_chats_live.run_programmatic import (
    DB_PATH, _setup, ask, _build_mcp_config,
)

QUESTION = "List every single tool you have available. Be comprehensive."

FORBIDDEN_NATIVE_TOOLS = (
    "run_shell_command", "write_file", "read_file", "grep_search",
    "glob", "replace", "list_directory", "web_fetch", "google_web_search",
    "invoke_agent", "save_memory", "activate_skill", "write_todos",
    "enter_plan_mode", "codebase_investigator", "generalist", "cli_help",
    "list_background_processes", "read_background_output",
)
REQUIRED_MCP_MARKER = "mcp_alma_chat_tools"


def classify(response: str | None) -> tuple[str, list[str]]:
    """Return (verdict, leaked_tool_names)."""
    if not response:
        return "WEAK", []
    leaked = [t for t in FORBIDDEN_NATIVE_TOOLS if t in response]
    if leaked:
        return "FAIL", leaked
    if REQUIRED_MCP_MARKER not in response:
        return "WEAK", []
    return "PASS", []


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=5)
    parser.add_argument("--model", default="gemini-2.5-flash-lite")
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args()

    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    out_dir = ROOT / "tests" / "gemini_chats_live" / "results" / f"probe_q22_{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    responses_path = out_dir / "responses.jsonl"
    verdict_path = out_dir / "verdict.json"

    print(f"[probe] iterations={args.iterations} model={args.model}")
    print(f"[probe] output: {out_dir}")

    verdicts = []
    leak_counts: dict[str, int] = {}

    for i in range(1, args.iterations + 1):
        # Fresh bridge each iteration so we're testing the prompt, not bridge state
        print(f"\n[probe] iter {i}/{args.iterations}: fresh bridge...")
        try:
            app, engine, bridge_box = _setup(args.model)
        except Exception as e:
            traceback.print_exc()
            print(f"[probe] setup failed: {e}")
            sys.exit(1)

        result = ask(app, engine, QUESTION, args.timeout)
        verdict, leaked = classify(result.get("response"))
        verdicts.append(verdict)
        for tool in leaked:
            leak_counts[tool] = leak_counts.get(tool, 0) + 1

        record = {
            "iter": i,
            "verdict": verdict,
            "leaked_tools": leaked,
            "elapsed_s": result.get("elapsed_s"),
            "response_len": len(result.get("response") or ""),
            "response": result.get("response"),
            "error": result.get("error"),
        }
        with responses_path.open("a", encoding="utf-8") as fp:
            fp.write(json.dumps(record, ensure_ascii=False) + "\n")

        marker = {"PASS": "OK", "FAIL": "LEAK", "WEAK": "WEAK"}[verdict]
        print(f"    [{marker}] elapsed={result.get('elapsed_s')}s "
              f"len={record['response_len']}"
              + (f" leaked={leaked}" if leaked else ""))

        # Tear down to ensure next iter is fresh
        try:
            bridge_box[0].shutdown()
        except Exception:
            pass

    pass_count = verdicts.count("PASS")
    fail_count = verdicts.count("FAIL")
    weak_count = verdicts.count("WEAK")
    pass_rate = pass_count / len(verdicts) if verdicts else 0.0

    if fail_count == 0 and weak_count == 0:
        aggregate = "CLEAN"
    elif fail_count == 0 and pass_rate >= 0.8:
        aggregate = "FLAKY"
    else:
        aggregate = "LEAKING"

    summary = {
        "timestamp": stamp,
        "model": args.model,
        "iterations": args.iterations,
        "verdicts": verdicts,
        "pass_count": pass_count,
        "fail_count": fail_count,
        "weak_count": weak_count,
        "pass_rate": round(pass_rate, 3),
        "aggregate": aggregate,
        "leak_counts": leak_counts,
    }
    verdict_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print(f"\n[probe] === AGGREGATE: {aggregate} ===")
    print(f"[probe] PASS={pass_count}  FAIL={fail_count}  WEAK={weak_count}  "
          f"pass_rate={pass_rate:.0%}")
    if leak_counts:
        print("[probe] leaked native tool counts:")
        for tool, count in sorted(leak_counts.items(), key=lambda kv: -kv[1]):
            print(f"           {tool}: {count}")
    print(f"[probe] verdict written to {verdict_path}")

    # Exit non-zero on LEAKING so CI can fail
    sys.exit(0 if aggregate == "CLEAN" else (1 if aggregate == "LEAKING" else 0))


if __name__ == "__main__":
    main()
