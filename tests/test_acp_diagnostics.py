"""
ACP Bridge Diagnostics — Worker Silence Investigation

Tests WHY workers go silent during concurrent ACP sessions.
Hypotheses to test:
  H1: Gemini API rate-limits / throttles concurrent sessions
  H2: SQLite WAL contention blocks MCP subprocess writes
  H3: CLI subprocess dies silently (reader thread exits)
  H4: Events arrive but get filtered/dropped by reader
  H5: CLI hangs waiting for permission response we never send

Usage:
  python tests/test_acp_diagnostics.py
  python tests/test_acp_diagnostics.py --test concurrency
  python tests/test_acp_diagnostics.py --test sqlite
  python tests/test_acp_diagnostics.py --test single
"""

import io
import sys
import os
import json
import time
import sqlite3
import logging
import threading
from pathlib import Path
from queue import Queue, Empty
from concurrent.futures import ThreadPoolExecutor, as_completed

# Force UTF-8 on Windows
if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Verbose logging — capture EVERYTHING from the bridge
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    stream=sys.stdout,
    force=True,
)
# Quiet noisy loggers
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("asyncio").setLevel(logging.WARNING)

logger = logging.getLogger("diagnostics")

DB_PATH = Path("data/local_warehouse.db")
SIMPLE_PROMPT = (
    "Classify this single ticket by calling store_classification:\n"
    "ticket_id: DIAG-001\n"
    "trc: Diagnostics Test\n"
    "full_thread: [2025-01-01 10:00] CUSTOMER: I was charged twice for my copay.\n"
    "\n"
    "Call store_classification with:\n"
    "  ticket_id: DIAG-001\n"
    "  sub_cluster: duplicate copay charge\n"
    "  sub_cluster_confidence: 0.95\n"
    "  is_novel: false\n"
    "  sentiment_intensity: 3\n"
    "  sentiment_polarity: negative\n"
    "  friction_type: incorrect_charge\n"
    "  anomaly_flag: normal\n"
    "  entities: {}\n"
    "  key_phrases: [\"charged twice\", \"copay\"]\n"
    "  root_cause_hint: duplicate charge\n"
    "  summary: Customer reports duplicate copay charge\n"
    "\n"
    "TOOL CONSTRAINT: ONLY use MCP tools. No file reading, no code execution, "
    "no web search, no shell commands, no agent delegation. "
    "Call store_classification exactly once, then stop."
)


def _parse_args():
    import argparse
    parser = argparse.ArgumentParser(description="ACP Bridge Diagnostics")
    parser.add_argument("--test", type=str, default="all",
                        choices=["all", "single", "concurrency", "sqlite", "raw"],
                        help="Which diagnostic to run")
    return parser.parse_args()


# ═══════════════════════════════════════════════════════════════════
# TEST 1: Single bridge, single prompt — baseline
# ═══════════════════════════════════════════════════════════════════

def test_single_bridge():
    """One bridge, one simple prompt. Does it work at all?"""
    from src.agents.acp_bridge import ACPBridge

    print("\n" + "=" * 70)
    print("  TEST 1: Single bridge baseline")
    print("=" * 70)

    bridge = ACPBridge(model="gemini-2.5-flash")
    bridge.set_mcp_config([{
        "name": "alma-tools",
        "command": sys.executable,
        "args": ["-m", "src.mcp.alma_mcp_server"],
        "env": [],
    }])

    # Track raw events
    events_seen = []
    permission_requests = []

    # Monkey-patch the permission handler to log
    original_perm = bridge._handle_permission_request
    def _patched_perm(data):
        permission_requests.append(data)
        logger.info("DIAG: permission request: %s",
                     json.dumps(data.get("params", {}), default=str)[:300])
        original_perm(data)
    bridge._handle_permission_request = _patched_perm

    def on_token(event):
        events_seen.append({"type": event.type, "time": time.time(),
                           "data_keys": list((event.data or {}).keys())})
        logger.info("DIAG: event type=%s data_keys=%s",
                     event.type, list((event.data or {}).keys()))

    try:
        bridge.ensure_running()
        # Create session with DB path injected (matches orchestrator pattern)
        sid = bridge.new_session(mcp_env={"ALMA_DB_PATH": str(DB_PATH)})
        print(f"  Bridge alive: {bridge.is_alive()}")
        print(f"  Session ID: {sid}")

        t0 = time.time()
        result = bridge.call_streaming(
            SIMPLE_PROMPT, "diag_single_001",
            on_token=on_token, timeout=120,
        )
        elapsed = time.time() - t0

        print(f"\n  Result:")
        print(f"    elapsed:      {elapsed:.1f}s")
        print(f"    error:        {result.get('error')}")
        print(f"    full_text:    {len(result.get('full_text', ''))} chars")
        print(f"    events:       {len(events_seen)}")
        print(f"    permissions:  {len(permission_requests)}")
        print(f"    early_stopped: {result.get('early_stopped', False)}")

        # Count event types
        type_counts = {}
        for e in events_seen:
            type_counts[e["type"]] = type_counts.get(e["type"], 0) + 1
        print(f"    event types:  {type_counts}")

        # Check DB
        conn = sqlite3.connect(str(DB_PATH))
        diag_count = conn.execute(
            "SELECT COUNT(*) FROM nlp_ticket_classifications "
            "WHERE ticket_id = 'DIAG-001'"
        ).fetchone()[0]
        conn.close()
        print(f"    DB DIAG-001:  {diag_count} rows")

        if result.get("full_text"):
            preview = result["full_text"][:300].replace('\n', '\\n')
            print(f"    text preview: {preview}")

        return {
            "elapsed": elapsed,
            "error": result.get("error"),
            "events": len(events_seen),
            "permissions": len(permission_requests),
            "db_count": diag_count,
            "type_counts": type_counts,
        }

    except Exception as e:
        print(f"  EXCEPTION: {e}")
        return {"error": str(e)}
    finally:
        bridge.shutdown()


