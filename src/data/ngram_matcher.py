"""
Alma Insights — N-gram Matcher
Provisional sub-pattern classification using stored n-gram fingerprints.
Runs between Gemini scans to provide near-real-time sub-pattern tracking.
"""

import json
import logging
import re
from datetime import datetime
from collections import defaultdict

logger = logging.getLogger("alma.ngram_matcher")

# Minimum match score to classify (below this → unmatched)
DEFAULT_MATCH_THRESHOLD = 0.3


class NgramMatcher:

    def __init__(self, db, match_threshold=None):
        self.db = db
        self.threshold = match_threshold or DEFAULT_MATCH_THRESHOLD
        self._cache = {}  # trc → {pattern_id → [(ngram, weight)]}

    def classify_ticket(self, ticket_id, trc, text):
        """
        Score ticket text against all active sub-patterns for this TRC.
        Returns (matched_pattern_id, match_score) or (None, 0.0).
        """
        if not text or not trc:
            return None, 0.0

        pattern_ngrams = self._load_ngrams_for_trc(trc)
        if not pattern_ngrams:
            return None, 0.0

        text_lower = text.lower()
        best_pattern = None
        best_score = 0.0

        for pattern_id, ngrams in pattern_ngrams.items():
            score = self._score_text(text_lower, ngrams)
            if score > best_score:
                best_score = score
                best_pattern = pattern_id

        if best_score < self.threshold:
            return None, best_score

        return best_pattern, best_score

    def classify_batch(self, ticket_ids):
        """
        Classify multiple tickets. Stores results to
        provisional_classifications table.
        """
        if not ticket_ids:
            return

        now = datetime.now().isoformat()
        classified = 0
        unmatched = 0

        for tid in ticket_ids:
            # Get ticket data
            conv = self.db.get_conversation(tid)
            if not conv:
                continue

            trc = conv.get("trc_code", "")
            if not trc:
                continue

            # Check if active sub-patterns exist for this TRC
            pattern_ngrams = self._load_ngrams_for_trc(trc)
            if not pattern_ngrams:
                continue

            text = conv.get("full_thread", "") or conv.get("subject", "")
            pattern_id, score = self.classify_ticket(tid, trc, text)

            # Store provisional classification
            self.db.conn.execute("""
                INSERT OR REPLACE INTO provisional_classifications
                    (ticket_id, trc, matched_pattern_id, match_score,
                     match_method, is_confirmed, created_at)
                VALUES (?, ?, ?, ?, 'ngram', 0, ?)
            """, (tid, trc, pattern_id, score, now))

            if pattern_id:
                classified += 1
            else:
                unmatched += 1

        self.db.commit()
        logger.info(
            f"N-gram batch: {classified} classified, {unmatched} unmatched "
            f"out of {len(ticket_ids)} tickets"
        )

    def _load_ngrams_for_trc(self, trc):
        """
        Load active sub-patterns + their n-grams for a TRC.
        Cache in memory for batch processing.
        Only load n-grams with specificity > 0.1.
        """
        if trc in self._cache:
            return self._cache[trc]

        patterns = self.db.get_active_sub_patterns(trc)
        if not patterns:
            self._cache[trc] = {}
            return {}

        pattern_ngrams = {}
        for p in patterns:
            pid = p["pattern_id"]
            ngrams = self.db.get_sub_pattern_ngrams(pid, min_specificity=0.1)
            if ngrams:
                pattern_ngrams[pid] = [
                    (ng["ngram"], ng["frequency"] * ng["specificity"])
                    for ng in ngrams
                ]

        self._cache[trc] = pattern_ngrams
        return pattern_ngrams

    def _score_text(self, text_lower, pattern_ngrams):
        """
        Check which n-grams from the pattern appear in the text.
        Weight by frequency × specificity.
        Normalize: score / max_possible_score.
        """
        if not pattern_ngrams:
            return 0.0

        matched_weight = 0.0
        total_weight = 0.0

        for ngram, weight in pattern_ngrams:
            total_weight += weight
            if ngram in text_lower:
                matched_weight += weight

        if total_weight == 0:
            return 0.0

        return matched_weight / total_weight

    def clear_cache(self):
        """Clear the TRC n-gram cache (call after scan updates patterns)."""
        self._cache.clear()
