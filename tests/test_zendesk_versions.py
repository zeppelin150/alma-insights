"""WS-V1 — Migration 053 (Zendesk version history) + zendesk_versions store.

Module-level locks (the store capture hooks are WS-V2; every test here
calls capture/list/get/restore/rollback/seed/prune directly): 053 applies
on empty_db and double-applies idempotently; the post-hook seeds seq-1
'create' baselines exactly once (re-run guarded); capture_article_version
stores the OLD state with both origin tags; cap-50 pruning keeps the
newest 50 with monotonic ordering; captures participate in the caller's
open transaction (outer rollback removes them, no atomic() nesting
raise); list/get shapes; restore_article_version creates a PENDING draft
(source_ref='version-restore', verbatim body_html, mirror row
byte-identical before/after, invisible to include_ai=False lists) and
refuses missing versions / purged articles; the derive_author matrix;
draft-save seq counting; rollback_draft_to_version byte-exact restore +
'rollback' record, status never written, refused for
ready/copied/pushed and wrong-draft version ids; and the prune_orphans
sweep including copied-draft histories surviving purge scope='all'.

Note: tests that depend on a draft's version rows clear
zendesk_draft_versions after creating drafts, so they stay valid both
before and after WS-V2 lands the save_article_draft capture hook.
"""

import shutil
import sqlite3
from pathlib import Path

import pytest

from src.data import zendesk_store, zendesk_versions

ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS = ROOT / "migrations"

ARTICLE = {"id": 101, "title": "Setting up SSO", "body": "<p>alpha tunnel body</p>",
           "locale": "en-us", "section_id": 9, "html_url": "https://x/101",
           "updated_at": "2026-07-01", "label_names": ["sso"], "position": 3}

OLD_STATE = {"article_id": 101, "content_hash": "hash-old",
             "title": "Setting up SSO (old)",
             "body_html": "<p>old&nbsp;body <!-- raw --></p>",
             "body_text": "old body", "section_id": 9,
             "labels_json": '["sso"]', "author_name": "Docs Team",
             "draft": 0, "outdated": 0, "position": 3,
             "updated_at": "2026-06-01", "origin": "pull"}


def _mirror_row(conn, article_id):
    row = conn.execute("SELECT * FROM zendesk_articles WHERE article_id=?",
                       (article_id,)).fetchone()
    return dict(row) if row else None


def _clear_draft_versions(conn):
    """Make draft-version state deterministic regardless of whether the
    WS-V2 save-capture hooks have landed in zendesk_store yet."""
    conn.execute("DELETE FROM zendesk_draft_versions")
    conn.commit()


def _capture_states(conn, article_id, n, start=0):
    ids = []
    for i in range(start, start + n):
        row = dict(OLD_STATE, article_id=article_id, content_hash=f"h{i}",
                   title=f"T{i}", body_html=f"<p>v{i}</p>", body_text=f"v{i}")
        ids.append(zendesk_versions.capture_article_version(
            conn, row, replaced_by_origin="pull"))
    return ids


def _force_status(conn, draft_id, status):
    conn.execute("UPDATE zendesk_article_drafts SET status=? WHERE id=?",
                 (status, draft_id))
    conn.commit()


# ── migration application ────────────────────────────────────────────

@pytest.fixture()
def pre053(tmp_path):
    """Connection migrated with 030+051 ONLY (a pre-053 database), plus the
    tmp migrations dir so tests can drop 053 in and re-migrate."""
    from src.data.connection_factory import get_connection
    from src.updater.schema_migrator import SchemaMigrator
    mig_dir = tmp_path / "migs"
    mig_dir.mkdir()
    for name in ("030_zendesk_content.sql", "051_zendesk_mirror.sql"):
        shutil.copy(MIGRATIONS / name, mig_dir / name)
    conn = get_connection(tmp_path / "m.db")
    SchemaMigrator(mig_dir).migrate(conn)
    yield conn, mig_dir
    conn.close()


def _add_053(mig_dir):
    shutil.copy(MIGRATIONS / "053_zendesk_versions.sql",
                mig_dir / "053_zendesk_versions.sql")


def _remigrate_053(conn, mig_dir):
    from src.updater.schema_migrator import SchemaMigrator
    return SchemaMigrator(mig_dir).migrate(conn)


