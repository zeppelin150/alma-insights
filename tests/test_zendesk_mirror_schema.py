"""WS1 — Migration 051 (Zendesk mirror) + zendesk_store mirror API.

Locks: idempotent re-apply; the 051 post-hook backfilling body_text /
actions_text / content_hash for pre-051 rows (a legacy row becomes
body-searchable); contentless-FTS discipline (insert/update/delete tracked,
original-value deletes, exactly ONE posting after re-upserts); the grep-level
ban on INSERT OR REPLACE against the mirrored tables; content_hash dedup
including metadata-only changes; the pending→ready→copied transition matrix;
the include_ai provenance filter; purge scopes (copied/pushed drafts always
retained); mutators inside an already-open transaction (no atomic() nesting
raise); and sanitized search_mirror MATCH.
"""

import re
import shutil
from pathlib import Path

import pytest

from src.data import zendesk_store

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
MIGRATIONS = ROOT / "migrations"

ARTICLE = {"id": 101, "title": "Setting up SSO", "body": "<p>alpha tunnel body</p>",
           "locale": "en-us", "section_id": 9, "html_url": "https://x/101",
           "updated_at": "2026-07-01", "label_names": ["sso"], "position": 3}

MACRO = {"id": 201, "title": "Refund apology", "description": "say sorry",
         "active": True, "updated_at": "2026-06-12",
         "actions": [{"field": "comment_value", "value": "We are sorry about the refund delay"}]}


def _fts_hits(conn, table, term):
    return conn.execute(
        f"SELECT rowid FROM {table} WHERE {table} MATCH ?", (f'"{term}"',)
    ).fetchall()


# ── migration application ────────────────────────────────────────────

@pytest.fixture()
def pre051(tmp_path):
    """Connection migrated with 030 ONLY (a pre-051 mirror), plus the tmp
    migrations dir so tests can drop 051 in and re-migrate."""
    from src.data.connection_factory import get_connection
    from src.updater.schema_migrator import SchemaMigrator
    mig_dir = tmp_path / "migs"
    mig_dir.mkdir()
    shutil.copy(MIGRATIONS / "030_zendesk_content.sql",
                mig_dir / "030_zendesk_content.sql")
    conn = get_connection(tmp_path / "m.db")
    SchemaMigrator(mig_dir).migrate(conn)
    yield conn, mig_dir
    conn.close()


def _add_051(mig_dir):
    shutil.copy(MIGRATIONS / "051_zendesk_mirror.sql",
                mig_dir / "051_zendesk_mirror.sql")


