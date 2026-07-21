-- Migration 050 — full-text index over enablement_documents.
--
-- The Drive search eval (tests/drive_eval, scripts/run_drive_eval.py) measured
-- 0.000 recall@k on natural-language queries: enablement_store.search_documents
-- and kb.search's full-text FLOOR both wrapped the ENTIRE query in a single
-- LIKE '%…%', so a multi-word question only matched a verbatim contiguous
-- phrase. This FTS5 mirror is the retrieval layer both paths now rank over
-- (src/data/enablement_doc_search.py), the same tokenized approach proven on
-- the help corpus (048_help_center.sql).
--
-- enablement_documents (027) is the source of truth; this is a rebuildable
-- index kept in sync by triggers. unicode61 + remove_diacritics matches 046/048
-- (payer names). Contentless (content='') — so, per the 048 note, the delete
-- trigger MUST supply the ORIGINAL column values, not the rowid-only form, or a
-- corrected/re-indexed document leaves its OLD text searchable.
--
-- All writes go through enablement_store.save_document, which upserts
-- (INSERT ON CONFLICT DO UPDATE): a fresh doc fires the AFTER INSERT trigger, a
-- re-index fires AFTER UPDATE. Idempotent (IF NOT EXISTS). Back-fills existing
-- rows so documents indexed before this migration become searchable at once.

CREATE VIRTUAL TABLE IF NOT EXISTS enablement_documents_fts USING fts5(
    name, full_text,
    content='',
    tokenize='unicode61 remove_diacritics 2'
);

-- Recreate (not IF NOT EXISTS) so re-running repairs an earlier definition.
DROP TRIGGER IF EXISTS enablement_documents_ai;
DROP TRIGGER IF EXISTS enablement_documents_ad;
DROP TRIGGER IF EXISTS enablement_documents_au;

CREATE TRIGGER enablement_documents_ai AFTER INSERT ON enablement_documents BEGIN
    INSERT INTO enablement_documents_fts(rowid, name, full_text)
    VALUES (new.rowid, new.name, new.full_text);
END;
CREATE TRIGGER enablement_documents_ad AFTER DELETE ON enablement_documents BEGIN
    INSERT INTO enablement_documents_fts(enablement_documents_fts, rowid, name, full_text)
    VALUES ('delete', old.rowid, old.name, old.full_text);
END;
CREATE TRIGGER enablement_documents_au AFTER UPDATE ON enablement_documents BEGIN
    INSERT INTO enablement_documents_fts(enablement_documents_fts, rowid, name, full_text)
    VALUES ('delete', old.rowid, old.name, old.full_text);
    INSERT INTO enablement_documents_fts(rowid, name, full_text)
    VALUES (new.rowid, new.name, new.full_text);
END;

-- Back-fill: rebuild the index from whatever rows already exist. 'delete-all'
-- clears it first so re-application cannot double-index.
INSERT INTO enablement_documents_fts(enablement_documents_fts) VALUES ('delete-all');
INSERT INTO enablement_documents_fts(rowid, name, full_text)
    SELECT rowid, name, full_text FROM enablement_documents;