class TestMigration053:
    def test_tables_present_on_empty_db(self, empty_db):
        conn = empty_db.conn
        av_cols = {r[1] for r in conn.execute(
            "PRAGMA table_info(zendesk_article_versions)").fetchall()}
        for col in ("version_id", "article_id", "content_hash", "title",
                    "body_html", "body_text", "section_id", "labels_json",
                    "author_name", "draft", "outdated", "position",
                    "updated_at", "origin", "replaced_by_origin",
                    "captured_at"):
            assert col in av_cols, f"zendesk_article_versions.{col} missing"
        dv_cols = {r[1] for r in conn.execute(
            "PRAGMA table_info(zendesk_draft_versions)").fetchall()}
        for col in ("version_id", "draft_id", "seq", "title", "body",
                    "body_html", "author", "save_kind", "rollback_of",
                    "created_at"):
            assert col in dv_cols, f"zendesk_draft_versions.{col} missing"
        indexes = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'").fetchall()}
        assert "idx_zdav_article" in indexes

    def test_autoincrement_pks(self, empty_db):
        """Pruning deletes rows — a reused rowid would break the monotonic
        version_id ordering, so both PKs must be AUTOINCREMENT."""
        conn = empty_db.conn
        for table in ("zendesk_article_versions", "zendesk_draft_versions"):
            sql = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                (table,)).fetchone()[0]
            assert "AUTOINCREMENT" in sql.upper(), table

    def test_draft_seq_unique_constraint(self, empty_db):
        conn = empty_db.conn
        conn.execute(
            "INSERT INTO zendesk_draft_versions (draft_id, seq, created_at) "
            "VALUES (1, 1, 't')")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO zendesk_draft_versions (draft_id, seq, "
                "created_at) VALUES (1, 1, 't2')")
        conn.rollback()

    def test_double_apply_is_idempotent(self, pre053):
        conn, mig_dir = pre053
        _add_053(mig_dir)
        applied = _remigrate_053(conn, mig_dir)
        assert "053_zendesk_versions.sql" in applied
        _capture_states(conn, 101, 1)
        # simulate a re-application (repair form): untrack, migrate again
        conn.execute("DELETE FROM schema_migrations "
                     "WHERE filename='053_zendesk_versions.sql'")
        conn.commit()
        applied = _remigrate_053(conn, mig_dir)
        assert "053_zendesk_versions.sql" in applied
        assert conn.execute(
            "SELECT COUNT(*) FROM zendesk_article_versions").fetchone()[0] == 1

    def test_posthook_seeds_baselines_exactly_once(self, pre053):
        conn, mig_dir = pre053

        def _insert_draft(title, body, source_ref=None, rationale=None):
            # direct SQL, not save_article_draft — on this pre-053 database
            # the WS-V2 capture hook (once landed) would have no
            # zendesk_draft_versions table to write to
            cur = conn.execute(
                "INSERT INTO zendesk_article_drafts (title, body, source_ref, "
                "rationale, created_at, updated_at) VALUES (?,?,?,?,'t','t')",
                (title, body, source_ref, rationale))
            return int(cur.lastrowid)

        renn = _insert_draft("renn draft", "r body", rationale="stale steps")
        spec = _insert_draft("spec draft", "s body",
                             source_ref="specialist-edit")
        hand = _insert_draft("hand draft", "h body")
        conn.commit()

        _add_053(mig_dir)
        applied = _remigrate_053(conn, mig_dir)
        assert "053_zendesk_versions.sql" in applied

        for did, author in ((renn, "renn"), (spec, "specialist"),
                            (hand, "user")):
            saves = zendesk_versions.list_draft_versions(conn, did)
            assert len(saves) == 1, f"draft {did} expected one baseline"
            assert saves[0]["seq"] == 1
            assert saves[0]["save_kind"] == "create"
            assert saves[0]["author"] == author
            full = zendesk_versions.get_draft_version(
                conn, saves[0]["version_id"])
            assert full["body"] in ("r body", "s body", "h body")

        # re-run the migration (untrack): the guard must not re-seed
        conn.execute("DELETE FROM schema_migrations "
                     "WHERE filename='053_zendesk_versions.sql'")
        conn.commit()
        _remigrate_053(conn, mig_dir)
        for did in (renn, spec, hand):
            assert len(zendesk_versions.list_draft_versions(conn, did)) == 1

    def test_seed_skips_drafts_that_already_have_rows(self, empty_db):
        conn = empty_db.conn
        did = zendesk_store.save_article_draft(conn, title="t", body="b")
        _clear_draft_versions(conn)
        vid = zendesk_versions.capture_draft_version(
            conn, did, title="t", body="b", body_html=None,
            author="user", save_kind="create")
        assert zendesk_versions.seed_draft_baselines(conn) == 0
        saves = zendesk_versions.list_draft_versions(conn, did)
        assert [s["version_id"] for s in saves] == [vid]


