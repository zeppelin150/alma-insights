-- Migration 025: Historical member snapshots (Phase 6 snippets_then / S10.4)
--
-- Records per-cluster-per-scan a sample of member ticket_ids so the
-- regression prompt's `snippets_then` field can hydrate from a prior scan.
--
-- PHI-safe: ticket_ids only. Subjects/bodies are fetched at regression-
-- render time via the normal ticket_index path.
--
-- Cap: up to 200 ticket_ids per (cluster, scan). Larger clusters get a
-- deterministic random sample seeded by SHA256(scan_id || cluster_id).
--
-- Plan reference: s10-backend-foundation-kernel.md §S10.4.

CREATE TABLE IF NOT EXISTS cluster_member_snapshots (
    cluster_id              TEXT NOT NULL,
    scan_id                 TEXT NOT NULL,
    member_ticket_ids_json  TEXT,
    member_count            INTEGER,
    snapshot_at             TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (cluster_id, scan_id)
);
CREATE INDEX IF NOT EXISTS idx_cms_cluster ON cluster_member_snapshots(cluster_id);
CREATE INDEX IF NOT EXISTS idx_cms_scan    ON cluster_member_snapshots(scan_id);