class TestMigration051:
    def test_columns_and_tables_present_on_empty_db(self, empty_db):
        conn = empty_db.conn
        art_cols = {r[1] for r in conn.execute(
            "PRAGMA table_info(zendesk_articles)").fetchall()}
        for col in ("body_html", "body_text", "draft", "outdated", "labels_json",
                    "author_name", "position", "created_at_remote",
                    "content_hash", "origin", "source_file", "raw_json"):
            assert col in art_cols, f"zendesk_articles.{col} missing"
        mac_cols = {r[1] for r in conn.execute(
            "PRAGMA table_info(zendesk_macros)").fetchall()}
        for col in ("actions_text", "content_hash", "origin", "source_file",
                    "raw_json"):
            assert col in mac_cols, f"zendesk_macros.{col} missing"
        for table, col in (("zendesk_article_drafts", "body_html"),
                           ("zendesk_article_drafts", "rationale"),
                           ("zendesk_article_drafts", "sources_json"),
                           ("zendesk_article_drafts", "copied_at"),
                           ("zendesk_macro_drafts", "reply_html"),
                           ("zendesk_macro_drafts", "rationale"),
                           ("zendesk_macro_drafts", "sources_json"),
                           ("zendesk_macro_drafts", "copied_at")):
            cols = {r[1] for r in conn.execute(
                f"PRAGMA table_info({table})").fetchall()}
            assert col in cols, f"{table}.{col} missing"
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert "zendesk_sections" in tables
        assert "zendesk_categories" in tables
        assert "zendesk_articles_fts" in tables
        assert "zendesk_macros_fts" in tables

    def test_posthook_backfills_pre051_rows(self, pre051):
        conn, mig_dir = pre051
        # Legacy rows written by the pre-051 sync shape (no projections)
        conn.execute(
            "INSERT INTO zendesk_articles (article_id, title, body, locale, "
            "section_id, html_url, updated_at, fetched_at) "
            "VALUES (7, 'Legacy VPN', '<p>vpn tunnel steps</p>', 'en-us', 1, "
            "'', 't', 't')")
        conn.execute(
            "INSERT INTO zendesk_macros (macro_id, name, description, "
            "actions_json, active, updated_at, fetched_at) VALUES "
            "(8, 'Old macro', '', "
            "'[{\"field\": \"comment_value\", \"value\": \"escalate quickly\"}]', "
            "1, 't', 't')")
        conn.commit()

        _add_051(mig_dir)
        from src.updater.schema_migrator import SchemaMigrator
        applied = SchemaMigrator(mig_dir).migrate(conn)
        assert "051_zendesk_mirror.sql" in applied

        row = conn.execute(
            "SELECT body_text, content_hash FROM zendesk_articles "
            "WHERE article_id=7").fetchone()
        assert row[0] == "vpn tunnel steps"
        assert row[1]
        # a pre-migration row becomes BODY-searchable post-migration
        assert len(_fts_hits(conn, "zendesk_articles_fts", "tunnel")) == 1

        mrow = conn.execute(
            "SELECT actions_text, content_hash FROM zendesk_macros "
            "WHERE macro_id=8").fetchone()
        assert "escalate quickly" in mrow[0]
        assert mrow[1]
        assert len(_fts_hits(conn, "zendesk_macros_fts", "escalate")) == 1

    def test_double_apply_is_idempotent(self, pre051):
        conn, mig_dir = pre051
        _add_051(mig_dir)
        from src.updater.schema_migrator import SchemaMigrator
        SchemaMigrator(mig_dir).migrate(conn)
        zendesk_store.upsert_articles(conn, [dict(ARTICLE)])
        # simulate a re-application (repair form): untrack, migrate again
        conn.execute("DELETE FROM schema_migrations "
                     "WHERE filename='051_zendesk_mirror.sql'")
        conn.commit()
        applied = SchemaMigrator(mig_dir).migrate(conn)
        assert "051_zendesk_mirror.sql" in applied
        # data survives and the FTS still holds exactly one posting
        assert len(_fts_hits(conn, "zendesk_articles_fts", "tunnel")) == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM zendesk_articles").fetchone()[0] == 1


# ── FTS discipline ───────────────────────────────────────────────────

