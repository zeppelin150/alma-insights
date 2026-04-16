"""
Alma Insights -- Dynamic Batch Packer (Pass 5.4)

Dual-constraint batch sizing: output budget AND input budget.
Model-adaptive: auto-adjusts based on the selected Gemini model.

Learns optimal batch size per TRC from measured output characteristics.
Persists learned profiles to trc_batch_profiles table.
"""

from __future__ import annotations

import logging
from datetime import datetime

from src.data.connection_factory import get_connection

logger = logging.getLogger("alma.batch_packer")

# ── Model-specific output limits (Build Spec 5.2) ──
MODEL_OUTPUT_LIMITS = {
    "gemini-2.0-flash":       20_000,    # ~8K output tokens ≈ 27K chars
    "gemini-2.5-flash":      200_000,    # ~65K output tokens ≈ 260K chars
    "gemini-2.5-flash-lite": 200_000,    # ~65K output tokens (same as 2.5-flash)
    "gemini-2.5-pro":        200_000,    # ~65K output tokens ≈ 260K chars
    "gemini-3-flash-preview":      200_000,
    "gemini-3.1-flash-lite-preview": 200_000,
    "gemini-3.1-pro-preview":      200_000,
}
MODEL_MAX_BATCH = {
    "gemini-2.0-flash":       25,
    "gemini-2.5-flash":       45,    # was 75 — reduced further to stay under 150s stall threshold
    "gemini-2.5-flash-lite":  45,    # aligned with 2.5-flash cap
    "gemini-2.5-pro":         60,    # was 100 — pro is faster but still benefits from smaller batches
    "gemini-3-flash-preview":       45,
    "gemini-3.1-flash-lite-preview": 45,
    "gemini-3.1-pro-preview":       60,
}
DEFAULT_OUTPUT_BUDGET = 20_000      # fallback for unknown models

# ── Model-specific input limits (Build Spec 5.4) ──
# Max chars for the ticket-content portion of the prompt.
# Conservative vs full context windows — prevents stalls on long threads.
MODEL_INPUT_LIMITS = {
    "gemini-2.0-flash":      100_000,   # 1M context, but output is real constraint
    "gemini-2.5-flash":      300_000,   # 1M context, generous budget
    "gemini-2.5-flash-lite": 250_000,   # slightly conservative
    "gemini-2.5-pro":        400_000,   # largest effective budget
    "gemini-3-flash-preview":      300_000,
    "gemini-3.1-flash-lite-preview": 300_000,
    "gemini-3.1-pro-preview":      400_000,
}
DEFAULT_INPUT_BUDGET = 100_000

# Per-ticket overhead in the JSONL section (JSON wrapper, ticket_id field, etc.)
JSON_OVERHEAD_PER_TICKET = 120

# Thread truncation limit (MUST match worker_agent._build_prompt line 572)
THREAD_TRUNCATION_LIMIT = 3000

# Fixed prompt overhead: template (~2K) + stats (~2K) + taxonomy (~15K) + instructions
PROMPT_OVERHEAD_CHARS = 20_000

# ── Default estimates (before real data) ──
DEFAULT_CHARS_PER_TICKET = 800   # measured: ~700 chars per classification JSON
MIN_BATCH_SIZE = 5

# ── EMA smoothing factor ──
EMA_ALPHA = 0.3   # weight for new measurement (0.3 new, 0.7 old)


