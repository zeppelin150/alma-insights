-- Migration 004: Guru Card Graph (Hardening H5)
--
-- Adds relationship graph and domain tagging tables for
-- multi-agent card analysis and constellation traversal.

-- ── Card-to-card relationships ──────────────────────────────────
-- Relationship types:
--   same_collection  — cards in the same Guru collection
--   cross_reference  — card A mentions card B title in content
--   url_link         — card A contains a URL pointing to card B
--   shared_friction  — cards cover the same friction type

CREATE TABLE IF NOT EXISTS guru_card_graph (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id   TEXT NOT NULL,
    target_id   TEXT NOT NULL,
    rel_type    TEXT NOT NULL CHECK(rel_type IN (
                    'same_collection', 'cross_reference',
                    'url_link', 'shared_friction'
                )),
    weight      REAL NOT NULL DEFAULT 1.0,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source_id, target_id, rel_type)
);

CREATE INDEX IF NOT EXISTS idx_card_graph_source
    ON guru_card_graph(source_id);
CREATE INDEX IF NOT EXISTS idx_card_graph_target
    ON guru_card_graph(target_id);
CREATE INDEX IF NOT EXISTS idx_card_graph_rel_type
    ON guru_card_graph(rel_type);


-- ── Domain tags ─────────────────────────────────────────────────
-- Each card can have 1+ domain tags.  Domains are assigned by:
--   collection-based (collection name → domain)
--   title-keyword    (keyword match on card title)
--   llm-classified   (optional batch classification)

CREATE TABLE IF NOT EXISTS guru_card_domains (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id     TEXT NOT NULL,
    domain      TEXT NOT NULL,
    source      TEXT NOT NULL CHECK(source IN (
                    'collection', 'keyword', 'llm'
                )),
    confidence  REAL NOT NULL DEFAULT 1.0,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(card_id, domain, source)
);

CREATE INDEX IF NOT EXISTS idx_card_domains_card
    ON guru_card_domains(card_id);
CREATE INDEX IF NOT EXISTS idx_card_domains_domain
    ON guru_card_domains(domain);