class TestFtsDiscipline:
    def test_insert_update_delete_tracked(self, empty_db):
        conn = empty_db.conn
        zendesk_store.upsert_articles(conn, [dict(ARTICLE)])
        assert len(_fts_hits(conn, "zendesk_articles_fts", "tunnel")) == 1

        changed = dict(ARTICLE, body="<p>beta harbor body</p>")
        zendesk_store.upsert_articles(conn, [changed])
        # original-value delete rule: the OLD body's terms are gone
        assert len(_fts_hits(conn, "zendesk_articles_fts", "tunnel")) == 0
        assert len(_fts_hits(conn, "zendesk_articles_fts", "harbor")) == 1

        conn.execute("DELETE FROM zendesk_articles WHERE article_id=101")
        conn.commit()
        assert len(_fts_hits(conn, "zendesk_articles_fts", "harbor")) == 0

    def test_reupsert_leaves_exactly_one_posting(self, empty_db):
        conn = empty_db.conn
        zendesk_store.upsert_articles(conn, [dict(ARTICLE)])
        zendesk_store.upsert_articles(
            conn, [dict(ARTICLE, body="<p>second body</p>")])
        zendesk_store.upsert_articles(
            conn, [dict(ARTICLE, body="<p>third body</p>")])
        # title term is stable across upserts — a REPLACE-style desync would
        # stack a posting per write
        assert len(_fts_hits(conn, "zendesk_articles_fts", "sso")) == 1

    def test_macro_fts_tracked(self, empty_db):
        conn = empty_db.conn
        zendesk_store.upsert_macros(conn, [dict(MACRO)])
        assert len(_fts_hits(conn, "zendesk_macros_fts", "refund")) >= 1
        changed = dict(MACRO, actions=[{"field": "comment_value",
                                        "value": "apology dispatched"}])
        zendesk_store.upsert_macros(conn, [changed])
        assert len(_fts_hits(conn, "zendesk_macros_fts", "delay")) == 0
        assert len(_fts_hits(conn, "zendesk_macros_fts", "dispatched")) == 1
        conn.execute("DELETE FROM zendesk_macros WHERE macro_id=201")
        conn.commit()
        assert len(_fts_hits(conn, "zendesk_macros_fts", "dispatched")) == 0

    def test_no_insert_or_replace_targets_mirror_tables(self):
        pat = re.compile(
            r"INSERT\s+OR\s+REPLACE\s+INTO\s+zendesk_"
            r"(articles|macros|sections|categories)\b", re.IGNORECASE)
        offenders = []
        for path in SRC.rglob("*.py"):
            text = path.read_text(encoding="utf-8", errors="ignore")
            if pat.search(text):
                offenders.append(str(path.relative_to(ROOT)))
        assert not offenders, (
            "INSERT OR REPLACE fires only the FTS insert trigger and desyncs "
            f"the contentless mirror — use ON CONFLICT DO UPDATE: {offenders}")


# ── upsert dedup + projection ────────────────────────────────────────

class TestUpsertDedup:
    def test_hash_unchanged_row_skipped(self, empty_db):
        conn = empty_db.conn
        first = zendesk_store.upsert_articles(conn, [dict(ARTICLE)])
        assert first["inserted"] == 1
        second = zendesk_store.upsert_articles(conn, [dict(ARTICLE)])
        assert second == {"inserted": 0, "updated": 0, "unchanged": 1,
                          "conflicts": 0, "conflict_ids": []}

    def test_metadata_only_change_is_an_update(self, empty_db):
        conn = empty_db.conn
        zendesk_store.upsert_articles(conn, [dict(ARTICLE)])
        meta = dict(ARTICLE, label_names=["sso", "saml"], outdated=True,
                    section_id=12)
        report = zendesk_store.upsert_articles(conn, [meta])
        assert report["updated"] == 1 and report["unchanged"] == 0
        row = zendesk_store.get_article(conn, 101)
        assert row["outdated"] == 1
        assert row["labels"] == ["sso", "saml"]
        assert row["section_id"] == 12
        # body untouched: projection + legacy column still populated
        assert row["body_text"] == "alpha tunnel body"
        assert row["body"] == "<p>alpha tunnel body</p>"
        assert row["body_html"] == "<p>alpha tunnel body</p>"

    def test_import_cannot_replace_differing_pull_row(self, empty_db):
        conn = empty_db.conn
        zendesk_store.upsert_articles(conn, [dict(ARTICLE)], origin="pull")
        hostile = dict(ARTICLE, body="<p>poisoned baseline</p>")
        report = zendesk_store.upsert_articles(conn, [hostile], origin="import",
                                               source_file="evil.json")
        assert report["conflicts"] == 1 and report["conflict_ids"] == [101]
        row = zendesk_store.get_article(conn, 101)
        assert row["body_html"] == "<p>alpha tunnel body</p>"
        assert row["origin"] == "pull"

    def test_sections_and_categories_upsert(self, empty_db):
        conn = empty_db.conn
        zendesk_store.upsert_categories(conn, [{"id": 1, "name": "General",
                                                "position": 1}])
        rep = zendesk_store.upsert_sections(
            conn, [{"id": 9, "category_id": 1, "name": "FAQ", "position": 2}])
        assert rep["inserted"] == 1
        rep2 = zendesk_store.upsert_sections(
            conn, [{"id": 9, "category_id": 1, "name": "FAQ v2"}])
        assert rep2["updated"] == 1
        assert zendesk_store.list_sections(conn)[0]["name"] == "FAQ v2"
        assert zendesk_store.list_categories(conn)[0]["name"] == "General"


