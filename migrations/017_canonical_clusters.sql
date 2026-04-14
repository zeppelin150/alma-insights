-- Migration 017: Canonicalization Engine tables (Phase 3)
--
-- Adds the persistent canonical-cluster registry (canonical_clusters), the
-- tuning-harness results table (canonicalization_tuning_runs), and the
-- assignment columns on ticket_index that every downstream phase reads.
--
-- Columns follow canonicalization-enrichment.md §3.1. Centroids are stored as
-- BLOB of float32 (1024-dim, L2-normalized) to match Qwen3 embeddings.

-- ────────────────────────────────────────────────────────────────
-- 1. canonical_clusters — registry per (TRC, cluster)
-- ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS canonical_clusters (
    cluster_id              TEXT PRIMARY KEY,                 -- "{trc_slug}-{uuid4}"
    trc                     TEXT NOT NULL,
    canonical_label         TEXT,
    label_source            TEXT,                             -- 'medoid' | 'extractive' | 'llm' | 'existing_retained'
    label_version           INTEGER DEFAULT 1,
    centroid_blob           BLOB NOT NULL,                    -- float32 vector, L2-normalized
    representative_ticket_id TEXT,                            -- medoid ticket_id
    member_count            INTEGER DEFAULT 0,
    lifetime_tickets        INTEGER DEFAULT 0,
    lifetime_scans          INTEGER DEFAULT 0,
    tier                    TEXT DEFAULT 'probationary',      -- probationary | active | stable | dormant | retired | split
    discovered_scan_id      TEXT,
    first_seen_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_seen_scan_id       TEXT,
    last_seen_at            TIMESTAMP,
    merged_into             TEXT,                             -- self-FK for merge history
    split_into_json         TEXT,                             -- JSON array of child cluster_ids
    concept_id              TEXT,                             -- FK to canonical_concepts (Phase 7 — table may not exist yet)
    FOREIGN KEY (merged_into) REFERENCES canonical_clusters(cluster_id)
);
CREATE INDEX IF NOT EXISTS idx_cc_trc       ON canonical_clusters(trc);
CREATE INDEX IF NOT EXISTS idx_cc_tier      ON canonical_clusters(tier);
CREATE INDEX IF NOT EXISTS idx_cc_concept   ON canonical_clusters(concept_id);
CREATE INDEX IF NOT EXISTS idx_cc_last_scan ON canonical_clusters(last_seen_scan_id);

-- ────────────────────────────────────────────────────────────────
-- 2. ticket_index — assignment pointers
-- ────────────────────────────────────────────────────────────────
-- (Columns only added when absent; ALTER TABLE inside the migrator is
--  idempotent via the _ALTER_ADD_COL_RE pre-check.)
ALTER TABLE ticket_index ADD COLUMN canonical_issue_id TEXT;
ALTER TABLE ticket_index ADD COLUMN canonical_confidence REAL;
ALTER TABLE ticket_index ADD COLUMN assignment_method TEXT;     -- hdbscan_core | hdbscan_border | knn_fallback | unclustered | snapped_existing
ALTER TABLE ticket_index ADD COLUMN hdbscan_membership_prob REAL;
ALTER TABLE ticket_index ADD COLUMN canonicalized_at TIMESTAMP;

CREATE INDEX IF NOT EXISTS idx_ti_canonical    ON ticket_index(canonical_issue_id);
CREATE INDEX IF NOT EXISTS idx_ti_canon_method ON ticket_index(assignment_method);

-- ────────────────────────────────────────────────────────────────
-- 3. canonicalization_tuning_runs — grid-search audit
-- ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS canonicalization_tuning_runs (
    run_id              TEXT PRIMARY KEY,
    params_json         TEXT NOT NULL,
    silhouette          REAL,
    davies_bouldin      REAL,
    golden_precision    REAL,
    golden_recall       REAL,
    golden_f1           REAL,
    noise_pct           REAL,
    n_clusters          INTEGER,
    gemini_coherence    REAL,
    wall_time_ms        INTEGER,
    scan_id             TEXT,
    trc                 TEXT,                  -- NULL = whole-run, else per-TRC
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    notes               TEXT
);
CREATE INDEX IF NOT EXISTS idx_ctr_created ON canonicalization_tuning_runs(created_at);
CREATE INDEX IF NOT EXISTS idx_ctr_trc     ON canonicalization_tuning_runs(trc);
