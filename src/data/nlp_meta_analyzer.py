"""
Alma Insights — NLP Meta-Analyzer (Layer 2)
Runs after Gemini scan completes. Aggregates classifications, manages
the learning sub-taxonomy, extracts n-grams, cross-references statistical
engines, and generates ranked findings.

HIPAA A5: N-grams are redacted before storage.
HIPAA A7: Provisional classification pruning after analysis.
"""

import json
import logging
import uuid
from datetime import datetime, timedelta
from collections import defaultdict, Counter
from difflib import SequenceMatcher

logger = logging.getLogger("alma.nlp_meta")


class NLPMetaAnalyzer:

    def __init__(self, db):
        self.db = db

    def run_analysis(self, scan_id):
        """
        Main entry. Called when scan status = 'scan_complete'.
        """
        logger.info(f"Starting meta-analysis for scan {scan_id}")
        self.db.conn.execute(
            "UPDATE nlp_scan_runs SET status = 'analyzing' WHERE scan_id = ?",
            (scan_id,)
        )
        self.db.commit()

        try:
            # 1. Within-TRC analysis
            trcs = self._get_scan_trcs(scan_id)
            for trc in trcs:
                self._analyze_within_trc(scan_id, trc)

            # 2. Update sub-taxonomy (tier lifecycle)
            self._update_sub_taxonomy(scan_id)

            # 3. Update n-grams (dual-source)
            self._update_ngrams(scan_id)

            # 4. Create snapshots
            self._create_snapshots(scan_id)

            # 5. Cross-TRC analysis
            self._analyze_cross_trc(scan_id)

            # 6. Cross-reference statistical engines
            findings = self.db.conn.execute(
                "SELECT * FROM nlp_findings WHERE scan_id = ?", (scan_id,)
            ).fetchall()
            for f in findings:
                self._cross_reference_engines(dict(f), scan_id)

            # 7. Rank findings by impact
            self._rank_findings(scan_id)

            # 8. Select exemplar tickets
            self._select_exemplars_for_findings(scan_id)

            # HIPAA A7: Prune old provisional classifications
            self._prune_provisional(scan_id)

            # Done
            self.db.conn.execute(
                "UPDATE nlp_scan_runs SET status = 'analysis_complete' WHERE scan_id = ?",
                (scan_id,)
            )
            self.db.commit()
            logger.info(f"Meta-analysis complete for scan {scan_id}")

        except Exception as e:
            logger.error(f"Meta-analysis failed: {e}")
            self.db.conn.execute(
                "UPDATE nlp_scan_runs SET status = 'failed' WHERE scan_id = ?",
                (scan_id,)
            )
            self.db.commit()
            raise

    # ─── Helper: get TRCs in scan ───

    def _get_scan_trcs(self, scan_id):
        rows = self.db.conn.execute(
            "SELECT DISTINCT trc FROM nlp_ticket_classifications WHERE scan_id = ?",
            (scan_id,)
        ).fetchall()
        return [r[0] for r in rows]

    # ═══════════════════════════════════════════
    #  WITHIN-TRC ANALYSIS
    # ═══════════════════════════════════════════

    def _analyze_within_trc(self, scan_id, trc):
        """Group by sub_cluster within TRC, compute stats, create findings."""
        rows = self.db.conn.execute("""
            SELECT sub_cluster, COUNT(*) AS cnt,
                   AVG(sentiment_intensity) AS avg_sent,
                   SUM(CASE WHEN anomaly_flag = 'critical' THEN 1 ELSE 0 END) AS critical_cnt,
                   SUM(CASE WHEN anomaly_flag = 'unusual' THEN 1 ELSE 0 END) AS unusual_cnt,
                   SUM(CASE WHEN is_novel = 1 THEN 1 ELSE 0 END) AS novel_cnt
            FROM nlp_ticket_classifications
            WHERE scan_id = ? AND trc = ?
            GROUP BY sub_cluster
            ORDER BY cnt DESC
        """, (scan_id, trc)).fetchall()

        total_tickets = sum(r[1] for r in rows)
        if total_tickets == 0:
            return

        # Get total for this TRC in the scan date range
        scan_row = self.db.conn.execute(
            "SELECT * FROM nlp_scan_runs WHERE scan_id = ?", (scan_id,)
        ).fetchone()

        for row in rows:
            sub_cluster = row[0]
            cnt = row[1]
            avg_sent = row[2]
            critical_cnt = row[3]
            unusual_cnt = row[4]
            pct_of_trc = cnt / total_tickets if total_tickets > 0 else 0

            # Get dominant friction type
            friction_row = self.db.conn.execute("""
                SELECT friction_type, COUNT(*) AS fcnt
                FROM nlp_ticket_classifications
                WHERE scan_id = ? AND trc = ? AND sub_cluster = ?
                GROUP BY friction_type
                ORDER BY fcnt DESC LIMIT 1
            """, (scan_id, trc, sub_cluster)).fetchone()
            dominant_friction = friction_row[0] if friction_row else 'other'

            # Create within_trc finding if significant
            if cnt >= 5 or critical_cnt > 0:
                anomaly_pct = (critical_cnt + unusual_cnt) / cnt if cnt > 0 else 0
                temporal_trend = 'new' if pct_of_trc > 0 else 'stable'

                finding_id = str(uuid.uuid4())
                self.db.conn.execute("""
                    INSERT INTO nlp_findings
                        (finding_id, scan_id, finding_type, scope, title,
                         description, ticket_count, pct_of_scanned,
                         avg_sentiment_intensity, dominant_friction_type,
                         top_trcs, top_sub_patterns, temporal_trend,
                         impact_score, created_at)
                    VALUES (?, ?, 'within_trc', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0.0, ?)
                """, (
                    finding_id, scan_id,
                    f'TRC::{trc}',
                    f'{sub_cluster} ({trc})',
                    f'Sub-pattern "{sub_cluster}" in TRC {trc}: {cnt} tickets, '
                    f'{avg_sent:.1f} avg sentiment, {dominant_friction} friction',
                    cnt, pct_of_trc,
                    avg_sent, dominant_friction,
                    json.dumps([trc]),
                    json.dumps([sub_cluster]),
                    temporal_trend,
                    datetime.now().isoformat(),
                ))

        self.db.commit()

    # ═══════════════════════════════════════════
    #  SUB-TAXONOMY MANAGEMENT
    # ═══════════════════════════════════════════

    def _update_sub_taxonomy(self, scan_id):
        """
        Tier lifecycle management after scan.
        """
        now = datetime.now().isoformat()

        # 1. Get all (trc, sub_cluster) pairs from this scan
        pairs = self.db.conn.execute("""
            SELECT trc, sub_cluster, COUNT(*) AS cnt,
                   SUM(CASE WHEN is_novel = 1 THEN 1 ELSE 0 END) AS novel_cnt
            FROM nlp_ticket_classifications
            WHERE scan_id = ?
            GROUP BY trc, sub_cluster
        """, (scan_id,)).fetchall()

        # Warn about legacy JSON-array TRC values (mixed-batch bug)
        bad_trcs = [r for r in pairs if r[0] and r[0].startswith('[')]
        if bad_trcs:
            logger.warning(
                f"Found {len(bad_trcs)} classification groups with "
                f"JSON-array TRC values (mixed-batch bug). These will "
                f"group incorrectly. Re-run scan after TRC propagation fix."
            )

        for row in pairs:
            trc = row[0]
            sub_cluster = row[1]
            cnt = row[2]
            novel_cnt = row[3]

            if not sub_cluster:
                continue

            # Check if pattern already exists
            existing = self.db.conn.execute(
                "SELECT * FROM sub_patterns WHERE trc = ? AND label = ?",
                (trc, sub_cluster)
            ).fetchone()

            if existing:
                existing = dict(existing)
                # Update existing pattern
                new_lifetime = existing["lifetime_tickets"] + cnt
                new_scans = existing["lifetime_scans"] + 1
                self.db.conn.execute("""
                    UPDATE sub_patterns
                    SET lifetime_tickets = ?, lifetime_scans = ?,
                        last_seen_scan = ?, last_seen_at = ?
                    WHERE pattern_id = ?
                """, (new_lifetime, new_scans, scan_id, now,
                      existing["pattern_id"]))
            else:
                # Check for fuzzy match against dormant/retired patterns
                reactivated = self._try_reactivate(trc, sub_cluster, scan_id, cnt, now)
                if not reactivated:
                    # Create new probationary pattern
                    pattern_id = str(uuid.uuid4())

                    # Determine dominant friction type
                    friction_row = self.db.conn.execute("""
                        SELECT friction_type, COUNT(*) AS fcnt
                        FROM nlp_ticket_classifications
                        WHERE scan_id = ? AND trc = ? AND sub_cluster = ?
                        GROUP BY friction_type
                        ORDER BY fcnt DESC LIMIT 1
                    """, (scan_id, trc, sub_cluster)).fetchone()
                    friction = friction_row[0] if friction_row else 'other'

                    self.db.conn.execute("""
                        INSERT INTO sub_patterns
                            (pattern_id, trc, label, description, friction_type,
                             tier, discovered_scan, discovered_at,
                             last_seen_scan, last_seen_at,
                             lifetime_tickets, lifetime_scans)
                        VALUES (?, ?, ?, ?, ?, 'probationary', ?, ?, ?, ?, ?, 1)
                    """, (
                        pattern_id, trc, sub_cluster,
                        f"Discovered in scan {scan_id[:8]}",
                        friction, scan_id, now, scan_id, now, cnt,
                    ))

        self.db.commit()

        # 2. Tier promotion/demotion
        self._promote_patterns(scan_id)
        self._demote_patterns(scan_id)
        self._retire_patterns(scan_id)

    def _try_reactivate(self, trc, label, scan_id, cnt, now):
        """Check if a novel label fuzzy-matches a dormant/retired pattern."""
        dormant = self.db.conn.execute("""
            SELECT * FROM sub_patterns
            WHERE trc = ? AND tier IN ('dormant', 'retired')
              AND merged_into IS NULL
        """, (trc,)).fetchall()

        for pat in dormant:
            pat = dict(pat)
            similarity = SequenceMatcher(
                None, label.lower(), pat["label"].lower()
            ).ratio()
            if similarity > 0.8:
                # Reactivate
                new_lifetime = pat["lifetime_tickets"] + cnt
                new_scans = pat["lifetime_scans"] + 1
                self.db.conn.execute("""
                    UPDATE sub_patterns
                    SET tier = 'active', lifetime_tickets = ?,
                        lifetime_scans = ?, last_seen_scan = ?,
                        last_seen_at = ?
                    WHERE pattern_id = ?
                """, (new_lifetime, new_scans, scan_id, now,
                      pat["pattern_id"]))
                logger.info(
                    f"Reactivated dormant pattern '{pat['label']}' "
                    f"(matched '{label}', similarity={similarity:.2f})"
                )
                return True
        return False

    def _promote_patterns(self, scan_id):
        """Probationary -> active: 2+ scans AND 10+ lifetime tickets."""
        self.db.conn.execute("""
            UPDATE sub_patterns
            SET tier = 'active'
            WHERE tier = 'probationary'
              AND lifetime_scans >= 2
              AND lifetime_tickets >= 10
              AND merged_into IS NULL
        """)
        self.db.commit()

    def _demote_patterns(self, scan_id):
        """Active -> dormant: < 10 tickets in last 3 scans."""
        # Get last 3 scan IDs
        recent_scans = self.db.conn.execute("""
            SELECT scan_id FROM nlp_scan_runs
            WHERE status = 'analysis_complete'
            ORDER BY created_at DESC LIMIT 3
        """).fetchall()
        recent_ids = [r[0] for r in recent_scans]

        if len(recent_ids) < 3:
            return  # not enough scan history

        active_patterns = self.db.conn.execute("""
            SELECT pattern_id, trc, label FROM sub_patterns
            WHERE tier = 'active' AND merged_into IS NULL
        """).fetchall()

        placeholders = ','.join(['?'] * len(recent_ids))
        for pat in active_patterns:
            pat = dict(pat)
            row = self.db.conn.execute(f"""
                SELECT COALESCE(SUM(ticket_count), 0) AS recent_tickets
                FROM sub_pattern_snapshots
                WHERE pattern_id = ? AND scan_id IN ({placeholders})
            """, [pat["pattern_id"]] + recent_ids).fetchone()

            if row[0] < 10:
                self.db.conn.execute(
                    "UPDATE sub_patterns SET tier = 'dormant' WHERE pattern_id = ?",
                    (pat["pattern_id"],)
                )
                logger.info(f"Demoted to dormant: '{pat['label']}'")

        self.db.commit()

    def _retire_patterns(self, scan_id):
        """
        Probationary -> retired: not confirmed in 2 scans after discovery.
        Dormant -> retired: < 5 lifetime AND not seen in 180 days.
        """
        # Retire unconfirmed probationary patterns
        completed_scans = self.db.conn.execute("""
            SELECT COUNT(*) FROM nlp_scan_runs
            WHERE status = 'analysis_complete'
        """).fetchone()[0]

        if completed_scans >= 3:
            # Probationary patterns discovered 2+ scans ago with only 1 scan
            self.db.conn.execute("""
                UPDATE sub_patterns
                SET tier = 'retired'
                WHERE tier = 'probationary'
                  AND lifetime_scans <= 1
                  AND merged_into IS NULL
                  AND discovered_scan != ?
                  AND pattern_id NOT IN (
                      SELECT DISTINCT sp.pattern_id
                      FROM sub_patterns sp
                      JOIN sub_pattern_snapshots sps ON sp.pattern_id = sps.pattern_id
                      WHERE sps.scan_id = ?
                  )
            """, (scan_id, scan_id))

        # Retire old dormant patterns
        cutoff_date = (datetime.now() - timedelta(days=180)).isoformat()
        self.db.conn.execute("""
            UPDATE sub_patterns
            SET tier = 'retired'
            WHERE tier = 'dormant'
              AND lifetime_tickets < 5
              AND (last_seen_at IS NULL OR last_seen_at < ?)
              AND merged_into IS NULL
        """, (cutoff_date,))

        self.db.commit()

    # ═══════════════════════════════════════════
    #  N-GRAM EXTRACTION
    # ═══════════════════════════════════════════

    def _update_ngrams(self, scan_id):
        """
        Dual-source n-gram extraction.
        HIPAA A5: All n-grams redacted before storage.
        """
        from src.gemini.gemini_client import _load_redaction_config
        import re

        redaction_config = _load_redaction_config()
        now = datetime.now().isoformat()

        # Get all sub-patterns that appeared in this scan
        patterns = self.db.conn.execute("""
            SELECT DISTINCT sp.pattern_id, sp.trc, sp.label
            FROM sub_patterns sp
            JOIN nlp_ticket_classifications tc
              ON sp.trc = tc.trc AND sp.label = tc.sub_cluster
            WHERE tc.scan_id = ? AND sp.tier IN ('active', 'probationary')
              AND sp.merged_into IS NULL
        """, (scan_id,)).fetchall()

        for pat in patterns:
            pat = dict(pat)
            pattern_id = pat["pattern_id"]
            trc = pat["trc"]

            # SOURCE 1: Gemini key_phrases
            phrase_rows = self.db.conn.execute("""
                SELECT key_phrases FROM nlp_ticket_classifications
                WHERE scan_id = ? AND trc = ? AND sub_cluster = ?
                  AND key_phrases IS NOT NULL
            """, (scan_id, trc, pat["label"])).fetchall()

            phrase_counter = Counter()
            for pr in phrase_rows:
                try:
                    phrases = json.loads(pr[0])
                    for p in phrases:
                        cleaned = self._redact_ngram(p, redaction_config)
                        if cleaned and not self._is_pure_redaction_token(cleaned):
                            phrase_counter[cleaned.lower()] += 1
                except (json.JSONDecodeError, TypeError):
                    pass

            for phrase, freq in phrase_counter.items():
                n = len(phrase.split())
                self._upsert_ngram(pattern_id, trc, phrase, n, 'gemini',
                                   freq, freq, now)

            # SOURCE 2: TF-IDF on ticket text
            self._extract_tfidf_ngrams(scan_id, pattern_id, trc,
                                       pat["label"], redaction_config, now)

        # Recompute specificity for all n-grams per TRC
        trc_list = list(set(dict(p)["trc"] for p in patterns))
        for trc in trc_list:
            self._recompute_specificity(trc)

        # Prune old n-grams (not seen in 90 days)
        cutoff = (datetime.now() - timedelta(days=90)).isoformat()
        self.db.conn.execute(
            "DELETE FROM sub_pattern_ngrams WHERE last_seen < ?",
            (cutoff,)
        )

        self.db.commit()

    def _redact_ngram(self, text, config):
        """Apply redaction to an n-gram before storage (HIPAA A5)."""
        import re
        for pat in config.get("patterns", []):
            if pat.get("conditional"):
                continue
            flags = re.IGNORECASE if pat.get("flags") == "i" else 0
            text = re.sub(pat["regex"], pat["replace"], text, flags=flags)
        return text.strip()

    def _is_pure_redaction_token(self, text):
        """Check if n-gram is entirely redaction tokens (no analytical value)."""
        import re
        cleaned = re.sub(r'\[[\w]+\]', '', text).strip()
        return len(cleaned) == 0

    def _upsert_ngram(self, pattern_id, trc, ngram, n, source, freq,
                      ticket_count, now):
        """Insert or update an n-gram."""
        existing = self.db.conn.execute(
            "SELECT ngram_id, frequency FROM sub_pattern_ngrams "
            "WHERE pattern_id = ? AND ngram = ?",
            (pattern_id, ngram)
        ).fetchone()

        if existing:
            self.db.conn.execute("""
                UPDATE sub_pattern_ngrams
                SET frequency = frequency + ?, ticket_count = ticket_count + ?,
                    last_seen = ?, source = CASE WHEN source != ? THEN 'both' ELSE source END
                WHERE ngram_id = ?
            """, (freq, ticket_count, now, source, existing[0]))
        else:
            self.db.conn.execute("""
                INSERT INTO sub_pattern_ngrams
                    (pattern_id, trc, ngram, n, source, frequency,
                     ticket_count, first_seen, last_seen, specificity)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0.0)
            """, (pattern_id, trc, ngram, n, source, freq,
                  ticket_count, now, now))

    def _extract_tfidf_ngrams(self, scan_id, pattern_id, trc, label,
                              redaction_config, now):
        """Extract TF-IDF n-grams from ticket text for a sub-pattern."""
        try:
            from sklearn.feature_extraction.text import TfidfVectorizer
        except ImportError:
            logger.warning("sklearn not available, skipping TF-IDF n-grams")
            return

        # Get redacted ticket text for this sub-pattern
        rows = self.db.conn.execute("""
            SELECT tc.summary
            FROM nlp_ticket_classifications tc
            WHERE tc.scan_id = ? AND tc.trc = ? AND tc.sub_cluster = ?
              AND tc.summary IS NOT NULL
        """, (scan_id, trc, label)).fetchall()

        texts = [r[0] for r in rows if r[0]]
        if len(texts) < 3:
            return  # not enough text for meaningful TF-IDF

        try:
            vectorizer = TfidfVectorizer(
                ngram_range=(1, 3),
                max_features=30,
                stop_words='english',
                min_df=2,
            )
            tfidf_matrix = vectorizer.fit_transform(texts)
            feature_names = vectorizer.get_feature_names_out()

            # Get top 20 by mean TF-IDF score
            mean_scores = tfidf_matrix.mean(axis=0).A1
            top_indices = mean_scores.argsort()[::-1][:20]

            for idx in top_indices:
                ngram = feature_names[idx]
                redacted = self._redact_ngram(ngram, redaction_config)
                if redacted and not self._is_pure_redaction_token(redacted):
                    n = len(redacted.split())
                    self._upsert_ngram(
                        pattern_id, trc, redacted.lower(), n, 'tfidf',
                        1, len(texts), now
                    )
        except Exception as e:
            logger.warning(f"TF-IDF extraction failed for {label}: {e}")

    def _recompute_specificity(self, trc):
        """Recompute specificity scores for all n-grams in a TRC."""
        # Count active patterns in TRC
        active_count_row = self.db.conn.execute("""
            SELECT COUNT(*) FROM sub_patterns
            WHERE trc = ? AND tier IN ('active', 'probationary')
              AND merged_into IS NULL
        """, (trc,)).fetchone()
        active_count = active_count_row[0]
        if active_count <= 1:
            # All n-grams have max specificity when only 1 pattern
            self.db.conn.execute("""
                UPDATE sub_pattern_ngrams SET specificity = 1.0
                WHERE trc = ?
            """, (trc,))
            return

        # For each n-gram, count how many patterns in this TRC contain it
        ngrams = self.db.conn.execute(
            "SELECT DISTINCT ngram FROM sub_pattern_ngrams WHERE trc = ?",
            (trc,)
        ).fetchall()

        for ng_row in ngrams:
            ngram = ng_row[0]
            containing = self.db.conn.execute("""
                SELECT COUNT(DISTINCT pattern_id) FROM sub_pattern_ngrams
                WHERE trc = ? AND ngram = ?
            """, (trc, ngram)).fetchone()[0]
            specificity = 1 - (containing / active_count)
            self.db.conn.execute("""
                UPDATE sub_pattern_ngrams
                SET specificity = ?
                WHERE trc = ? AND ngram = ?
            """, (specificity, trc, ngram))

        self.db.commit()

    # ═══════════════════════════════════════════
    #  SNAPSHOTS
    # ═══════════════════════════════════════════

    def _create_snapshots(self, scan_id):
        """One snapshot per sub-pattern per scan."""
        scan_row = self.db.conn.execute(
            "SELECT * FROM nlp_scan_runs WHERE scan_id = ?", (scan_id,)
        ).fetchone()
        scan_row = dict(scan_row)

        patterns = self.db.conn.execute("""
            SELECT DISTINCT sp.pattern_id, sp.trc, sp.label
            FROM sub_patterns sp
            JOIN nlp_ticket_classifications tc
              ON sp.trc = tc.trc AND sp.label = tc.sub_cluster
            WHERE tc.scan_id = ? AND sp.merged_into IS NULL
        """, (scan_id,)).fetchall()

        for pat in patterns:
            pat = dict(pat)
            pid = pat["pattern_id"]
            trc = pat["trc"]

            # Ticket count for this pattern in this scan
            count_row = self.db.conn.execute("""
                SELECT COUNT(*) AS cnt,
                       AVG(sentiment_intensity) AS avg_sent,
                       SUM(CASE WHEN is_novel = 1 THEN 1 ELSE 0 END) AS novel_cnt
                FROM nlp_ticket_classifications
                WHERE scan_id = ? AND trc = ? AND sub_cluster = ?
            """, (scan_id, trc, pat["label"])).fetchone()

            ticket_count = count_row[0]
            avg_sentiment = count_row[1]
            novel_tickets = count_row[2]

            # Total tickets for parent TRC
            total_row = self.db.conn.execute("""
                SELECT COUNT(*) FROM nlp_ticket_classifications
                WHERE scan_id = ? AND trc = ?
            """, (scan_id, trc)).fetchone()
            total_trc = total_row[0]
            pct_of_trc = ticket_count / total_trc if total_trc > 0 else 0

            # Sentiment distribution
            sent_dist_rows = self.db.conn.execute("""
                SELECT sentiment_polarity, COUNT(*) AS cnt
                FROM nlp_ticket_classifications
                WHERE scan_id = ? AND trc = ? AND sub_cluster = ?
                GROUP BY sentiment_polarity
            """, (scan_id, trc, pat["label"])).fetchall()
            sent_dist = {r[0]: r[1] for r in sent_dist_rows}

            # Friction distribution
            fric_dist_rows = self.db.conn.execute("""
                SELECT friction_type, COUNT(*) AS cnt
                FROM nlp_ticket_classifications
                WHERE scan_id = ? AND trc = ? AND sub_cluster = ?
                GROUP BY friction_type
            """, (scan_id, trc, pat["label"])).fetchall()
            fric_dist = {r[0]: r[1] for r in fric_dist_rows}

            # Top entities
            entity_rows = self.db.conn.execute("""
                SELECT entities_json FROM nlp_ticket_classifications
                WHERE scan_id = ? AND trc = ? AND sub_cluster = ?
                  AND entities_json IS NOT NULL
            """, (scan_id, trc, pat["label"])).fetchall()

            payer_counter = Counter()
            product_counter = Counter()
            for er in entity_rows:
                try:
                    entities = json.loads(er[0])
                    if entities.get("payer"):
                        payer_counter[entities["payer"]] += 1
                    if entities.get("product_area"):
                        product_counter[entities["product_area"]] += 1
                except (json.JSONDecodeError, TypeError):
                    pass

            top_entities = {
                "payers": [p for p, _ in payer_counter.most_common(5)],
                "products": [p for p, _ in product_counter.most_common(5)],
            }

            # N-grams added this scan
            ngram_count = self.db.conn.execute("""
                SELECT COUNT(*) FROM sub_pattern_ngrams
                WHERE pattern_id = ? AND first_seen >= ?
            """, (pid, scan_row["created_at"])).fetchone()[0]

            # Insert snapshot
            self.db.conn.execute("""
                INSERT OR REPLACE INTO sub_pattern_snapshots
                    (pattern_id, scan_id, scan_date_start, scan_date_end,
                     ticket_count, pct_of_trc, avg_sentiment,
                     sentiment_dist, friction_dist, top_entities,
                     novel_tickets, new_ngrams_added)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                pid, scan_id,
                scan_row["date_range_start"], scan_row["date_range_end"],
                ticket_count, pct_of_trc, avg_sentiment,
                json.dumps(sent_dist), json.dumps(fric_dist),
                json.dumps(top_entities),
                novel_tickets, ngram_count,
            ))

        self.db.commit()

    # ═══════════════════════════════════════════
    #  CROSS-TRC ANALYSIS
    # ═══════════════════════════════════════════

    def _analyze_cross_trc(self, scan_id):
        """Find patterns spanning TRCs."""
        # Entity concentration across TRCs
        entity_rows = self.db.conn.execute("""
            SELECT entities_json, trc FROM nlp_ticket_classifications
            WHERE scan_id = ? AND entities_json IS NOT NULL
        """, (scan_id,)).fetchall()

        payer_trc_map = defaultdict(set)
        payer_counts = Counter()
        for er in entity_rows:
            try:
                entities = json.loads(er[0])
                payer = entities.get("payer")
                if payer:
                    payer_trc_map[payer].add(er[1])
                    payer_counts[payer] += 1
            except (json.JSONDecodeError, TypeError):
                pass

        # Create cross-TRC findings for payers appearing in 3+ TRCs
        for payer, trcs in payer_trc_map.items():
            if len(trcs) >= 3:
                finding_id = str(uuid.uuid4())
                self.db.conn.execute("""
                    INSERT INTO nlp_findings
                        (finding_id, scan_id, finding_type, scope, title,
                         description, ticket_count, top_trcs, top_entities,
                         temporal_trend, impact_score, created_at)
                    VALUES (?, ?, 'cross_trc', 'CROSS_TRC', ?, ?, ?, ?, ?, 'stable', 0.0, ?)
                """, (
                    finding_id, scan_id,
                    f'Entity "{payer}" spans {len(trcs)} TRCs',
                    f'Payer "{payer}" appears across {len(trcs)} TRCs with '
                    f'{payer_counts[payer]} total tickets',
                    payer_counts[payer],
                    json.dumps(list(trcs)),
                    json.dumps({"payer": payer, "trc_count": len(trcs)}),
                    datetime.now().isoformat(),
                ))

        # Friction pattern across TRCs
        friction_rows = self.db.conn.execute("""
            SELECT friction_type, trc, COUNT(*) AS cnt
            FROM nlp_ticket_classifications
            WHERE scan_id = ?
            GROUP BY friction_type, trc
        """, (scan_id,)).fetchall()

        friction_trc_map = defaultdict(set)
        friction_counts = Counter()
        for fr in friction_rows:
            friction_trc_map[fr[0]].add(fr[1])
            friction_counts[fr[0]] += fr[2]

        for friction, trcs in friction_trc_map.items():
            if len(trcs) >= 3 and friction not in ('other', 'positive_feedback'):
                finding_id = str(uuid.uuid4())
                self.db.conn.execute("""
                    INSERT INTO nlp_findings
                        (finding_id, scan_id, finding_type, scope, title,
                         description, ticket_count, dominant_friction_type,
                         top_trcs, temporal_trend, impact_score, created_at)
                    VALUES (?, ?, 'cross_trc', 'CROSS_TRC', ?, ?, ?, ?, ?, 'stable', 0.0, ?)
                """, (
                    finding_id, scan_id,
                    f'Systemic "{friction}" friction across {len(trcs)} TRCs',
                    f'Friction type "{friction}" is dominant across {len(trcs)} TRCs',
                    friction_counts[friction],
                    friction,
                    json.dumps(list(trcs)[:10]),
                    datetime.now().isoformat(),
                ))

        self.db.commit()

    # ═══════════════════════════════════════════
    #  STATISTICAL ENGINE CROSS-REFERENCE
    # ═══════════════════════════════════════════

    def _cross_reference_engines(self, finding, scan_id):
        """Cross-reference a finding with existing statistical engines."""
        validation = {}

        top_trcs = []
        if finding.get("top_trcs"):
            try:
                top_trcs = json.loads(finding["top_trcs"])
            except (json.JSONDecodeError, TypeError):
                pass

        # Poisson incident flags
        if top_trcs:
            poisson_flags = []
            for trc in top_trcs[:5]:
                flags = self.db.conn.execute("""
                    SELECT * FROM incident_flags
                    WHERE trc_code = ? AND status = 'open'
                """, (trc,)).fetchall()
                poisson_flags.extend([dict(f) for f in flags])
            validation['poisson'] = {
                'flagged': len(poisson_flags) > 0,
                'count': len(poisson_flags),
            }

        # Theta anomaly flags
        if top_trcs:
            theta_flags = []
            for trc in top_trcs[:5]:
                flags = self.db.conn.execute("""
                    SELECT * FROM anomaly_flags
                    WHERE trc_code = ? AND status = 'open'
                """, (trc,)).fetchall()
                theta_flags.extend([dict(f) for f in flags])
            validation['theta'] = {
                'flagged': len(theta_flags) > 0,
                'count': len(theta_flags),
            }

        # Update finding with validation
        self.db.conn.execute("""
            UPDATE nlp_findings
            SET statistical_validation = ?
            WHERE finding_id = ?
        """, (json.dumps(validation), finding["finding_id"]))
        self.db.commit()

    # ═══════════════════════════════════════════
    #  IMPACT SCORING & RANKING
    # ═══════════════════════════════════════════

    def _compute_impact_score(self, finding):
        """
        impact = pct_of_scanned × avg_sentiment_intensity ×
                 urgency_prevalence × temporal_acceleration
        """
        pct = finding.get("pct_of_scanned") or 0
        sent = finding.get("avg_sentiment_intensity") or 1
        ticket_count = finding.get("ticket_count") or 0

        # Base score from volume and sentiment
        score = pct * sent * (1 + ticket_count / 100)

        # Boost for critical findings
        if finding.get("dominant_friction_type") in (
            'escalation_demand', 'repeat_contact'
        ):
            score *= 1.5

        return score

    def _rank_findings(self, scan_id):
        """Compute impact scores and rank all findings."""
        findings = self.db.conn.execute(
            "SELECT * FROM nlp_findings WHERE scan_id = ?", (scan_id,)
        ).fetchall()

        for f in findings:
            f = dict(f)
            score = self._compute_impact_score(f)
            self.db.conn.execute(
                "UPDATE nlp_findings SET impact_score = ? WHERE finding_id = ?",
                (score, f["finding_id"])
            )
        self.db.commit()

    def _select_exemplars_for_findings(self, scan_id, max_n=10):
        """Select exemplar ticket IDs for top findings."""
        findings = self.db.conn.execute("""
            SELECT * FROM nlp_findings
            WHERE scan_id = ?
            ORDER BY impact_score DESC LIMIT 20
        """, (scan_id,)).fetchall()

        for f in findings:
            f = dict(f)
            top_trcs = []
            try:
                top_trcs = json.loads(f.get("top_trcs") or "[]")
            except (json.JSONDecodeError, TypeError):
                pass

            if not top_trcs:
                continue

            # Get exemplar tickets: highest anomaly, highest sentiment
            placeholders = ','.join(['?'] * len(top_trcs))
            exemplars = self.db.conn.execute(f"""
                SELECT ticket_id FROM nlp_ticket_classifications
                WHERE scan_id = ? AND trc IN ({placeholders})
                ORDER BY
                    CASE anomaly_flag
                        WHEN 'critical' THEN 3
                        WHEN 'unusual' THEN 2
                        ELSE 1
                    END DESC,
                    sentiment_intensity DESC
                LIMIT ?
            """, [scan_id] + top_trcs + [max_n]).fetchall()

            ticket_ids = [r[0] for r in exemplars]
            self.db.conn.execute("""
                UPDATE nlp_findings
                SET exemplar_ticket_ids = ?
                WHERE finding_id = ?
            """, (json.dumps(ticket_ids), f["finding_id"]))

        self.db.commit()

    # ═══════════════════════════════════════════
    #  HIPAA A7: PROVISIONAL CLASSIFICATION PRUNING
    # ═══════════════════════════════════════════

    def _prune_provisional(self, scan_id):
        """
        Delete provisional classifications that have been confirmed
        by a Gemini scan and are older than 30 days.
        """
        self.db.conn.execute("""
            DELETE FROM provisional_classifications
            WHERE confirmed_by_scan IS NOT NULL
              AND created_at < date('now', '-30 days')
        """)
        self.db.commit()
