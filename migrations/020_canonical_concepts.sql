-- Migration 020: canonical_concepts — Level-2 concept grouping (Phase 7)
--
-- The Phase 3 gate showed HDBSCAN + Qwen3 embeddings cap at ~0.65 pairwise
-- recall regardless of tuning, because same-concept tickets diverge in the
-- embedding space on surface-level features (claim IDs, dates, wording).
--
-- Phase 7 adds a Level-2 abstraction: one batched Gemini call per scan
-- groups the canonical_clusters into human-meaningful `concepts`. Recall is
-- re-scored against `concept_id` rather than `cluster_id`, targeting ≥ 0.80.
--
-- Plan reference: canonicalization-enrichment.md §7 + phase-5-7-session-kernel.md
-- (Phase 7 — the recall fix).
--
-- The `canonical_clusters.concept_id` FK column was pre-declared in
-- migration 017 as nullable, so this migration only creates the parent
-- table and its indexes.

CREATE TABLE IF NOT EXISTS canonical_concepts (
    concept_id              TEXT PRIMARY KEY,               -- slug or uuid hex
    concept_label           TEXT NOT NULL,
    concept_description     TEXT,
    centroid_blob           BLOB,                           -- mean of member-cluster centroids (optional, may be NULL until computed)
    member_cluster_count    INTEGER DEFAULT 0,
    lifetime_tickets        INTEGER DEFAULT 0,
    trcs_touched_json       TEXT,                           -- JSON array of TRC strings represented by member clusters
    first_seen_scan_id      TEXT,
    last_seen_scan_id       TEXT,
    first_seen_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_seen_at            TIMESTAMP,
    source                  TEXT DEFAULT 'llm',             -- 'llm' | 'agglomerative' | 'manual'
    llm_confidence          REAL,
    rationale               TEXT
);

CREATE INDEX IF NOT EXISTS idx_cconcept_last_scan ON canonical_concepts(last_seen_scan_id);
CREATE INDEX IF NOT EXISTS idx_cconcept_source    ON canonical_concepts(source);


-- Audit log: one row per linking run, capturing LLM call metadata.
CREATE TABLE IF NOT EXISTS canonical_concept_linking_runs (
    run_id                  TEXT PRIMARY KEY,
    scan_id                 TEXT,
    cluster_count_input     INTEGER,
    concept_count_output    INTEGER,
    unlinked_cluster_count  INTEGER,
    tokens_in               INTEGER,
    tokens_out              INTEGER,
    wall_time_ms            INTEGER,
    cost_usd                REAL,
    raw_output              TEXT,
    created_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_cc_linking_scan ON canonical_concept_linking_runs(scan_id);