# ── revisions lifecycle ──────────────────────────────────────────────

def _force_status(conn, table, draft_id, status):
    conn.execute(f"UPDATE {table} SET status=? WHERE id=?", (status, draft_id))
    conn.commit()


class TestDraftLifecycle:
    STATUSES = ("pending", "ready", "copied", "pushed")
    ALLOWED = {("pending", "ready"), ("ready", "pending"),
               ("pending", "copied"), ("ready", "copied")}

    def test_full_transition_matrix(self, empty_db):
        conn = empty_db.conn
        for current in self.STATUSES:
            for target in self.STATUSES:
                did = zendesk_store.save_article_draft(conn, title="t", body="b")
                _force_status(conn, "zendesk_article_drafts", did, current)
                res = zendesk_store.set_draft_status(conn, "article", did, target)
                expect_ok = (current, target) in self.ALLOWED
                assert res["ok"] is expect_ok, (
                    f"{current}->{target} expected ok={expect_ok}, got {res}")
                if not expect_ok:
                    assert res["error"] == "invalid_transition"
                    row = zendesk_store.get_article_draft(conn, did)
                    assert row["status"] == current

    def test_matrix_applies_to_macro_drafts(self, empty_db):
        conn = empty_db.conn
        did = zendesk_store.save_macro_draft(conn, name="m", actions=[])
        assert zendesk_store.set_draft_status(conn, "macro", did, "ready")["ok"]
        assert not zendesk_store.set_draft_status(conn, "macro", did, "ready")["ok"]
        assert zendesk_store.set_draft_status(conn, "macro", did, "copied")["ok"]
        assert not zendesk_store.set_draft_status(conn, "macro", did, "pending")["ok"]

    def test_copied_sets_copied_at(self, empty_db):
        conn = empty_db.conn
        did = zendesk_store.save_article_draft(conn, title="t", body="b")
        res = zendesk_store.set_draft_status(conn, "article", did, "copied")
        assert res["ok"]
        assert zendesk_store.get_article_draft(conn, did)["copied_at"]

    def test_expected_mismatch_refused(self, empty_db):
        conn = empty_db.conn
        did = zendesk_store.save_article_draft(conn, title="t", body="b")
        res = zendesk_store.set_draft_status(conn, "article", did, "ready",
                                             expected="ready")
        assert not res["ok"] and res["error"] == "status_changed"

    def test_invalid_kind_and_missing_draft(self, empty_db):
        conn = empty_db.conn
        assert zendesk_store.set_draft_status(
            conn, "widget", 1, "ready")["error"] == "invalid_kind"
        assert zendesk_store.set_draft_status(
            conn, "article", 999, "ready")["error"] == "draft_not_found"
        assert zendesk_store.delete_draft(
            conn, "article", 999)["error"] == "draft_not_found"

    def test_delete_draft(self, empty_db):
        conn = empty_db.conn
        did = zendesk_store.save_article_draft(conn, title="t", body="b")
        assert zendesk_store.delete_draft(conn, "article", did)["ok"]
        assert zendesk_store.get_article_draft(conn, did) is None


class TestListRevisions:
    def test_union_shape_and_filters(self, empty_db):
        conn = empty_db.conn
        zendesk_store.upsert_articles(conn, [dict(ARTICLE)])
        zendesk_store.upsert_macros(conn, [dict(MACRO)])
        a = zendesk_store.save_article_draft(
            conn, title="SSO (rev)", body="b", article_id=101,
            rationale="steps stale", sources_json=[{"ref": "doc:1", "label": "runbook"}])
        m = zendesk_store.save_macro_draft(
            conn, name="Refund v2", actions=[], macro_id=201, rationale="tone")
        zendesk_store.set_draft_status(conn, "macro", m, "ready")

        revs = zendesk_store.list_revisions(conn)
        assert {r["kind"] for r in revs} == {"article", "macro"}
        art = next(r for r in revs if r["kind"] == "article")
        assert art["draft_id"] == a
        assert art["target_id"] == 101
        assert art["target_title"] == "Setting up SSO"
        assert art["status"] == "pending"
        assert art["rationale"] == "steps stale"
        assert "runbook" in art["sources_json"]
        assert art["created_at"] and art["copied_at"] is None

        only_ready = zendesk_store.list_revisions(conn, status="ready")
        assert [r["draft_id"] for r in only_ready] == [m]
        only_articles = zendesk_store.list_revisions(conn, kind="article")
        assert all(r["kind"] == "article" for r in only_articles)