# ── author derivation ────────────────────────────────────────────────

class TestDeriveAuthor:
    @pytest.mark.parametrize("source_ref,rationale,expected", [
        ("specialist-edit", None, "specialist"),
        ("specialist-edit", "renn wrote this", "specialist"),  # ref wins
        (None, "renn wrote this", "renn"),
        ("doc:1", "renn wrote this", "renn"),
        (None, None, "user"),
        ("doc:1", None, "user"),
    ])
    def test_matrix(self, source_ref, rationale, expected):
        assert zendesk_versions.derive_author(source_ref, rationale) == expected


# ── article version capture ──────────────────────────────────────────

class TestArticleCapture:
    def test_capture_stores_old_state_with_both_origin_tags(self, empty_db):
        conn = empty_db.conn
        vid = zendesk_versions.capture_article_version(
            conn, dict(OLD_STATE), replaced_by_origin="import")
        v = zendesk_versions.get_article_version(conn, vid)
        assert v["article_id"] == 101
        assert v["content_hash"] == "hash-old"
        assert v["title"] == "Setting up SSO (old)"
        assert v["body_html"] == "<p>old&nbsp;body <!-- raw --></p>"
        assert v["body_text"] == "old body"
        assert v["section_id"] == 9
        assert v["labels_json"] == '["sso"]'
        assert v["author_name"] == "Docs Team"
        assert v["updated_at"] == "2026-06-01"    # superseded remote stamp
        assert v["origin"] == "pull"              # the superseded row's
        assert v["replaced_by_origin"] == "import"  # the write that won
        assert v["captured_at"]

    def test_capture_defaults_for_sparse_rows(self, empty_db):
        conn = empty_db.conn
        vid = zendesk_versions.capture_article_version(
            conn, {"article_id": 7}, replaced_by_origin="pull")
        v = zendesk_versions.get_article_version(conn, vid)
        assert v["title"] == ""
        assert v["labels_json"] == "[]"
        assert v["origin"] == "pull"
        assert v["draft"] == 0 and v["outdated"] == 0

    def test_explicit_now_is_stored(self, empty_db):
        conn = empty_db.conn
        vid = zendesk_versions.capture_article_version(
            conn, dict(OLD_STATE), replaced_by_origin="pull",
            now="2026-07-20T12:00:00+00:00")
        v = zendesk_versions.get_article_version(conn, vid)
        assert v["captured_at"] == "2026-07-20T12:00:00+00:00"

    def test_list_shape_and_ordering(self, empty_db):
        conn = empty_db.conn
        ids = _capture_states(conn, 101, 3)
        versions = zendesk_versions.list_article_versions(conn, 101)
        assert [v["version_id"] for v in versions] == list(reversed(ids))
        first = versions[0]
        assert set(first.keys()) == {"version_id", "title", "content_hash",
                                     "origin", "replaced_by_origin",
                                     "captured_at", "updated_at", "chars"}
        assert first["chars"] == len("v2")
        assert "body_html" not in first and "body_text" not in first

    def test_list_limit_and_missing_get(self, empty_db):
        conn = empty_db.conn
        _capture_states(conn, 101, 5)
        assert len(zendesk_versions.list_article_versions(
            conn, 101, limit=2)) == 2
        assert zendesk_versions.get_article_version(conn, 999999) is None
        assert zendesk_versions.list_article_versions(conn, 424242) == []

    def test_cap_50_prunes_oldest_keeps_newest_monotonic(self, empty_db):
        conn = empty_db.conn
        other = zendesk_versions.capture_article_version(
            conn, dict(OLD_STATE, article_id=202),
            replaced_by_origin="pull")
        ids = _capture_states(conn, 101, 55)
        versions = zendesk_versions.list_article_versions(conn, 101, limit=100)
        assert len(versions) == 50
        got = [v["version_id"] for v in versions]
        assert got == sorted(got, reverse=True)          # monotonic
        assert got == list(reversed(ids[5:]))            # newest 50 exactly
        for old_id in ids[:5]:                           # oldest 5 pruned
            assert zendesk_versions.get_article_version(conn, old_id) is None
        # other article's history untouched by the per-article prune
        assert zendesk_versions.get_article_version(conn, other) is not None

    def test_capture_participates_in_caller_transaction(self, empty_db):
        conn = empty_db.conn
        conn.execute("BEGIN")
        assert conn.in_transaction
        # no atomic() nesting raise; row visible inside the txn
        vid = zendesk_versions.capture_article_version(
            conn, dict(OLD_STATE), replaced_by_origin="pull")
        assert zendesk_versions.get_article_version(conn, vid) is not None
        conn.rollback()
        # the outer rollback removed the capture with it
        assert zendesk_versions.get_article_version(conn, vid) is None

    def test_standalone_capture_commits(self, empty_db):
        conn = empty_db.conn
        vid = zendesk_versions.capture_article_version(
            conn, dict(OLD_STATE), replaced_by_origin="pull")
        conn.rollback()  # no-op after the internal atomic() committed
        assert zendesk_versions.get_article_version(conn, vid) is not None


