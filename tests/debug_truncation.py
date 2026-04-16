"""
Diagnostic: Test batch sizes to find the output token truncation threshold.
"""
import sys, os, json, time, sqlite3, re
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from pathlib import Path
from src.agents.acp_bridge import ACPBridge as GeminiBridge  # ACP migration

DB_PATH = str(Path(__file__).parent.parent / "data" / "local_warehouse.db")

def get_model():
    config_path = Path(__file__).parent.parent / "config" / "settings.yaml"
    try:
        import yaml
        with open(config_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        return cfg.get("gemini", {}).get("model", "gemini-2.5-flash")
    except Exception:
        return "gemini-2.5-flash"


def get_tickets(n):
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
    return [{
        "ticket_id": r["ticket_id"],
        "trc": r["trc_code"],
        "full_thread": r["full_thread"][:2000],
    } for r in rows]


def build_prompt(tickets):
    trc = tickets[0]["trc"]
    ticket_jsonl = "\n".join(json.dumps({
        "ticket_id": t["ticket_id"],
        "full_thread": t["full_thread"],
    }, ensure_ascii=False) for t in tickets)

    return f"""You are classifying support tickets for an RCM healthcare platform.
TRC: {trc}

TICKETS:
{ticket_jsonl}

For EACH ticket, output a JSON object:
{{"ticket_id":"<id>","sub_cluster":"<pattern>","sub_cluster_confidence":<0-1>,"is_novel":false,"sentiment_intensity":<1-5>,"sentiment_polarity":"<pos|neg|mixed|neutral>","friction_type":"<type>","anomaly_flag":"normal","anomaly_reason":null,"entities":{{"payer":null,"product_area":null,"feature":null}},"key_phrases":["<p1>","<p2>"],"root_cause_hint":"<1 sentence>","summary":"<1-2 sentences>"}}

Respond with a JSON array. Wrap in ```json fenced block.
Output ONLY the JSON array. No preamble.
Classify ALL {len(tickets)} tickets.
"""


def try_parse(full_text):
    """Replicate the worker's parsing chain."""
    cleaned = full_text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.lstrip("`").lstrip("json").lstrip("\n")
        cleaned = cleaned.rstrip("`").rstrip("\n")
    cleaned = re.sub(r",\s*([\]}])", r"\1", cleaned)

    # Strategy 1: Full JSON
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, list):
            return "full_json", len(parsed), parsed
        return "full_json_single", 1, [parsed]
    except json.JSONDecodeError:
        pass

    # Strategy 2: Regex array extraction
    match = re.search(r"\[[\s\S]*\]", cleaned)
    if match:
        try:
            arr = re.sub(r",\s*([\]}])", r"\1", match.group())
            parsed = json.loads(arr)
            return "regex_array", len(parsed), parsed
        except json.JSONDecodeError:
            pass

    # Strategy 3: Flat object regex (current code)
    objects = []
    for m in re.finditer(r"\{[^{}]*\}", cleaned):
        try:
            obj = json.loads(m.group())
            if isinstance(obj, dict) and obj.get("ticket_id"):
                objects.append(obj)
        except json.JSONDecodeError:
            continue
    if objects:
        return "flat_regex", len(objects), objects

    # Strategy 4: Improved nested object extraction
    objects2 = extract_nested_objects(cleaned)
    if objects2:
        return "nested_regex", len(objects2), objects2

    return "FAILED", 0, []


def extract_nested_objects(text):
    """Extract JSON objects with nested braces."""
    objects = []
    i = 0
    while i < len(text):
        if text[i] == '{':
            depth = 0
            start = i
            for j in range(i, len(text)):
                if text[j] == '{':
                    depth += 1
                elif text[j] == '}':
                    depth -= 1
                    if depth == 0:
                        candidate = text[start:j+1]
                        try:
                            obj = json.loads(candidate)
                            if isinstance(obj, dict) and obj.get("ticket_id"):
                                objects.append(obj)
                        except json.JSONDecodeError:
                            pass
                        i = j + 1
                        break
            else:
                # Unmatched opening brace — try partial
                candidate = text[start:]
                # Try to fix truncated JSON
                candidate_fixed = candidate.rstrip()
                # Close any open strings and braces
                for _ in range(10):
                    try:
                        obj = json.loads(candidate_fixed + '"}')
                        if isinstance(obj, dict) and obj.get("ticket_id"):
                            objects.append(obj)
                        break
                    except json.JSONDecodeError:
                        candidate_fixed += '"}'
                break
        else:
            i += 1
    return objects


def main():
    model = get_model()
    print(f"Model: {model}")

    bridge = GeminiBridge(model=model)
    bridge.ensure_running()

    for batch_size in [10, 20, 40]:
        tickets = get_tickets(batch_size)
        actual_n = len(tickets)
        prompt = build_prompt(tickets)

        print(f"\n{'='*60}")
        print(f"Batch size: {actual_n} tickets | Prompt: {len(prompt)} chars")

        start = time.time()
        result = bridge.call_streaming(prompt, f"trunc_test_{batch_size}", timeout=120)
        elapsed = time.time() - start

        full_text = result.get("full_text", "")
        error = result.get("error")

        print(f"  Time: {elapsed:.1f}s | Error: {error}")
        print(f"  Response length: {len(full_text)} chars")

        # Check if truncated
        is_complete = full_text.rstrip().endswith("```") or full_text.rstrip().endswith("]")
        print(f"  Looks complete: {is_complete}")
        print(f"  Last 100 chars: {full_text[-100:]!r}")

        # Parse
        strategy, count, items = try_parse(full_text)
        print(f"  Parse strategy: {strategy}")
        print(f"  Parsed items: {count}/{actual_n}")

        if count < actual_n:
            print(f"  ** TRUNCATED! Only {count}/{actual_n} parsed **")

    bridge.shutdown()
    print("\nDone.")


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.WARNING)
    main()
