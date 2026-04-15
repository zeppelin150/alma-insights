-- Migration 019: Cluster merge audit log (Phase 3 over-splitting fix)
--
-- Records every centroid-merge event the canonicalization engine performs.
-- A merge fires when two new clusters' confidence-weighted centroids sit
-- closer than centroid_merge_threshold (default 0.85 cosine). The lower
-- HDBSCAN label number wins as the keeper; the higher is absorbed.
--
-- Joined to canonical_clusters by kept_cluster_id for forensics.
-- The absorbed cluster never gets persisted to canonical_clusters (it was
-- transient HDBSCAN output), so the event row IS the only record of it.
--
-- Plan reference: canonicalization-enrichment.md §3 (gate fix).

CREATE TABLE IF NOT EXISTS cluster_merge_events (
    event_id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id                        TEXT NOT NULL,
    trc                            TEXT,
    kept_cluster_id                TEXT NOT NULL,
    kept_member_count_before       INTEGER NOT NULL,
    kept_member_count_after        INTEGER NOT NULL,
    absorbed_member_count          INTEGER NOT NULL,
    cosine_at_merge                REAL NOT NULL,
    centroid_merge_threshold       REAL NOT NULL,
    merged_at                      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (kept_cluster_id)  REFERENCES canonical_clusters(cluster_id)
);

CREATE INDEX IF NOT EXISTS idx_cme_scan ON cluster_merge_events(scan_id);
CREATE INDEX IF NOT EXISTS idx_cme_kept ON cluster_merge_events(kept_cluster_id);
CREATE INDEX IF NOT EXISTS idx_cme_trc  ON cluster_merge_events(trc);
