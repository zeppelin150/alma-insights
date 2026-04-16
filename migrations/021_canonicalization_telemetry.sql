-- Migration 021: Canonicalization telemetry + per-scan regression (Phase 6)
--
-- Tracks drift (centroid movement + member churn), fission (cluster splits),
-- and dormancy/resurrection for each canonical_clusters row. Also stores
-- per-scan Gemini regression verdicts.
--
-- Stages are observational: enabling them changes nothing about assignment
-- quality; they only record what happened so Phase 8's integrity report has
-- ground truth to reason over.
--
-- Plan reference: canonicalization-enrichment.md §6 (rewritten in kernel
--                 phase-6-9-session-kernel.md as 021_…, since 020 was taken
--                 by canonical_concepts in Phase 7).

-- ────────────────────────────────────────────────────────────────
-- 1. cluster_drift_events — one row per (cluster_id, scan_id)
-- ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS cluster_drift_events (
    event_id              TEXT PRIMARY KEY,
    cluster_id            TEXT NOT NULL,
    scan_id               TEXT NOT NULL,
    prev_scan_id          TEXT,                        -- last scan this cluster was measured
    centroid_before_blob  BLOB,                        -- pre-scan centroid
    centroid_after_blob   BLOB,                        -- post-scan centroid
    drift_cosine          REAL,                        -- 1 - cos(before, after), i.e. cosine distance
    members_added         INTEGER DEFAULT 0,
    members_lost          INTEGER DEFAULT 0,
    members_retained      INTEGER DEFAULT 0,
    member_count_before   INTEGER,
    member_count_after    INTEGER,
    variance_before       REAL,                        -- mean cosine distance from centroid, pre-scan
    variance_after        REAL,
    silhouette_before     REAL,                        -- mean silhouette sample across members
    silhouette_after      REAL,
    stability_band        TEXT,                        -- 'stable' | 'normal' | 'drifting' | 'unstable'
    created_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (cluster_id) REFERENCES canonical_clusters(cluster_id)
);
CREATE INDEX IF NOT EXISTS idx_cde_scan    ON cluster_drift_events(scan_id);
CREATE INDEX IF NOT EXISTS idx_cde_cluster ON cluster_drift_events(cluster_id);
CREATE INDEX IF NOT EXISTS idx_cde_band    ON cluster_drift_events(stability_band);


-- ────────────────────────────────────────────────────────────────
-- 2. cluster_fission_events — one row per fission CANDIDATE (committed or rejected)
-- ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS cluster_fission_events (
    event_id                  TEXT PRIMARY KEY,
    parent_cluster_id         TEXT NOT NULL,
    scan_id                   TEXT NOT NULL,
    triggered_by              TEXT,                    -- 'variance_threshold' | 'silhouette_drop' | 'member_count_doubled'
    child_cluster_ids_json    TEXT,                    -- JSON array of new cluster_ids (committed only)
    member_count_parent       INTEGER,
    tickets_reassigned_count  INTEGER DEFAULT 0,
    variance_before           REAL,
    silhouette_before         REAL,
    silhouette_after          REAL,                    -- silhouette after proposed split (from sub-HDBSCAN)
    silhouette_improvement    REAL,
    llm_gate_score            REAL,                    -- 1-5 from fission_gate.txt
    llm_gate_reasoning        TEXT,
    committed                 BOOLEAN DEFAULT 0,       -- 1 if split was actually executed
    human_approved            BOOLEAN,                 -- populated by HITL queue (NULL = not yet reviewed)
    created_at                TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (parent_cluster_id) REFERENCES canonical_clusters(cluster_id)
);
CREATE INDEX IF NOT EXISTS idx_cfe_scan      ON cluster_fission_events(scan_id);
CREATE INDEX IF NOT EXISTS idx_cfe_parent    ON cluster_fission_events(parent_cluster_id);
CREATE INDEX IF NOT EXISTS idx_cfe_committed ON cluster_fission_events(committed);


-- ────────────────────────────────────────────────────────────────
-- 3. dormancy_events — tier transitions active↔dormant↔retired↔active
-- ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS dormancy_events (
    event_id                          TEXT PRIMARY KEY,
    cluster_id                        TEXT NOT NULL,
    scan_id                           TEXT NOT NULL,       -- scan at which transition fired
    tier_transition                   TEXT NOT NULL,       -- 'active->dormant' | 'dormant->retired' | 'dormant->active' | 'retired->active'
    scans_without_members             INTEGER,             -- consecutive empty scans at transition time
    resurrecting_ticket_ids_json      TEXT,                -- populated for '*->active' transitions
    avg_resurrection_cosine           REAL,                -- mean cosine of new members to centroid
    confirmation_gate_passed          BOOLEAN,             -- True if resurrection met the ≥3 members rule
    human_reviewed_false_positive     BOOLEAN DEFAULT 0,
    created_at                        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (cluster_id) REFERENCES canonical_clusters(cluster_id)
);
CREATE INDEX IF NOT EXISTS idx_de_scan    ON dormancy_events(scan_id);
CREATE INDEX IF NOT EXISTS idx_de_cluster ON dormancy_events(cluster_id);
CREATE INDEX IF NOT EXISTS idx_de_trans   ON dormancy_events(tier_transition);


-- ────────────────────────────────────────────────────────────────
-- 4. cluster_regression_reports — per-scan Gemini verdicts
-- ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS cluster_regression_reports (
    report_id               TEXT PRIMARY KEY,
    scan_id                 TEXT NOT NULL,
    cluster_id              TEXT NOT NULL,
    stratum                 TEXT,                        -- 'stable' | 'drifting' | 'recently_split' | 'new'
    verdict_score           REAL,                        -- 1.0 - 5.0 from Gemini
    prev_verdict_score      REAL,                        -- latest prior score for 2-scan confirmation
    diagnosis_text          TEXT,
    recommended_action      TEXT,                        -- 'none' | 'rename' | 'split' | 'merge' | 'retire'
    sample_ticket_ids_json  TEXT,                        -- sampled members shown to the LLM
    confirmed_degradation   BOOLEAN DEFAULT 0,           -- True when current AND prev < 3.0
    created_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (cluster_id) REFERENCES canonical_clusters(cluster_id)
);
CREATE INDEX IF NOT EXISTS idx_crr_scan    ON cluster_regression_reports(scan_id);
CREATE INDEX IF NOT EXISTS idx_crr_cluster ON cluster_regression_reports(cluster_id);
CREATE INDEX IF NOT EXISTS idx_crr_confirmed ON cluster_regression_reports(confirmed_degradation);


-- ────────────────────────────────────────────────────────────────
-- 5. canonical_clusters counter for dormancy detection
-- ────────────────────────────────────────────────────────────────
-- Incremented each scan the cluster has 0 members; reset to 0 on any scan
-- with ≥ 1 member. tier transitions fire when counter crosses 3 (dormant)
-- or 10 (retired). Nullable so existing rows start at NULL → treated as 0.
ALTER TABLE canonical_clusters ADD COLUMN scans_without_members INTEGER DEFAULT 0;
