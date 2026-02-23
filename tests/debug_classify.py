"""
Diagnostic test: Send a small batch through the classify pipeline
and print exactly what Gemini returns + what the parser does.
"""
import sys, os, json, time, sqlite3
# Add project root to path so `from src.agents...` imports work
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from pathlib import Path
from src.agents.worker_agent import WorkerAgent
from src.agents.gemini_bridge_wrapper import GeminiBridge
from src.agents.tool_registry import ToolRegistry
from src.agents.stream_parser import StreamParser, StreamEvent

DB_PATH = str(Path(__file__).parent.parent / "data" / "local_warehouse.db")

def get_model_from_settings():
    config_path = Path(__file__).parent.parent / "config" / "settings.yaml"
    try:
        import yaml
        with open(config_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        return cfg.get("gemini", {}).get("model", "gemini-2.5-flash")
    except Exception:
        return "gemini-2.5-flash"


def get_test_tickets(n=5):
    """Get a small batch of tickets from the DB."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT DISTINCT ticket_id, trc_code, full_thread
        FROM conversations
        WHERE trc_code = 'Provider payout rate dissatisfaction'
          AND created_at >= '2025-01-01' AND created_at <= '2025-03-12'
          AND full_thread IS NOT NULL AND full_thread != ''
        LIMIT ?
    """, (n,)).fetchall()
    conn.close()

    tickets = []
    for r in rows:
        tickets.append({
            "ticket_id": r["ticket_id"],
            "trc": r["trc_code"],
            "full_thread": r["full_thread"][:3000],
        })
    return tickets


def test_1_raw_bridge():
    """Test 1: Send raw prompt to bridge, capture full response text."""
    print("\n" + "="*70)
    print("TEST 1: Raw bridge call — capture full_text")
    print("="*70)

    model = get_model_from_settings()
    print(f"Model: {model}")

    tickets = get_test_tickets(3)
    print(f"Got {len(tickets)} test tickets:")
    for t in tickets:
        print(f"  {t['ticket_id']} | {t['trc']} | thread_len={len(t['full_thread'])}")

    if not tickets:
        print("ERROR: No tickets found!")
        return

    # Build a simple prompt
    ticket_jsonl = "\n".join(json.dumps({
        "ticket_id": t["ticket_id"],
        "full_thread": t["full_thread"],
    }, ensure_ascii=False) for t in tickets)

    trc = tickets[0]["trc"]
    prompt = f"""You are classifying support tickets for an RCM healthcare platform.
TRC: {trc}

TICKETS:
{ticket_jsonl}

For EACH ticket, output a JSON object:
{{
  "ticket_id": "<ticket_id>",
  "sub_cluster": "<behavioral sub-pattern, <8 words>",
  "sub_cluster_confidence": <0.0-1.0>,
  "is_novel": false,
  "sentiment_intensity": <1-5>,
  "sentiment_polarity": "<positive|negative|mixed|neutral>",
  "friction_type": "<one of: access_blocked, self_serve_failure, incorrect_charge, missing_information, policy_confusion, feature_broken, other>",
  "anomaly_flag": "normal",
  "anomaly_reason": null,
  "entities": {{"payer": null, "product_area": null, "feature": null}},
  "key_phrases": ["<phrase1>", "<phrase2>"],
  "root_cause_hint": "<one sentence hypothesis>",
  "summary": "<1-2 sentence de-identified summary>"
}}

OUTPUT FORMAT:
Respond with a JSON array containing one classification object per ticket.
Wrap the array in a ```json fenced code block.
Output ONLY the JSON array. No preamble, no explanation after.
"""

    print(f"\nPrompt length: {len(prompt)} chars")

    # Boot bridge
    bridge = GeminiBridge(model=model)
    bridge.ensure_running()
    print("Bridge booted.")

    # Collect streaming events
    content_chunks = []
    def on_token(event):
        if event.type == "content":
            delta = event.data.get("delta", "")
            content_chunks.append(delta)

    try:
        start = time.time()
        result = bridge.call_streaming(
            prompt, "diag_test_1",
            on_token=on_token,
            timeout=120,
        )
        elapsed = time.time() - start

        print(f"\nBridge call completed in {elapsed:.1f}s")
        print(f"Error: {result.get('error')}")
        print(f"Turns: {result.get('turns')}")
        print(f"full_text length: {len(result.get('full_text', ''))}")

        full_text = result.get("full_text", "")
        print(f"\n--- FULL TEXT (first 2000 chars) ---")
        print(full_text[:2000])
        print(f"--- END FULL TEXT ---")

        # Check what streaming collected
        streamed = "".join(content_chunks)
        print(f"\nStreamed content length: {len(streamed)}")
        if streamed != full_text:
            print("WARNING: streamed != full_text!")
            print(f"  streamed[:200] = {streamed[:200]!r}")
            print(f"  full_text[:200] = {full_text[:200]!r}")

    finally:
        bridge.shutdown()


