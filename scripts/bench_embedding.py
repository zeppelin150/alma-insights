"""Benchmark Qwen3 embedding throughput with different settings.

Pulls 100 real tickets from the gate DB, then encodes them under several
configs to identify the cheapest win.
"""
from __future__ import annotations

import sqlite3
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

import numpy as np

DB = _ROOT / "data" / "phase3_gate_test.db"


def _load_texts(n: int = 100) -> list[str]:
    """Pull raw-body texts matching what Phase 2 would compose."""
    conn = sqlite3.connect(str(DB))
    rows = conn.execute(
        """SELECT c.subject, c.full_thread
             FROM ticket_index ti
             JOIN conversations c ON c.ticket_id = ti.ticket_id
            LIMIT ?""", (n,)).fetchall()
    conn.close()
    out = []
    for subj, body in rows:
        blocks = (body or "").split("\n\n---\n\n")[:3]
        txt = ((subj or "") + "\n\n" + "\n\n".join(blocks)).strip()
        out.append(txt[:2048])
    return out


def _bench(label: str, texts: list[str], *, batch_size: int,
           max_seq_length: int | None = None, truncate_chars: int | None = None):
    from src.data.embedding.model_loader import get_model
    model = get_model()
    if max_seq_length is not None:
        model.max_seq_length = max_seq_length
    if truncate_chars:
        texts = [t[:truncate_chars] for t in texts]
    # Warmup
    model.encode(texts[:2], batch_size=2, show_progress_bar=False)
    t0 = time.perf_counter()
    emb = model.encode(texts, batch_size=batch_size, show_progress_bar=False,
                       normalize_embeddings=True)
    dt = time.perf_counter() - t0
    print(f"  {label:50s}  {dt:6.2f}s  ({dt/len(texts)*1000:6.1f} ms/ticket)")
    return emb, dt


def main():
    texts = _load_texts(100)
    avg_chars = int(np.mean([len(t) for t in texts]))
    print(f"Loaded {len(texts)} real tickets, avg {avg_chars} chars")
    print()

    # Import model once to warm disk cache
    from src.data.embedding.model_loader import get_model
    get_model()

    print("Config                                                 time    per-ticket")
    print("-" * 78)
    base, _ = _bench("BASELINE bs=32, max_seq=default",   texts, batch_size=32)
    _bench("bs=32, max_seq=512",                         texts, batch_size=32, max_seq_length=512)
    _bench("bs=32, max_seq=256",                         texts, batch_size=32, max_seq_length=256)
    _bench("bs=64, max_seq=512",                         texts, batch_size=64, max_seq_length=512)
    _bench("bs=128, max_seq=512",                        texts, batch_size=128, max_seq_length=512)
    _bench("bs=32, max_seq=512, trunc=512",              texts, batch_size=32, max_seq_length=512, truncate_chars=512)
    fast, _ = _bench("bs=64, max_seq=256, trunc=768",    texts, batch_size=64, max_seq_length=256, truncate_chars=768)
    print()
    # Semantic similarity check: does max_seq=256 change results meaningfully?
    if base is not None and fast is not None:
        # Average cos-sim of each ticket's baseline vs. fast embedding
        sims = np.sum(base * fast, axis=1)
        print(f"Baseline-vs-fast avg cosine: {float(sims.mean()):.4f} (1.0 = identical)")
        print(f"Baseline-vs-fast min cosine: {float(sims.min()):.4f}")


if __name__ == "__main__":
    main()
