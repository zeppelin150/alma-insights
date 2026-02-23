"""
Alma Insights -- Dynamic Batch Packer (Pass 5.2)

Sizes batches to maximize payload per bridge call.
Model-adaptive: auto-adjusts output budget and max batch size
based on the selected Gemini model's output token limit.

Learns optimal batch size per TRC from measured output characteristics.
Persists learned profiles to trc_batch_profiles table.
"""

import logging
from datetime import datetime

logger = logging.getLogger("alma.batch_packer")

# ── Model-specific output limits (Build Spec 5.2) ──
MODEL_OUTPUT_LIMITS = {
    "gemini-2.0-flash":  20_000,    # ~8K output tokens ≈ 27K chars
    "gemini-2.5-flash": 200_000,    # ~65K output tokens ≈ 260K chars
    "gemini-2.5-pro":   200_000,    # ~65K output tokens ≈ 260K chars
}
MODEL_MAX_BATCH = {
    "gemini-2.0-flash":  25,
    "gemini-2.5-flash": 100,
    "gemini-2.5-pro":   100,
}
DEFAULT_OUTPUT_BUDGET = 20_000      # fallback for unknown models

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
        logger.info(
            f"BatchPacker: model={self._model}, "
            f"budget={self._output_budget}, max_batch={self._max_batch}"
        )
        self._trc_profiles = {}   # trc -> measured chars_per_ticket
        self._load_profiles()

    def compute_batch_size(self, trc, ticket_count):
        """
        How many tickets should go in one API call for this TRC?

        Uses measured output size from previous scans.
        Falls back to conservative default for unknown TRCs.

        Args:
            trc: TRC code (or comma-joined for mixed batches)
            ticket_count: total tickets available for this TRC

        Returns:
            Optimal batch size (int), clamped to [MIN, MAX]
        """
        chars_per_ticket = self._trc_profiles.get(trc, DEFAULT_CHARS_PER_TICKET)

        # How many tickets fit in the output budget?
        if chars_per_ticket > 0:
            optimal = int(self._output_budget / chars_per_ticket)
        else:
            optimal = self._max_batch

        # Clamp to bounds (model-adaptive max)
        optimal = max(MIN_BATCH_SIZE, min(self._max_batch, optimal))

        # Don't exceed actual ticket count
        return min(optimal, ticket_count)

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
                import sqlite3
                conn = sqlite3.connect(self._db_path)
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