# ── restore (non-destructive) ────────────────────────────────────────

class TestRestoreArticleVersion:
    @pytest.fixture()
    def mirrored(self, empty_db):
        conn = empty_db.conn
        zendesk_store.upsert_articles(conn, [dict(ARTICLE)])
        vid = zendesk_versions.capture_article_version(
            conn, dict(OLD_STATE), replaced_by_origin="pull")
        return conn, vid

    def test_creates_pending_draft_with_verbatim_body_html(self, mirrored):
        conn, vid = mirrored
        res = zendesk_versions.restore_article_version(conn, vid)
        assert res["ok"] is True and res["error"] is None
        d = zendesk_store.get_article_draft(conn, res["draft_id"])
        assert d["status"] == "pending"
        assert d["source_ref"] == "version-restore"
        assert d["article_id"] == 101
        assert d["title"] == "Setting up SSO (old)"
        assert d["body"] == "old body"
        # verbatim bytes -> copy-exact restore (no sanitize at store level)
        assert d["body_html"] == "<p>old&nbsp;body <!-- raw --></p>"
        assert d["rationale"].startswith("Restored from the version captured")
        assert f"article-version:{vid}" in d["sources_json"]
        assert "Version history" in d["sources_json"]

    def test_mirror_row_byte_identical_before_and_after(self, mirrored):
        conn, vid = mirrored
        before = _mirror_row(conn, 101)
        res = zendesk_versions.restore_article_version(conn, vid)
        assert res["ok"]
        after = _mirror_row(conn, 101)
        assert after == before
        assert after["content_hash"] == before["content_hash"]

    def test_invisible_to_native_include_ai_false_lists(self, mirrored):
        conn, vid = mirrored
        res = zendesk_versions.restore_article_version(conn, vid)
        native_ids = {d["id"] for d in zendesk_store.list_article_drafts(
            conn, include_ai=False)}
        assert res["draft_id"] not in native_ids
        web_ids = {d["id"] for d in zendesk_store.list_article_drafts(
            conn, include_ai=True)}
        assert res["draft_id"] in web_ids

    def test_missing_version_refused(self, empty_db):
        res = zendesk_versions.restore_article_version(empty_db.conn, 999999)
        assert res == {"ok": False, "draft_id": None,
                       "error": "version_not_found"}

    def test_purged_article_refused(self, mirrored):
        conn, vid = mirrored
        zendesk_store.purge_mirror(conn, scope="articles")
        res = zendesk_versions.restore_article_version(conn, vid)
        assert res == {"ok": False, "draft_id": None, "error": "article_gone"}


# ── draft version capture ────────────────────────────────────────────