# ═══════════════════════════════════════════════════════════════════
# TEST 2: Concurrent bridges — does parallelism cause silence?
# ═══════════════════════════════════════════════════════════════════

def test_concurrency():
    """Boot N bridges simultaneously, send prompts, see who responds."""
    from src.agents.acp_bridge import ACPBridge

    N = 4  # match lower worker count
    print("\n" + "=" * 70)
    print(f"  TEST 2: {N} concurrent bridges")
    print("=" * 70)

    results = {}
    lock = threading.Lock()

    def run_one(idx):
        bridge_id = f"diag_concurrent_{idx}"
        ticket_id = f"DIAG-CONC-{idx}"

        prompt = SIMPLE_PROMPT.replace("DIAG-001", ticket_id)

        bridge = ACPBridge(model="gemini-2.5-flash")
        bridge.set_mcp_config([{
            "name": "alma-tools",
            "command": sys.executable,
            "args": ["-m", "src.mcp.alma_mcp_server"],
            "env": [],
        }])

        events = []
        perms = []

        original_perm = bridge._handle_permission_request
        def _patched_perm(data):
            perms.append(time.time())
            original_perm(data)
        bridge._handle_permission_request = _patched_perm

        def on_token(event):
            events.append({"type": event.type, "t": time.time()})

        try:
            bridge.ensure_running()
            sid = bridge.new_session(mcp_env={"ALMA_DB_PATH": str(DB_PATH)})
            logger.info("DIAG: bridge %d alive, session=%s", idx, sid)

            t0 = time.time()
            result = bridge.call_streaming(
                prompt, bridge_id,
                on_token=on_token, timeout=120,
            )
            elapsed = time.time() - t0

            # Check DB for this ticket
            conn = sqlite3.connect(str(DB_PATH))
            db_count = conn.execute(
                "SELECT COUNT(*) FROM nlp_ticket_classifications "
                "WHERE ticket_id = ?", (ticket_id,)
            ).fetchone()[0]
            conn.close()

            type_counts = {}
            for e in events:
                type_counts[e["type"]] = type_counts.get(e["type"], 0) + 1

            r = {
                "idx": idx,
                "elapsed": elapsed,
                "error": result.get("error"),
                "text_len": len(result.get("full_text", "")),
                "events": len(events),
                "permissions": len(perms),
                "db_count": db_count,
                "type_counts": type_counts,
                "early_stopped": result.get("early_stopped", False),
            }

            with lock:
                results[idx] = r

            logger.info("DIAG: bridge %d done: elapsed=%.1fs events=%d "
                         "db_count=%d error=%s",
                         idx, elapsed, len(events), db_count,
                         result.get("error"))

        except Exception as e:
            with lock:
                results[idx] = {"idx": idx, "error": str(e), "elapsed": 0}
            logger.error("DIAG: bridge %d EXCEPTION: %s", idx, e)

        finally:
            bridge.shutdown()

    # Boot all bridges at once, stagger prompts by 2s
    print(f"  Launching {N} bridges in parallel...")
    t_start = time.time()

    with ThreadPoolExecutor(max_workers=N) as pool:
        futures = {pool.submit(run_one, i): i for i in range(N)}
        for future in as_completed(futures, timeout=300):
            idx = futures[future]
            try:
                future.result()
            except Exception as e:
                print(f"  Bridge {idx} FAILED: {e}")

    total_elapsed = time.time() - t_start

    print(f"\n  Concurrency results ({total_elapsed:.1f}s total):")
    print(f"  {'Bridge':<8} {'Elapsed':>8} {'Events':>7} {'Perms':>6} "
          f"{'DB':>4} {'Error':<20} {'Types'}")
    print(f"  {'-'*8} {'-'*8} {'-'*7} {'-'*6} {'-'*4} {'-'*20} {'-'*20}")

    for i in range(N):
        r = results.get(i, {"error": "no result"})
        print(f"  {i:<8} {r.get('elapsed', 0):>7.1f}s {r.get('events', 0):>7} "
              f"{r.get('permissions', 0):>6} {r.get('db_count', 0):>4} "
              f"{str(r.get('error', ''))[:20]:<20} "
              f"{r.get('type_counts', {})}")

    silent = sum(1 for r in results.values() if r.get("events", 0) == 0)
    succeeded = sum(1 for r in results.values() if r.get("db_count", 0) > 0)
    print(f"\n  Silent workers:   {silent}/{N}")
    print(f"  Succeeded (DB>0): {succeeded}/{N}")

    return results


