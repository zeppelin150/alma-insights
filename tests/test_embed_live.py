"""
Live embedding test — builds embeddings from the `tickets` table
when `ticket_index` is empty. Does NOT modify any production code.

Usage:
    python tests/test_embed_live.py              # embed + search test
    python tests/test_embed_live.py --verify     # just verify existing embeddings
    python tests/test_embed_live.py --search "billing dispute"

This script:
  1. Reads tickets from the `tickets` table (PHI-free fields only)
  2. Computes embeddings via the new embedding engine
  3. Writes to `ticket_embeddings` table (migration 006)
  4. Runs a sample semantic search to verify
"""

import argparse
import hashlib
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Ensure project root on path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

DB_PATH = PROJECT_ROOT / "data" / "local_warehouse.db"


def embed_from_tickets(conn, force=False):
    """Build embeddings from the tickets table (fallback when ticket_index is empty)."""
    from src.data.embedding.model_loader import is_available, get_model, MODEL_NAME, EMBEDDING_DIM

    if not is_available():
        print("ERROR: Embedding model not available.")
        return 0

    print(f"Loading model...")
    model = get_model()
    if model is None:
        print("ERROR: Model failed to load.")
        return 0

    # Load tickets (PHI-free composite)
    rows = conn.execute(
        "SELECT ticket_id, subject, trc_code, trc_label, status "
        "FROM tickets"
    ).fetchall()
    print(f"Found {len(rows)} tickets in tickets table")

    if not rows:
        return 0

    # Check existing hashes unless force
    existing = {}
    if not force:
        try:
            existing = {
                r[0]: r[1] for r in
                conn.execute("SELECT ticket_id, source_text_hash FROM ticket_embeddings").fetchall()
            }
            print(f"  {len(existing)} existing embeddings found")
        except Exception:
            pass

    # Build texts and filter unchanged
    to_embed = []
    for r in rows:
        tid = r[0]
        text = _compose_text(r[1], r[2], r[3], r[4])
        text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
        if not force and existing.get(tid) == text_hash:
            continue
        to_embed.append((tid, text, text_hash))

    if not to_embed:
        print("All embeddings up to date.")
        return 0

    print(f"Embedding {len(to_embed)} tickets...")
    start = time.perf_counter()

    from src.data.embedding.encoder import embed_documents
    import numpy as np

    texts = [t[1] for t in to_embed]
    embeddings = embed_documents(texts, batch_size=32)

    # Write to ticket_embeddings
    now = datetime.now(timezone.utc).isoformat()
    for i, (tid, _text, text_hash) in enumerate(to_embed):
        vec = embeddings[i]
        blob = vec.astype(np.float32).tobytes()
        conn.execute(
            "INSERT OR REPLACE INTO ticket_embeddings "
            "(ticket_id, embedding_blob, source_text_hash, "
            "model_name, dim_size, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (tid, blob, text_hash, MODEL_NAME, EMBEDDING_DIM, now),
        )

    conn.commit()
    elapsed = time.perf_counter() - start
    print(f"Embedded {len(to_embed)} tickets in {elapsed:.1f}s")
    return len(to_embed)


def verify_embeddings(conn):
    """Check what's in ticket_embeddings."""
    try:
        row = conn.execute("SELECT COUNT(*), AVG(dim_size) FROM ticket_embeddings").fetchone()
        print(f"ticket_embeddings: {row[0]} rows, dim_size={int(row[1] or 0)}")
        sample = conn.execute(
            "SELECT ticket_id, model_name, dim_size, created_at "
            "FROM ticket_embeddings LIMIT 3"
        ).fetchall()
        for s in sample:
            print(f"  {s[0]}: model={s[1]}, dim={s[2]}, created={s[3]}")
        return row[0]
    except Exception as e:
        print(f"ERROR: {e}")
        return 0


def run_search(conn, query):
    """Run a semantic search against cached embeddings."""
    import numpy as np
    from src.data.embedding.encoder import embed_query
    from src.data.embedding.search import semantic_search, load_embedding_cache

    print(f"\nSearching for: \"{query}\"")
    embeddings, ticket_ids = load_embedding_cache(conn)
    if len(ticket_ids) == 0:
        print("No embeddings cached. Run without --verify first.")
        return

    print(f"Searching {len(ticket_ids)} embeddings...")
    q_vec = embed_query(query)
    results = semantic_search(q_vec, embeddings, ticket_ids, top_k=5)

    print(f"\nTop {len(results)} results:")
    for r in results:
        tid = r["ticket_id"]
        sim = r["similarity"]
        # Get ticket subject for display
        row = conn.execute(
            "SELECT subject, trc_code FROM tickets WHERE ticket_id = ?", (tid,)
        ).fetchone()
        subj = row[0][:80] if row else "?"
        trc = row[1] if row else "?"
        print(f"  {tid} (sim={sim:.3f}) [{trc}] {subj}")


def _compose_text(subject, trc_code, trc_label, status):
    """Build composite text from ticket fields."""
    parts = []
    if trc_code:
        parts.append(f"Category: {trc_code}")
    if trc_label:
        parts.append(f"Label: {trc_label}")
    if status:
        parts.append(f"Status: {status}")
    if subject:
        parts.append(subject)
    return ". ".join(parts) if parts else "No description"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Live embedding test")
    parser.add_argument("--verify", action="store_true", help="Just verify existing embeddings")
    parser.add_argument("--search", type=str, help="Run semantic search with this query")
    parser.add_argument("--force", action="store_true", help="Re-embed everything")
    args = parser.parse_args()

    conn = sqlite3.connect(str(DB_PATH))

    # Ensure ticket_embeddings table exists (migration 006)
    try:
        conn.execute("SELECT COUNT(*) FROM ticket_embeddings")
    except sqlite3.OperationalError:
        print("Running migration 006...")
        mig = (PROJECT_ROOT / "migrations" / "006_hybrid_chat.sql").read_text(encoding="utf-8")
        conn.executescript(mig)

    if args.verify:
        verify_embeddings(conn)
    elif args.search:
        verify_embeddings(conn)
        run_search(conn, args.search)
    else:
        count = embed_from_tickets(conn, force=args.force)
        verify_embeddings(conn)
        if count > 0:
            run_search(conn, "billing dispute")

    conn.close()