class TestDraftCapture:
    def test_seq_counts_up_and_list_shape(self, empty_db):
        conn = empty_db.conn
        did = zendesk_store.save_article_draft(conn, title="t", body="b")
        _clear_draft_versions(conn)
        v1 = zendesk_versions.capture_draft_version(
            conn, did, title="t", body="b", body_html="<p>b</p>",
            author="renn", save_kind="create")
        v2 = zendesk_versions.capture_draft_version(
            conn, did, title="t2", body="body two", body_html="<p>2</p>",
            author="specialist")
        saves = zendesk_versions.list_draft_versions(conn, did)
        assert [s["version_id"] for s in saves] == [v2, v1]
        assert [s["seq"] for s in saves] == [2, 1]
        newest = saves[0]
        assert set(newest.keys()) == {"version_id", "seq", "title", "author",
                                      "save_kind", "rollback_of",
                                      "created_at", "chars"}
        assert newest["author"] == "specialist"
        assert newest["save_kind"] == "save"
        assert newest["chars"] == len("body two")
        assert "body" not in newest and "body_html" not in newest
        full = zendesk_versions.get_draft_version(conn, v2)
        assert full["body"] == "body two"
        assert full["body_html"] == "<p>2</p>"

    def test_author_none_defaults_to_user(self, empty_db):
        conn = empty_db.conn
        vid = zendesk_versions.capture_draft_version(
            conn, 41, title="t", body="b", body_html=None, author=None)
        assert zendesk_versions.get_draft_version(conn, vid)["author"] == "user"

    def test_per_draft_seq_independent(self, empty_db):
        conn = empty_db.conn
        for did in (61, 62):
            zendesk_versions.capture_draft_version(
                conn, did, title="t", body="b", body_html=None, author="user")
        assert zendesk_versions.list_draft_versions(conn, 61)[0]["seq"] == 1
        assert zendesk_versions.list_draft_versions(conn, 62)[0]["seq"] == 1

    def test_cap_50_prunes_oldest_saves(self, empty_db):
        conn = empty_db.conn
        ids = [zendesk_versions.capture_draft_version(
            conn, 71, title=f"t{i}", body=f"b{i}", body_html=None,
            author="user") for i in range(55)]
        saves = zendesk_versions.list_draft_versions(conn, 71, limit=100)
        assert len(saves) == 50
        got = [s["version_id"] for s in saves]
        assert got == sorted(got, reverse=True)
        assert got == list(reversed(ids[5:]))
        for old_id in ids[:5]:
            assert zendesk_versions.get_draft_version(conn, old_id) is None

    def test_capture_participates_in_caller_transaction(self, empty_db):
        conn = empty_db.conn
        conn.execute("BEGIN")
        vid = zendesk_versions.capture_draft_version(
            conn, 81, title="t", body="b", body_html=None, author="user")
        conn.rollback()
        assert zendesk_versions.get_draft_version(conn, vid) is None


# ── rollback ─────────────────────────────────────────────────────────