# ═══════════════════════════════════════════════════════════════════
# TEST 3: SQLite WAL contention — is DB locking the bottleneck?
# ═══════════════════════════════════════════════════════════════════

def test_sqlite_contention():
    """Hammer SQLite with concurrent writes to see if WAL handles it."""
    print("\n" + "=" * 70)
    print("  TEST 3: SQLite WAL contention (no Gemini)")
    print("=" * 70)

    N_WRITERS = 8
    N_WRITES = 50  # per writer
    errors = []
    lock = threading.Lock()
    timings = []

    def writer(idx):
        from src.data.connection_factory import get_connection
        local_errors = 0
        local_times = []

        for j in range(N_WRITES):
            t0 = time.time()
            try:
                conn = get_connection(str(DB_PATH))
                conn.execute(
                    "INSERT OR REPLACE INTO nlp_ticket_classifications "
                    "(scan_id, batch_id, ticket_id, sub_cluster, "
                    "sub_cluster_confidence, is_novel, sentiment_intensity, "
                    "sentiment_polarity, friction_type, anomaly_flag, "
                    "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))",
                    (f"diag-sqlite-{idx}", f"batch-{idx}",
                     f"DIAG-SQL-{idx}-{j}", "test_cluster",
                     0.9, 0, 3, "neutral", "other", "normal"),
                )
                conn.commit()
                conn.close()
                local_times.append(time.time() - t0)
            except Exception as e:
                local_errors += 1
                local_times.append(time.time() - t0)
                if local_errors <= 3:
                    logger.warning("Writer %d write %d failed: %s", idx, j, e)

        with lock:
            errors.append(local_errors)
            timings.extend(local_times)

    t_start = time.time()
    with ThreadPoolExecutor(max_workers=N_WRITERS) as pool:
        futures = [pool.submit(writer, i) for i in range(N_WRITERS)]
        for f in as_completed(futures, timeout=60):
            f.result()
    total = time.time() - t_start

    total_errors = sum(errors)
    total_writes = N_WRITERS * N_WRITES

    timings.sort()
    p50 = timings[len(timings) // 2] if timings else 0
    p95 = timings[int(len(timings) * 0.95)] if timings else 0
    p99 = timings[int(len(timings) * 0.99)] if timings else 0

    print(f"  Writers:      {N_WRITERS}")
    print(f"  Writes/each:  {N_WRITES}")
    print(f"  Total writes: {total_writes}")
    print(f"  Errors:       {total_errors} ({total_errors/total_writes*100:.1f}%)")
    print(f"  Total time:   {total:.1f}s")
    print(f"  Throughput:   {total_writes/total:.0f} writes/s")
    print(f"  Latency p50:  {p50*1000:.1f}ms")
    print(f"  Latency p95:  {p95*1000:.1f}ms")
    print(f"  Latency p99:  {p99*1000:.1f}ms")

    # Cleanup
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("DELETE FROM nlp_ticket_classifications WHERE scan_id LIKE 'diag-sqlite-%'")
    conn.commit()
    conn.close()
    print("  Cleaned up diagnostic rows")

    return {"errors": total_errors, "throughput": total_writes / total,
            "p50_ms": p50 * 1000, "p95_ms": p95 * 1000}


# ═══════════════════════════════════════════════════════════════════
# TEST 4: Raw stdout capture — what does the CLI actually send?
# ═══════════════════════════════════════════════════════════════════

def test_raw_capture():
    """Boot one bridge with raw stdout capture to see every byte the CLI sends."""
    from src.agents.acp_bridge import ACPBridge

    print("\n" + "=" * 70)
    print("  TEST 4: Raw CLI stdout capture")
    print("=" * 70)

    bridge = ACPBridge(model="gemini-2.5-flash")
    bridge.set_mcp_config([{
        "name": "alma-tools",
        "command": sys.executable,
        "args": ["-m", "src.mcp.alma_mcp_server"],
        "env": [],
    }])

    # Intercept the reader thread to capture raw lines
    raw_lines = []
    raw_lock = threading.Lock()

    original_route = bridge._route_message
    def _capturing_route(data):
        with raw_lock:
            raw_lines.append({
                "t": time.time(),
                "has_id": "id" in data,
                "has_method": "method" in data,
                "method": data.get("method", ""),
                "id": data.get("id", ""),
                "keys": list(data.keys()),
                "preview": json.dumps(data, default=str)[:200],
            })
        original_route(data)
    bridge._route_message = _capturing_route

    try:
        bridge.ensure_running()
        sid = bridge.new_session(mcp_env={"ALMA_DB_PATH": str(DB_PATH)})
        print(f"  Bridge alive, session={sid}")
        print(f"  Sending simple classification prompt...")

        t0 = time.time()
        result = bridge.call_streaming(
            SIMPLE_PROMPT.replace("DIAG-001", "DIAG-RAW-001"),
            "diag_raw_001",
            timeout=120,
        )
        elapsed = time.time() - t0

        print(f"\n  Elapsed: {elapsed:.1f}s")
        print(f"  Error: {result.get('error')}")
        print(f"  Text: {len(result.get('full_text', ''))} chars")
        print(f"  Raw messages captured: {len(raw_lines)}")

        print(f"\n  Message timeline:")
        t_base = raw_lines[0]["t"] if raw_lines else t0
        for i, msg in enumerate(raw_lines):
            dt = msg["t"] - t_base
            print(f"    +{dt:6.1f}s  id={str(msg['id']):<6} "
                  f"method={msg['method']:<30} keys={msg['keys']}")

        # Count message types
        method_counts = {}
        for msg in raw_lines:
            key = msg["method"] or f"response(id={msg['id']})"
            method_counts[key] = method_counts.get(key, 0) + 1
        print(f"\n  Message type counts: {method_counts}")

        # Show permission request details
        perm_msgs = [m for m in raw_lines if m["method"] == "session/request_permission"]
        if perm_msgs:
            print(f"\n  Permission requests ({len(perm_msgs)}):")
            for p in perm_msgs[:5]:
                print(f"    {p['preview']}")

        return {"elapsed": elapsed, "messages": len(raw_lines),
                "method_counts": method_counts}

    except Exception as e:
        print(f"  EXCEPTION: {e}")
        import traceback
        traceback.print_exc()
        return {"error": str(e)}
    finally:
        # Cleanup
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute("DELETE FROM nlp_ticket_classifications WHERE ticket_id LIKE 'DIAG-RAW-%'")
        conn.commit()
        conn.close()
        bridge.shutdown()


# ═══════════════════════════════════════════════════════════════════

def cleanup_diag_data():
    """Remove all diagnostic test data from DB."""
    try:
        conn = sqlite3.connect(str(DB_PATH), timeout=10)
        conn.execute("PRAGMA busy_timeout = 10000")
        conn.execute("DELETE FROM nlp_ticket_classifications WHERE ticket_id LIKE 'DIAG-%'")
        conn.commit()
        conn.close()
    except sqlite3.OperationalError as e:
        print(f"  [WARN] cleanup failed (non-fatal): {e}")


def main():
    args = _parse_args()

    if not DB_PATH.exists():
        print(f"[ABORT] Database not found: {DB_PATH}")
        return 1

    # Clean up any prior diagnostic data
    cleanup_diag_data()

    if args.test in ("all", "single"):
        test_single_bridge()

    if args.test in ("all", "raw"):
        test_raw_capture()

    if args.test in ("all", "sqlite"):
        test_sqlite_contention()

    if args.test in ("all", "concurrency"):
        test_concurrency()

    # Final cleanup
    cleanup_diag_data()

    print("\n" + "=" * 70)
    print("  DIAGNOSTICS COMPLETE")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
