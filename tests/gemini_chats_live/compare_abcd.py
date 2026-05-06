"""Side-by-side ABCD comparison helper.

Loads the four runs and prints per-question response previews + tool-call
counts so the human grader can quickly diff. Optional; the abcd_diff_report
gets written by hand based on this output.

Usage:
    python tests/gemini_chats_live/compare_abcd.py [--limit-chars 240]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent

# Ordered: A baseline -> B post-fix -> C hardened -> D strict
RUNS = [
    ("A", "2026-04-23_111543"),
    ("B", "2026-04-23_123533_postfix"),
    ("C", "2026-04-23_130502_c_hardened"),
    ("D", None),  # filled in dynamically — most recent _d_strict folder
]


def find_d() -> str | None:
    results = ROOT / "tests" / "gemini_chats_live" / "results"
    candidates = sorted(p.name for p in results.iterdir() if p.name.endswith("_d_strict"))
    return candidates[-1] if candidates else None


def load(run_dir: str) -> dict[str, dict]:
    path = ROOT / "tests" / "gemini_chats_live" / "results" / run_dir / "transcripts.jsonl"
    out: dict[str, dict] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        out[r["id"]] = r
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit-chars", type=int, default=200)
    args = parser.parse_args()

    runs = list(RUNS)
    runs[-1] = ("D", find_d())
    if runs[-1][1] is None:
        raise SystemExit("No *_d_strict run found")

    by_run = {label: load(d) for label, d in runs}
    qids = sorted(by_run["A"].keys())

    for qid in qids:
        print(f"\n{'='*70}")
        print(qid)
        for label, _ in runs:
            r = by_run[label].get(qid, {})
            resp = (r.get("response") or f"[ERR: {r.get('error')}]")
            preview = resp[: args.limit_chars].replace("\n", " ⏎ ")
            elapsed = r.get("elapsed_s", "?")
            tools = r.get("telemetry", {}).get("tool_calls", "?")
            print(f"  [{label}] {elapsed}s tools={tools} :: {preview}")


if __name__ == "__main__":
    main()