class TestRollbackDraft:
    @pytest.fixture()
    def draft_with_history(self, empty_db):
        conn = empty_db.conn
        did = zendesk_store.save_article_draft(
            conn, title="Title v1", body="body v1", body_html="<p>v1</p>")
        _clear_draft_versions(conn)
        v1 = zendesk_versions.capture_draft_version(
            conn, did, title="Title v1", body="body v1",
            body_html="<p>v1</p>", author="renn", save_kind="create")
        zendesk_store.update_article_draft(
            conn, did, title="Title v2", body="body v2",
            body_html="<p>v2</p>")
        v2 = zendesk_versions.capture_draft_version(
            conn, did, title="Title v2", body="body v2",
            body_html="<p>v2</p>", author="specialist")
        return conn, did, v1, v2

    def test_rollback_restores_byte_exact_and_records(self, draft_with_history):
        conn, did, v1, v2 = draft_with_history
        base_seq = max(s["seq"] for s in
                       zendesk_versions.list_draft_versions(conn, did))
        res = zendesk_versions.rollback_draft_to_version(conn, did, v1)
        assert res["ok"] is True and res["error"] is None
        d = zendesk_store.get_article_draft(conn, did)
        assert d["title"] == "Title v1"
        assert d["body"] == "body v1"
        assert d["body_html"] == "<p>v1</p>"
        rec = zendesk_versions.get_draft_version(conn, res["new_version_id"])
        assert rec["save_kind"] == "rollback"
        assert rec["rollback_of"] == v1
        assert rec["seq"] == base_seq + 1
        assert rec["author"] == "specialist"
        assert rec["title"] == "Title v1" and rec["body"] == "body v1"

    def test_status_never_written(self, draft_with_history):
        conn, did, v1, _v2 = draft_with_history
        before = zendesk_store.get_article_draft(conn, did)["status"]
        assert before == "pending"
        zendesk_versions.rollback_draft_to_version(conn, did, v1)
        assert zendesk_store.get_article_draft(conn, did)["status"] == before

    @pytest.mark.parametrize("status", ["ready", "copied", "pushed"])
    def test_non_pending_refused(self, draft_with_history, status):
        conn, did, v1, _v2 = draft_with_history
        _force_status(conn, did, status)
        count_before = len(zendesk_versions.list_draft_versions(conn, did))
        res = zendesk_versions.rollback_draft_to_version(conn, did, v1)
        assert res == {"ok": False, "new_version_id": None,
                       "error": "draft_not_editable"}
        d = zendesk_store.get_article_draft(conn, did)
        assert d["body"] == "body v2"        # untouched
        assert d["status"] == status
        assert len(zendesk_versions.list_draft_versions(conn, did)) == \
            count_before

    def test_wrong_draft_version_id_refused(self, draft_with_history):
        conn, did, v1, _v2 = draft_with_history
        other = zendesk_store.save_article_draft(conn, title="o", body="ob")
        res = zendesk_versions.rollback_draft_to_version(conn, other, v1)
        assert res == {"ok": False, "new_version_id": None,
                       "error": "version_not_found"}
        assert zendesk_store.get_article_draft(conn, other)["body"] == "ob"

    def test_missing_version_and_missing_draft(self, empty_db):
        conn = empty_db.conn
        res = zendesk_versions.rollback_draft_to_version(conn, 1, 999999)
        assert res["error"] == "version_not_found"
        vid = zendesk_versions.capture_draft_version(
            conn, 555, title="t", body="b", body_html=None, author="user")
        res = zendesk_versions.rollback_draft_to_version(conn, 555, vid)
        assert res == {"ok": False, "new_version_id": None,
                       "error": "draft_not_found"}


# ── orphan sweep ─────────────────────────────────────────────────────

class TestPruneOrphans:
    def test_sweeps_purged_content_keeps_live_and_copied(self, empty_db):
        conn = empty_db.conn
        zendesk_store.upsert_articles(conn, [dict(ARTICLE)])
        _capture_states(conn, 101, 2)

        pending = zendesk_store.save_article_draft(conn, title="p", body="b")
        copied = zendesk_store.save_article_draft(conn, title="c", body="b")
        zendesk_store.set_draft_status(conn, "article", copied, "copied")
        _clear_draft_versions(conn)
        for did in (pending, copied):
            zendesk_versions.capture_draft_version(
                conn, did, title="t", body="b", body_html=None,
                author="user", save_kind="create")

        # nothing orphaned yet
        assert zendesk_versions.prune_orphans(conn) == {
            "article_versions": 0, "draft_versions": 0}

        # scope='all' deletes the article + pending draft, retains 'copied'
        zendesk_store.purge_mirror(conn, scope="all")
        swept = zendesk_versions.prune_orphans(conn)
        assert swept == {"article_versions": 2, "draft_versions": 1}
        assert zendesk_versions.list_article_versions(conn, 101) == []
        assert zendesk_versions.list_draft_versions(conn, pending) == []
        # the copied draft's audit history survives the purge
        assert len(zendesk_versions.list_draft_versions(conn, copied)) == 1

    def test_delete_draft_then_sweep(self, empty_db):
        conn = empty_db.conn
        did = zendesk_store.save_article_draft(conn, title="t", body="b")
        keep = zendesk_store.save_article_draft(conn, title="k", body="b")
        _clear_draft_versions(conn)
        for d in (did, keep):
            zendesk_versions.capture_draft_version(
                conn, d, title="t", body="b", body_html=None, author="user")
        zendesk_store.delete_draft(conn, "article", did)
        swept = zendesk_versions.prune_orphans(conn)
        assert swept["draft_versions"] == 1
        assert zendesk_versions.list_draft_versions(conn, did) == []
        assert len(zendesk_versions.list_draft_versions(conn, keep)) == 1