class BatchPacker:
    """
    Dynamic batch sizing with per-TRC learning.

    Uses measured output size from previous scans to compute optimal
    batch size for each TRC. Falls back to conservative defaults
    for unknown TRCs.
    """

    def __init__(self, db_connection, model=None):
        self.db = db_connection
        # Store the path for thread-safe writes
        self._db_path = db_connection.execute(
            "PRAGMA database_list"
        ).fetchone()[2] if db_connection else None
        # Model-adaptive limits (5.2)
        self._model = model or "gemini-2.0-flash"
        self._output_budget = MODEL_OUTPUT_LIMITS.get(
            self._model, DEFAULT_OUTPUT_BUDGET
        )
        self._max_batch = MODEL_MAX_BATCH.get(self._model, 25)
        self._input_budget = MODEL_INPUT_LIMITS.get(
            self._model, DEFAULT_INPUT_BUDGET
        )
        logger.info(
            f"BatchPacker: model={self._model}, "
            f"output_budget={self._output_budget}, "
            f"input_budget={self._input_budget}, max_batch={self._max_batch}"
        )
        self._trc_profiles = {}   # trc -> measured chars_per_ticket
        self._load_profiles()

    def compute_batch_size(self, trc, ticket_count, avg_thread_chars=0):
        """
        Dual-constraint batch sizing: output budget AND input budget.

        Uses measured output size from previous scans + per-TRC average
        thread length to compute the tighter of the two limits.

        Args:
            trc: TRC code (or comma-joined for mixed batches)
            ticket_count: total tickets available for this TRC
            avg_thread_chars: AVG(LENGTH(full_thread)) for this TRC
                (0 = unknown, uses worst-case THREAD_TRUNCATION_LIMIT)

        Returns:
            Optimal batch size (int), clamped to [MIN, MAX]
        """
        # ── Output constraint (existing) ──
        chars_per_ticket = self._trc_profiles.get(trc, DEFAULT_CHARS_PER_TICKET)
        if chars_per_ticket > 0:
            output_limit = int(self._output_budget / chars_per_ticket)
        else:
            output_limit = self._max_batch

        # ── Input constraint (5.4) ──
        if avg_thread_chars > 0:
            effective_thread = min(avg_thread_chars, THREAD_TRUNCATION_LIMIT)
        else:
            # Unknown thread length: assume worst case
            effective_thread = THREAD_TRUNCATION_LIMIT

        chars_per_ticket_input = effective_thread + JSON_OVERHEAD_PER_TICKET
        input_limit = int(
            self._input_budget / chars_per_ticket_input
        ) if chars_per_ticket_input > 0 else self._max_batch

        # Take the tighter constraint, clamp to bounds
        optimal = min(output_limit, input_limit)
        optimal = max(MIN_BATCH_SIZE, min(self._max_batch, optimal))

        logger.debug(
            f"BatchPacker: {trc[:40]} size={min(optimal, ticket_count)} "
            f"(out={output_limit}, in={input_limit}, cap={self._max_batch}, "
            f"avg_thread={avg_thread_chars:.0f})"
        )

        return min(optimal, ticket_count)

    def get_input_budget(self):
        """Return input char budget (for mixed-batch packing)."""
        return self._input_budget

    def record_result(self, trc, ticket_count, output_chars):
        """
        After a batch completes, record the actual output ratio.
        This feeds back into sizing for the next batch.

        Uses exponential moving average so one bad batch doesn't
        permanently skew the estimate.

        Args:
            trc: TRC code
            ticket_count: tickets in this batch
            output_chars: total response chars from Gemini
        """
        if ticket_count <= 0:
            return

        measured = output_chars / ticket_count
        old = self._trc_profiles.get(trc, DEFAULT_CHARS_PER_TICKET)
        new_estimate = old * (1 - EMA_ALPHA) + measured * EMA_ALPHA
        self._trc_profiles[trc] = new_estimate
        self._save_profile(trc, new_estimate)

        logger.debug(
            f"BatchPacker: {trc} profile updated "
            f"{old:.0f} -> {new_estimate:.0f} chars/ticket "
            f"(measured {measured:.0f} from {ticket_count} tickets)"
        )

    def halve_for_retry(self, original_size):
        """
        After truncation, retry at half size.
        Respects MIN_BATCH_SIZE floor.
        """
        return max(MIN_BATCH_SIZE, original_size // 2)

    def get_profile(self, trc):
        """Return the current chars_per_ticket estimate for a TRC."""
        return self._trc_profiles.get(trc, DEFAULT_CHARS_PER_TICKET)

    def get_all_profiles(self):
        """Return all learned TRC profiles."""
        return dict(self._trc_profiles)

    def _load_profiles(self):
        """Load learned TRC profiles from SQLite."""
        try:
            rows = self.db.execute("""
                SELECT trc, avg_chars_per_ticket
                FROM trc_batch_profiles
            """).fetchall()
            for r in rows:
                self._trc_profiles[r[0]] = r[1]
            if self._trc_profiles:
                logger.info(
                    f"BatchPacker: loaded {len(self._trc_profiles)} "
                    f"TRC profiles from database"
                )
        except Exception:
            pass  # table doesn't exist yet -- will be created by db_manager

    def _save_profile(self, trc, chars_per_ticket):
        """Persist a TRC profile to SQLite (thread-safe)."""
        if self._db_path is None:
            return
        try:
            if self._db_path == ":memory:" or self._db_path == "":
                # In-memory DB — must use same connection
                self.db.execute("""
                    INSERT OR REPLACE INTO trc_batch_profiles
                        (trc, avg_chars_per_ticket, updated_at)
                    VALUES (?, ?, ?)
                """, (trc, chars_per_ticket, datetime.now().isoformat()))
                self.db.commit()
            else:
                # File-based DB — use thread-local connection
                conn = get_connection(self._db_path)
                conn.execute("""
                    INSERT OR REPLACE INTO trc_batch_profiles
                        (trc, avg_chars_per_ticket, updated_at)
                    VALUES (?, ?, ?)
                """, (trc, chars_per_ticket, datetime.now().isoformat()))
                conn.commit()
                conn.close()
        except Exception as e:
            logger.warning(f"BatchPacker: failed to save profile for {trc}: {e}")

    def __repr__(self):
        return (
            f"BatchPacker(model={self._model}, "
            f"budget={self._output_budget}, max={self._max_batch}, "
            f"profiles={len(self._trc_profiles)})"
        )
