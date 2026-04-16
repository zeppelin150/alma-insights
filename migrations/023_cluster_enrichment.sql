-- Migration 023: Cluster enrichment rollups (Phase 7 / S10.1)
--
-- Per-scan aggregated metadata for each canonical cluster. Populated by
-- `compute_enrichment_rollups` at the end of `run_canonicalization`.
--
-- The analyst report reads these rows for concept-level narrative (top
-- payers, velocity, tag correlations) so it never re-aggregates ticket_index.
--
-- Plan reference: canonicalization-enrichment.md §7.1 (simplified per
--                 s10-backend-foundation-kernel.md §S10.1).

CREATE TABLE IF NOT EXISTS canonical_cluster_enrichment (
    cluster_id                 TEXT    NOT NULL,
    scan_id                    TEXT    NOT NULL,
    ticket_count               INTEGER,
    avg_sentiment_intensity    REAL,
    negative_sentiment_pct     REAL,
    avg_csat                   REAL,
    csat_response_rate         REAL,
    avg_resolution_hours       REAL,
    avg_first_reply_hours      REAL,   -- NULL until ticket_index exposes first-reply latency
    anomaly_ticket_pct         REAL,
    velocity_wow_pct           REAL,   -- % change in created-this-period vs prior 7 days
    velocity_mom_pct           REAL,   -- % change vs prior 30 days
    top_payers_json            TEXT,   -- JSON [{value, count, pct}, ...] up to 5
    top_providers_json         TEXT,
    top_states_json            TEXT,
    top_agents_json            TEXT,
    top_key_phrases_json       TEXT,
    top_tags_json              TEXT,
    trc_distribution_json      TEXT,
    custom_metrics_json        TEXT,   -- escape hatch for future dimensions
    computed_at                TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    source_query_checksum      TEXT,   -- SHA256 of canonical SQL so report can prove what was measured
    PRIMARY KEY (cluster_id, scan_id)
);
CREATE INDEX IF NOT EXISTS idx_cce_scan    ON canonical_cluster_enrichment(scan_id);
CREATE INDEX IF NOT EXISTS idx_cce_cluster ON canonical_cluster_enrichment(cluster_id);
