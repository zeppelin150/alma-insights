-- Migration 024: Phase 8 data integrity scoring + threshold calibration (S10.2)
--
-- Provides the backend substrate for the Data Integrity section of the
-- analyst report:
--
--   * `threshold_values_current`  — live tuning values consumed by the scorer
--   * `threshold_change_log`      — audit trail for every threshold change
--   * `data_integrity_scores`     — per-metric score rows written per scan
--   * `canonicalization_golden_set` — pair-level ground truth for calibration
--   * `integrity_review_queue`    — HITL triage queue (fed by the scorer)
--
-- Plan reference: canonicalization-enrichment.md §8 + s10-backend-foundation-kernel.md §S10.2.

-- ────────────────────────────────────────────────────────────────
-- 1. threshold_values_current — one row per setting key
-- ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS threshold_values_current (
    setting_key         TEXT PRIMARY KEY,
    value               REAL NOT NULL,
    calibration_stage   TEXT,       -- 'literature' | 'empirical' | 'golden_set' | 'hitl'
    last_calibrated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    source_scan_range   TEXT        -- e.g. 'scan1..scan12' for empirical
);

-- ────────────────────────────────────────────────────────────────
-- 2. threshold_change_log — every change with reason
-- ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS threshold_change_log (
    change_id                         TEXT PRIMARY KEY,
    setting_key                       TEXT,
    old_value                         REAL,
    new_value                         REAL,
    changed_by                        TEXT,            -- calibrator stage, or HITL username
    reason                            TEXT,
    scan_id_at_change                 TEXT,
    golden_set_precision_at_change    REAL,
    changed_at                        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_tcl_key ON threshold_change_log(setting_key);

-- ────────────────────────────────────────────────────────────────
-- 3. data_integrity_scores — per-scan per-metric values
-- ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS data_integrity_scores (
    scan_id     TEXT NOT NULL,
    dimension   TEXT NOT NULL,        -- coherence | label_fidelity | assignment_precision | temporal_stability | population_health
    metric      TEXT NOT NULL,        -- e.g. silhouette_mean, noise_rate
    value       REAL,
    threshold   REAL,
    passed      BOOLEAN,              -- value ≥ threshold for "higher is better", etc.
    weight      REAL,                 -- within-dimension weight
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (scan_id, dimension, metric)
);
CREATE INDEX IF NOT EXISTS idx_dis_scan ON data_integrity_scores(scan_id);
CREATE INDEX IF NOT EXISTS idx_dis_band ON data_integrity_scores(dimension);

-- ────────────────────────────────────────────────────────────────
-- 4. canonicalization_golden_set — pair-level ground truth
-- ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS canonicalization_golden_set (
    pair_id       TEXT PRIMARY KEY,
    ticket_a_id   TEXT,
    ticket_b_id   TEXT,
    same_concept  BOOLEAN NOT NULL,
    concept_id    TEXT,
    labeled_by    TEXT,
    labeled_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    notes         TEXT,
    source        TEXT                -- 'manual' | 'designed' | 'hitl_derived'
);
CREATE INDEX IF NOT EXISTS idx_gs_source ON canonicalization_golden_set(source);

-- ────────────────────────────────────────────────────────────────
-- 5. integrity_review_queue — HITL triage queue
-- ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS integrity_review_queue (
    review_id        TEXT PRIMARY KEY,
    item_type        TEXT,            -- fission_candidate | label_churn | dormancy_resurrection | threshold_suggestion | low_coherence
    payload_json     TEXT,
    priority         INTEGER DEFAULT 5,
    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    reviewed_at      TIMESTAMP,
    reviewed_by      TEXT,
    decision         TEXT,            -- 'approve' | 'reject' | 'defer' | 'apply' | 'confirm' | 'false_positive'
    decision_notes   TEXT,
    scan_id          TEXT             -- scan that generated this item
);
CREATE INDEX IF NOT EXISTS idx_irq_unreviewed ON integrity_review_queue(reviewed_at) WHERE reviewed_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_irq_type       ON integrity_review_queue(item_type);
