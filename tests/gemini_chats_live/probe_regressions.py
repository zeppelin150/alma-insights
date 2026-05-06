"""Diagnostic probe for D-run nondeterministic regressions.

Re-runs the questions that regressed C → D (Q07, Q08, Q17, plus Q11/Q16
as auxiliary signal) N times each with a **fresh bridge per iteration**.

Each iteration writes its own transcript and tool-call trace timestamp.
The aggregate report distinguishes:

  H1 — sampling nondeterminism: PASS rate fluctuates between iterations
       even with identical inputs. Same prompt + same tools, just
       different reasoning paths.
  H2 — prompt-complexity interference: ALL iterations look the same
       (consistently bad). Suggests the prompt itself is causing the
       failure, not sampling.
  H3 — tool-choice confusion: the trace shows Gemini calling the wrong
       tool for the question (e.g. friction_distribution for a
       time-series query).

Verdicts per question are based on simple heuristics; raw responses
are saved for manual grading too.

Usage:
    python tests/gemini_chats_live/probe_regressions.py
        [--iterations 5]
        [--questions Q07,Q08,Q17]
        [--model gemini-2.5-flash-lite]

Output:
    tests/gemini_chats_live/results/probe_regr_{stamp}/
        transcripts.jsonl
        verdicts.json
        report.md
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from tests.gemini_chats_live.run_programmatic import _setup, ask, DB_PATH

import yaml


QUESTIONS_PATH = Path(__file__).resolve().parent / "questions.yaml"


# ──────────────────────────────────────────────────────────────────
# Per-question pass heuristics (rough; backed up by saved responses
# for manual grading)
# ──────────────────────────────────────────────────────────────────

def verdict_q07(response: str) -> tuple[str, str]:
    """Q07: 'Was there a spike in cancellation fee... in February 2025?'
    PASS = identifies a spike with at least one weekly number.
    FAIL = "could not find" / "no TRC matching".
    """
    if not response:
        return "FAIL", "empty response"
    r = response.lower()
    if any(p in r for p in ("could not find", "no trc matching",
                            "i cannot determine", "no tickets found")):
        return "FAIL", "tool-not-found language"
    # Look for a yes + number pattern
    has_yes = "yes" in r or "spike" in r
    has_week_number = any(f"w{n:02d}" in r or f"w{n}" in r for n in range(2, 16))
    has_count = any(f"{n} ticket" in r for n in range(5, 60))
    if has_yes and (has_week_number or has_count):
        return "PASS", "yes + weekly counts"
    return "WEAK", "ambiguous"


def verdict_q08(response: str) -> tuple[str, str]:
    """Q08: 'Which subcluster grew fastest week-over-week?'
    PASS = identifies subcluster + WoW jump.
    FAIL = "cannot determine" / "does not provide".
    """
    if not response:
        return "FAIL", "empty response"
    r = response.lower()
    if any(p in r for p in ("cannot determine", "does not provide",
                            "would need to compare", "i cannot")):
        return "FAIL", "gave up on WoW"
    has_jump = "jump" in r or "+" in r or "growth" in r
    has_subcluster = "subcluster" in r or "sub-cluster" in r or "cancellation fee" in r or "eap" in r
    if has_jump and has_subcluster:
        return "PASS", "WoW analysis attempted"
    return "WEAK", "partial"


def verdict_q17(response: str) -> tuple[str, str]:
    """Q17: 'Which TRC has shown the clearest declining trend?'
    PASS = names a TRC + plausible weekly numbers (no constant-N nonsense).
    FAIL = claims constant or fabricates (e.g. 24/week with TRC of 38 total).
    """
    if not response:
        return "FAIL", "empty response"
    r = response.lower()
    if "no change" in r:
        return "FAIL", "claims constant (fabricated)"
    if any(p in r for p in ("cannot determine", "does not provide",
                            "i would need")):
        return "FAIL", "gave up"
    has_trc = any(p in r for p in ("trc", "client cannot", "provider", "refund"))
    has_decline = "decline" in r or "declining" in r or "decrease" in r
    if has_trc and has_decline:
        return "PASS", "declining trend identified"
    return "WEAK", "partial"


def verdict_q11(response: str) -> tuple[str, str]:
    """Q11: 'unusual keyword bursts in March 2025?'
    PASS = identifies real anomaly (business days / 2025-03-11).
    """
    if not response:
        return "FAIL", "empty"
    r = response.lower()
    if "business days" in r or "business" in r:
        return "PASS", "found 'business' anomaly"
    if "no" in r and "burst" in r:
        return "WEAK", "claims none"
    return "WEAK", "ambiguous"


def verdict_q16(response: str) -> tuple[str, str]:
    """Q16: 'weekly volume Q1 2025 — which week peaked?'
    PASS = identifies a peak week with a count >= 80 (real range W04-W09).
    """
    if not response:
        return "FAIL", "empty"
    r = response.lower()
    if "could not" in r or "cannot" in r or "no tickets" in r:
        return "FAIL", "gave up"
    return "PASS", "answered (manual review for accuracy)"


VERDICT_FUNCS = {
    "Q07": verdict_q07, "Q08": verdict_q08, "Q11": verdict_q11,
    "Q16": verdict_q16, "Q17": verdict_q17,
}


# ──────────────────────────────────────────────────────────────────
# Probe runner
# ──────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=5)
    parser.add_argument("--questions", default="Q07,Q08,Q17",
                        help="comma-separated IDs from questions.yaml")
    parser.add_argument("--model", default="gemini-2.5-flash-lite")
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args()

    qids = [q.strip() for q in args.questions.split(",") if q.strip()]
    qbank = {q["id"]: q for q in yaml.safe_load(QUESTIONS_PATH.read_text())["questions"]}
    selected = [(qid, qbank[qid]["question"]) for qid in qids if qid in qbank]
    if not selected:
        raise SystemExit(f"No questions matched {qids}")

    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    out_dir = ROOT / "tests" / "gemini_chats_live" / "results" / f"probe_regr_{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    transcripts_path = out_dir / "transcripts.jsonl"
    verdicts_path = out_dir / "verdicts.json"
    report_path = out_dir / "report.md"

    print(f"[probe] iterations={args.iterations} questions={qids}")
    print(f"[probe] output: {out_dir}")

    run_start = datetime.now()
    results: dict[str, list[dict]] = {qid: [] for qid in qids}

    for qid, qtext in selected:
        for i in range(1, args.iterations + 1):
            print(f"\n[probe] {qid} iter {i}/{args.iterations}: fresh bridge...")
            try:
                app, engine, bridge_box = _setup(args.model)
            except Exception as e:
                print(f"[probe] setup failed: {e}")
                continue

            r = ask(app, engine, qtext, args.timeout)
            verdict_fn = VERDICT_FUNCS.get(qid, lambda _: ("UNKNOWN", "no heuristic"))
            verdict, note = verdict_fn(r.get("response") or "")

            record = {
                "iter": i,
                "qid": qid,
                "verdict": verdict,
                "note": note,
                "elapsed_s": r.get("elapsed_s"),
                "response_len": len(r.get("response") or ""),
                "response": r.get("response"),
                "error": r.get("error"),
                "timestamp": datetime.now().isoformat(timespec="seconds"),
            }
            with transcripts_path.open("a", encoding="utf-8") as fp:
                fp.write(json.dumps(record, ensure_ascii=False) + "\n")
            results[qid].append(record)

            print(f"    [{verdict}] {note} | elapsed={r.get('elapsed_s')}s "
                  f"len={record['response_len']}")

            try:
                bridge_box[0].shutdown()
            except Exception:
                pass

    # Aggregate verdicts
    aggregate: dict[str, dict] = {}
    for qid, records in results.items():
        verdicts = [r["verdict"] for r in records]
        aggregate[qid] = {
            "iterations": len(records),
            "PASS": verdicts.count("PASS"),
            "FAIL": verdicts.count("FAIL"),
            "WEAK": verdicts.count("WEAK"),
            "UNKNOWN": verdicts.count("UNKNOWN"),
            "pass_rate": (
                verdicts.count("PASS") / len(verdicts) if verdicts else 0.0
            ),
            "verdicts_in_order": verdicts,
        }

    # Pull tool-call trace for the run window
    run_end = datetime.now()
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT tool_name, args_json, result_rows, error,
                  substr(created_at, 12, 8) AS t
           FROM chat_tool_executions
           WHERE created_at >= ? AND created_at <= ?
           ORDER BY created_at""",
        (run_start.strftime("%Y-%m-%d %H:%M:%S"),
         run_end.strftime("%Y-%m-%d %H:%M:%S")),
    ).fetchall()
    tool_trace = [dict(r) for r in rows]
    conn.close()

    summary = {
        "stamp": stamp,
        "model": args.model,
        "iterations_per_q": args.iterations,
        "questions": qids,
        "aggregate": aggregate,
        "tool_trace_count": len(tool_trace),
        "tool_trace": tool_trace,
    }
    verdicts_path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")

    # Write a brief report
    parts = [f"# Regression-isolation probe — {stamp}", ""]
    parts.append(f"Model: `{args.model}` · Iterations per question: {args.iterations}")
    parts.append("")
    parts.append("## Pass rates per question")
    parts.append("")
    parts.append("| QID | PASS | FAIL | WEAK | rate | verdicts in order |")
    parts.append("|---|---:|---:|---:|---:|---|")
    for qid, agg in aggregate.items():
        parts.append(
            f"| {qid} | {agg['PASS']} | {agg['FAIL']} | {agg['WEAK']} | "
            f"{agg['pass_rate']:.0%} | `{' '.join(agg['verdicts_in_order'])}` |"
        )
    parts.append("")
    parts.append("## Hypothesis distinguishability")
    parts.append("")
    parts.append("- **All PASS or all FAIL** for a question → **H2** (prompt/tool config issue, not sampling)")
    parts.append("- **Mixed PASS/FAIL** for a question → **H1** (sampling nondeterminism)")
    parts.append("- **All FAIL with wrong tool in trace** → **H3** (tool-choice confusion)")
    parts.append("")
    parts.append("## Tool trace summary")
    parts.append("")
    parts.append(f"{len(tool_trace)} tool calls during the probe window.")
    parts.append("")
    if tool_trace:
        from collections import Counter
        tool_counter = Counter(r["tool_name"] for r in tool_trace)
        parts.append("| tool | count |")
        parts.append("|---|---:|")
        for name, n in tool_counter.most_common():
            parts.append(f"| `{name}` | {n} |")
        parts.append("")

    report_path.write_text("\n".join(parts), encoding="utf-8")
    print(f"\n[probe] verdicts: {verdicts_path}")
    print(f"[probe] report: {report_path}")

    # Print pass-rate summary to stdout
    print("\n[probe] === SUMMARY ===")
    for qid, agg in aggregate.items():
        print(f"  {qid}: PASS={agg['PASS']}/{args.iterations} "
              f"({agg['pass_rate']:.0%})  "
              f"verdicts: {' '.join(agg['verdicts_in_order'])}")


if __name__ == "__main__":
    main()