class TestIncludeAiFilter:
    def test_rationale_drafts_hidden_from_native_lists(self, empty_db):
        conn = empty_db.conn
        hand = zendesk_store.save_article_draft(conn, title="hand", body="b")
        ai = zendesk_store.save_article_draft(conn, title="ai", body="b",
                                              rationale="renn wrote this")
        default_ids = {d["id"] for d in zendesk_store.list_article_drafts(conn)}
        assert hand in default_ids and ai not in default_ids
        all_ids = {d["id"] for d in zendesk_store.list_article_drafts(
            conn, include_ai=True)}
        assert {hand, ai} <= all_ids

    def test_macro_drafts_filter(self, empty_db):
        conn = empty_db.conn
        hand = zendesk_store.save_macro_draft(conn, name="hand", actions=[])
        ai = zendesk_store.save_macro_draft(conn, name="ai", actions=[],
                                            rationale="renn")
        default_ids = {d["id"] for d in zendesk_store.list_macro_drafts(conn)}
        assert hand in default_ids and ai not in default_ids
        assert ai in {d["id"] for d in zendesk_store.list_macro_drafts(
            conn, include_ai=True)}


class TestExtendedDraftKwargs:
    def test_article_draft_new_columns_round_trip(self, empty_db):
        conn = empty_db.conn
        did = zendesk_store.save_article_draft(
            conn, title="t", body="md body", body_html="<p>md body</p>",
            rationale="why", sources_json=[{"ref": "doc:9"}])
        d = zendesk_store.get_article_draft(conn, did)
        assert d["body_html"] == "<p>md body</p>"
        assert d["rationale"] == "why"
        assert "doc:9" in d["sources_json"]
        zendesk_store.update_article_draft(conn, did, body_html="<p>v2</p>")
        assert zendesk_store.get_article_draft(conn, did)["body_html"] == "<p>v2</p>"

    def test_macro_draft_reply_html_round_trip(self, empty_db):
        conn = empty_db.conn
        did = zendesk_store.save_macro_draft(
            conn, name="m", actions=[], reply_html="<p>hi</p>", rationale="r")
        d = zendesk_store.get_macro_draft(conn, did)
        assert d["reply_html"] == "<p>hi</p>"
        zendesk_store.update_macro_draft(conn, did, reply_html="<p>v2</p>")
        assert zendesk_store.get_macro_draft(conn, did)["reply_html"] == "<p>v2</p>"


# ── purge scopes ─────────────────────────────────────────────────────

def _seed_mirror(conn):
    zendesk_store.upsert_articles(conn, [dict(ARTICLE)])
    zendesk_store.upsert_macros(conn, [dict(MACRO)])
    zendesk_store.upsert_sections(conn, [{"id": 9, "name": "FAQ"}])
    zendesk_store.upsert_categories(conn, [{"id": 1, "name": "General"}])


