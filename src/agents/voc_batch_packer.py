"""
Alma Insights -- VOC Batch Packer (Build 9.0 T2)

TRC-level bin-packing for batched VOC analysis.  Groups TRCs into batches
that fit within the model's input budget, with:
  - 25% headroom reserve (char-based budgeting ≠ tokens)
  - Oversized TRC chunking (split tickets into slices when a TRC exceeds budget)
  - Decreasing-first-fit heuristic for efficient packing

Imports MODEL_INPUT_LIMITS from batch_packer.py — single source of truth
for model limits.
"""

from __future__ import annotations

import logging
from math import ceil

from src.agents.batch_packer import MODEL_INPUT_LIMITS, DEFAULT_INPUT_BUDGET

logger = logging.getLogger("alma.voc_batch_packer")

# Overhead for the batch prompt template (instructions, delimiters, formatting)
BATCH_PROMPT_OVERHEAD = 25_000

# Overhead per TRC section within a batch (delimiter + metadata lines)
TRC_SECTION_OVERHEAD = 500

# Token headroom factor: we use 75% of stated char budget to account
# for char-based budgeting being an imperfect proxy for token budgeting
HEADROOM_FACTOR = 0.75


class VOCBatchPacker:
    """Greedy decreasing-first-fit bin-packing for TRC analysis batches.

    Given a list of (trc_code, input_chars) pairs, groups TRCs into batches
    that fit within the model's input budget.  TRCs that exceed the budget
    individually are split into multiple chunks via intra-TRC chunking.
    """

    def __init__(self, model="gemini-2.5-flash"):
        """
        Args:
            model: Gemini model name (used to look up input budget).
        """
        self._model = model
        raw_budget = MODEL_INPUT_LIMITS.get(model, DEFAULT_INPUT_BUDGET)
        self._input_budget = int(raw_budget * HEADROOM_FACTOR) - BATCH_PROMPT_OVERHEAD

        if self._input_budget < 10_000:
            logger.warning(
                "VOCBatchPacker: very small input budget %d (model=%s, raw=%d)",
                self._input_budget, model, raw_budget,
            )

        logger.info(
            "VOCBatchPacker: model=%s, raw_budget=%d, effective_budget=%d",
            model, raw_budget, self._input_budget,
        )

    @property
    def input_budget(self):
        """Effective input budget in chars (after headroom + overhead)."""
        return self._input_budget

    def pack_trcs(self, trc_sizes, trc_ticket_counts=None):
        """Greedy decreasing-first-fit bin-pack TRCs into batches.

        Args:
            trc_sizes: List of (trc_code, total_input_chars) tuples.
                       total_input_chars = ticket_jsonl + nlp_context +
                       stat_context + TRC_SECTION_OVERHEAD.
            trc_ticket_counts: Optional dict trc_code → ticket_count.
                               Required for intra-TRC chunking of oversized TRCs.

        Returns:
            List of batch specs, each a dict:
                {
                    "trcs": [trc_code, ...],      # TRCs in this batch
                    "chunk_info": {                 # Only for chunked TRCs
                        trc_code: (chunk_n, chunk_total),
                    }
                }

            For oversized TRCs that get chunked, multiple batch specs will
            contain the same trc_code with different chunk_info entries.
        """
        if not trc_sizes:
            return []

        trc_ticket_counts = trc_ticket_counts or {}

        # Separate oversized TRCs from normal ones
        normal = []
        oversized = []

        for trc, chars in trc_sizes:
            if chars <= self._input_budget:
                normal.append((trc, chars))
            else:
                oversized.append((trc, chars))

        batches = []

        # Handle oversized TRCs: intra-TRC chunking
        for trc, chars in oversized:
            n_chunks = ceil(chars / self._input_budget)
            ticket_count = trc_ticket_counts.get(trc, 0)

            if ticket_count == 0:
                logger.warning(
                    "VOCBatchPacker: oversized TRC %s (%d chars) has no "
                    "ticket count — creating single solo batch",
                    trc, chars,
                )
                batches.append({"trcs": [trc], "chunk_info": {}})
                continue

            logger.info(
                "VOCBatchPacker: oversized TRC %s (%d chars, %d tickets) "
                "-> %d chunks",
                trc, chars, ticket_count, n_chunks,
            )

            for chunk_n in range(n_chunks):
                batches.append({
                    "trcs": [trc],
                    "chunk_info": {trc: (chunk_n, n_chunks)},
                })

        # Sort normal TRCs by size descending (decreasing-first-fit)
        normal.sort(key=lambda x: x[1], reverse=True)

        # Greedy bin-packing: try to fit each TRC into existing batch,
        # otherwise start a new one
        bin_remaining = []  # List of (remaining_chars, batch_index)

        for trc, chars in normal:
            placed = False

            # Try to fit into an existing batch with enough room
            for i, (remaining, batch_idx) in enumerate(bin_remaining):
                if chars + TRC_SECTION_OVERHEAD <= remaining:
                    batches[batch_idx]["trcs"].append(trc)
                    bin_remaining[i] = (
                        remaining - chars - TRC_SECTION_OVERHEAD,
                        batch_idx,
                    )
                    placed = True
                    break

            if not placed:
                # Start a new batch
                new_idx = len(batches)
                batches.append({"trcs": [trc], "chunk_info": {}})
                bin_remaining.append((
                    self._input_budget - chars - TRC_SECTION_OVERHEAD,
                    new_idx,
                ))

        # Log summary
        solo_batches = sum(1 for b in batches if len(b["trcs"]) == 1
                          and not b["chunk_info"])
        multi_batches = sum(1 for b in batches if len(b["trcs"]) > 1)
        chunk_batches = sum(1 for b in batches if b["chunk_info"])
        total_trcs = sum(len(b["trcs"]) for b in batches)

        logger.info(
            "VOCBatchPacker: %d TRCs -> %d batches "
            "(%d multi, %d solo, %d chunked)",
            total_trcs, len(batches), multi_batches, solo_batches, chunk_batches,
        )

        return batches
