-- Migration 018: Multi-label tiered canonical assignments (Phase 4)
--
-- Adds the M:N assignment table ticket_canonical_assignments so a single
-- ticket can have 1-3 ranked cluster memberships (primary / secondary /
-- tertiary) with per-tier thresholds. ticket_index.canonical_issue_id
-- continues to mirror the primary assignment for single-label fast-path.
--
-- Also adds assignment_transitions for scan-over-scan primary-cluster
-- change auditability.
--
-- Plan reference: canonicalization-enrichment.md §4.

CREATE TABLE IF NOT EXISTS ticket_canonical_assignments (
    ticket_id           TEXT NOT NULL,
    cluster_id          TEXT NOT NULL,
    rank                INTEGER NOT NULL,           -- 1 | 2 | 3
    cosine_similarity   REAL NOT NULL,
    assignment_tier     TEXT NOT NULL,              -- primary | secondary | tertiary
    assignment_method   TEXT NOT NULL,              -- mirrors canonicalization_engine enum
    assigned_in_scan_id TEXT NOT NULL,
    assigned_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (ticket_id, cluster_id),
    FOREIGN KEY (ticket_id)  REFERENCES ticket_index(ticket_id) ON DELETE CASCADE,
    FOREIGN KEY (cluster_id) REFERENCES canonical_clusters(cluster_id)
);
CREATE INDEX IF NOT EXISTS idx_tca_ticket_rank    ON ticket_canonical_assignments(ticket_id, rank);
CREATE INDEX IF NOT EXISTS idx_tca_cluster_tier   ON ticket_canonical_assignments(cluster_id, assignment_tier);
CREATE INDEX IF NOT EXISTS idx_tca_scan           ON ticket_canonical_assignments(assigned_in_scan_id);

CREATE TABLE IF NOT EXISTS assignment_transitions (
    transition_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id               TEXT NOT NULL,
    scan_id                 TEXT NOT NULL,
    old_primary_cluster_id  TEXT,
    new_primary_cluster_id  TEXT,
    old_cosine              REAL,
    new_cosine              REAL,
    transition_reason       TEXT,                   -- new_ticket | centroid_shift | cluster_split | cluster_merge
    transitioned_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_at_ticket ON assignment_transitions(ticket_id);
CREATE INDEX IF NOT EXISTS idx_at_scan   ON assignment_transitions(scan_id);