class TestPurgeMirror:
    def test_scope_articles_keeps_macros_and_drafts(self, empty_db):
        conn = empty_db.conn
        _seed_mirror(conn)
        did = zendesk_store.save_article_draft(conn, title="t", body="b")
        res = zendesk_store.purge_mirror(conn, scope="articles")
        assert res["ok"] and res["articles"] == 1 and res["macros"] == 0
        assert zendesk_store.list_macros(conn)
        assert zendesk_store.get_article_draft(conn, did) is not None
        assert len(_fts_hits(conn, "zendesk_articles_fts", "tunnel")) == 0

    def test_scope_all_retains_copied_and_pushed_drafts(self, empty_db):
        conn = empty_db.conn
        _seed_mirror(conn)
        pending = zendesk_store.save_article_draft(conn, title="p", body="b")
        ready = zendesk_store.save_article_draft(conn, title="r", body="b")
        zendesk_store.set_draft_status(conn, "article", ready, "ready")
        copied = zendesk_store.save_article_draft(conn, title="c", body="b")
        zendesk_store.set_draft_status(conn, "article", copied, "copied")
        pushed = zendesk_store.save_macro_draft(conn, name="pu", actions=[])
        _force_status(conn, "zendesk_macro_drafts", pushed, "pushed")

        res = zendesk_store.purge_mirror(conn, scope="all")
        assert res["ok"]
        assert res["articles"] == 1 and res["macros"] == 1
        assert res["sections"] == 1 and res["categories"] == 1
        assert res["article_drafts"] == 2 and res["macro_drafts"] == 0
        assert zendesk_store.get_article_draft(conn, pending) is None
        assert zendesk_store.get_article_draft(conn, ready) is None
        assert zendesk_store.get_article_draft(conn, copied)["status"] == "copied"
        assert zendesk_store.get_macro_draft(conn, pushed)["status"] == "pushed"

    def test_scope_imported_keeps_pull_rows(self, empty_db):
        conn = empty_db.conn
        zendesk_store.upsert_articles(conn, [dict(ARTICLE)], origin="pull")
        zendesk_store.upsert_articles(
            conn, [{"id": -5, "title": "File import", "body": "<p>x</p>"}],
            origin="import", source_file="a.html")
        res = zendesk_store.purge_mirror(conn, scope="imported")
        assert res["ok"] and res["articles"] == 1
        assert zendesk_store.get_article(conn, 101) is not None
        assert zendesk_store.get_article(conn, -5) is None

    def test_invalid_scope_refused(self, empty_db):
        res = zendesk_store.purge_mirror(empty_db.conn, scope="everything")
        assert res == {"ok": False, "error": "invalid_scope"}


# ── transaction discipline (MCP dispatch path) ───────────────────────

class TestTxnDiscipline:
    def test_mutators_inside_open_transaction_do_not_nest_atomic(self, empty_db):
        conn = empty_db.conn
        conn.execute("BEGIN")
        assert conn.in_transaction
        did = zendesk_store.save_article_draft(
            conn, title="t", body="b", rationale="mcp path")
        assert zendesk_store.set_draft_status(conn, "article", did, "ready")["ok"]
        zendesk_store.upsert_articles(conn, [dict(ARTICLE)])
        zendesk_store.upsert_macros(conn, [dict(MACRO)])
        assert zendesk_store.delete_draft(conn, "article", did)["ok"]
        conn.commit()
        assert zendesk_store.get_article(conn, 101) is not None


# ── search_mirror ────────────────────────────────────────────────────

class TestSearchMirror:
    def test_empty_mirror_returns_empty_lists(self, empty_db):
        out = zendesk_store.search_mirror(empty_db.conn, "anything at all")
        assert out == {"articles": [], "macros": []}

    def test_hostile_query_never_raises(self, empty_db):
        conn = empty_db.conn
        _seed_mirror(conn)
        for q in ('"foo" OR NEAR(', "a-b (c) *", "'; DROP TABLE x; --", "", None):
            out = zendesk_store.search_mirror(conn, q)
            assert set(out.keys()) == {"articles", "macros"}

    def test_hits_carry_shape_and_kind_filter(self, empty_db):
        conn = empty_db.conn
        _seed_mirror(conn)
        out = zendesk_store.search_mirror(conn, "tunnel")
        assert len(out["articles"]) == 1
        hit = out["articles"][0]
        assert hit["id"] == 101 and hit["title"] == "Setting up SSO"
        assert hit["section_id"] == 9
        assert "tunnel" in hit["snippet"]
        assert isinstance(hit["score"], float)

        macros_only = zendesk_store.search_mirror(conn, "refund", kind="macros")
        assert macros_only["articles"] == []
        assert macros_only["macros"] and macros_only["macros"][0]["id"] == 201