def test_2_stream_parser():
    """Test 2: Feed the response through the StreamParser."""
    print("\n" + "="*70)
    print("TEST 2: Stream parser behavior")
    print("="*70)

    # Simulate what Gemini might return
    simulated_responses = [
        # Case A: ```json fenced block
        '```json\n[{"ticket_id": "T1", "sub_cluster": "test", "sub_cluster_confidence": 0.8}]\n```',
        # Case B: Raw JSON array
        '[{"ticket_id": "T1", "sub_cluster": "test", "sub_cluster_confidence": 0.8}]',
        # Case C: ```classification fenced block
        '```classification\n{"ticket_id": "T1", "sub_cluster": "test", "sub_cluster_confidence": 0.8}\n```',
        # Case D: Preamble + ```json
        'Here are the classifications:\n```json\n[{"ticket_id": "T1", "sub_cluster": "test"}]\n```',
    ]

    for i, resp in enumerate(simulated_responses):
        parser = StreamParser()
        events = list(parser.feed(resp))
        events += list(parser.flush())

        print(f"\nCase {chr(65+i)}: {resp[:60]}...")
        print(f"  Events: {len(events)}")
        for e in events:
            print(f"    {e.event_type.name}: data={e.data}, raw={e.raw[:100] if e.raw else None}")

        # Check if any CLASSIFICATIONs were found
        classifications = [e for e in events if e.event_type == StreamEvent.CLASSIFICATION]
        print(f"  CLASSIFICATIONs: {len(classifications)}")


def test_3_full_pipeline():
    """Test 3: Full pipeline through WorkerAgent.classify_batch()."""
    print("\n" + "="*70)
    print("TEST 3: Full worker pipeline (classify_batch)")
    print("="*70)

    model = get_model_from_settings()
    tickets = get_test_tickets(3)

    if not tickets:
        print("ERROR: No tickets found!")
        return

    # Boot bridge
    bridge = GeminiBridge(model=model)
    bridge.ensure_running()

    # Create worker
    worker = WorkerAgent("diag_worker_0", bridge, DB_PATH)

    scan_id = "diag_scan_001"
    batch_id = "diag_batch_001"
    trc = tickets[0]["trc"]

    payload = {
        "scan_id": scan_id,
        "batch_id": batch_id,
        "trc": trc,
        "tickets": tickets,
        "stats_context": "No anomalies detected.",
        "sub_taxonomy": "No existing sub-patterns.",
        "chunk_n": 1,
        "chunk_total": 1,
        "date_start": "2025-01-01",
        "date_end": "2025-03-12",
    }

    try:
        result = worker.classify_batch(payload)

        print(f"\nResult: {json.dumps(result, indent=2)}")

        # Check what got stored
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT ticket_id, sub_cluster, friction_type FROM nlp_ticket_classifications WHERE scan_id = ?",
            (scan_id,)
        ).fetchall()

        print(f"\nStored classifications: {len(rows)}")
        for r in rows:
            print(f"  {r['ticket_id']}: {r['sub_cluster']} | {r['friction_type']}")

        # Clean up diagnostic data
        conn.execute("DELETE FROM nlp_ticket_classifications WHERE scan_id = ?", (scan_id,))
        conn.commit()
        conn.close()

    finally:
        bridge.shutdown()


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO, format='%(name)s %(levelname)s: %(message)s')

    # Run test 2 first (no bridge needed)
    test_2_stream_parser()

    # Run test 1 (raw bridge call)
    test_1_raw_bridge()

    # Run test 3 (full pipeline)
    test_3_full_pipeline()

    print("\n\nDIAGNOSTIC COMPLETE.")
