"""Tests for the managed documents tree (app_paths) + docs_backfill.

Headless — no Qt. The tree root is monkeypatched under tmp_path so no test
touches the real data/documents/.
"""

from pathlib import Path

import pytest

from src.data import app_paths, docs_backfill


@pytest.fixture()
def docs_tmp(tmp_path, monkeypatch):
    root = tmp_path / "docs"
    monkeypatch.setattr(app_paths, "_DEFAULT_ROOT", root)
    monkeypatch.setattr(app_paths, "_settings_root", lambda: None)
    return root


# ── tree creation ────────────────────────────────────────────────────

def test_ensure_docs_tree_creates_all_spaces(docs_tmp):
    report = app_paths.ensure_docs_tree()
    assert {r["name"] for r in report} == set(app_paths.SPACES)
    for r in report:
        assert r["created"] is True, f"{r['name']} should be new"
        assert Path(r["path"]).is_dir()
        assert Path(r["path"]).parent == docs_tmp
    assert (docs_tmp / "README.txt").is_file()


def test_ensure_docs_tree_idempotent(docs_tmp):
    app_paths.ensure_docs_tree()
    report = app_paths.ensure_docs_tree()
    assert all(r["created"] is False for r in report), "second run must be a no-op"


def test_accessors_lazy_mkdir(docs_tmp):
    path = app_paths.zendesk_import_dir()
    assert path.is_dir()
    assert path == docs_tmp / "Zendesk Imports"
    assert app_paths.downloads_dir() == docs_tmp / "Downloads"
    assert app_paths.exports_dir() == docs_tmp / "Exports"
    assert app_paths.zendesk_edits_dir() == docs_tmp / "Zendesk Edits"
    assert app_paths.worksheets_dir() == docs_tmp / "Worksheets"


def test_space_dir_unknown_raises(docs_tmp):
    with pytest.raises(ValueError):
        app_paths.space_dir("attic")


def test_settings_root_override(tmp_path, monkeypatch):
    override = tmp_path / "elsewhere"
    monkeypatch.setattr(app_paths, "_settings_root", lambda: override)
    assert app_paths.docs_root() == override
    assert override.is_dir()


def test_start_dir_degrades_on_error(docs_tmp, monkeypatch):
    assert app_paths.start_dir("downloads") == str(docs_tmp / "Downloads")
    assert app_paths.start_dir("exports", "deck.pptx") == \
        str(docs_tmp / "Exports" / "deck.pptx")

    def boom(name):
        raise OSError("disk full")
    monkeypatch.setattr(app_paths, "space_dir", boom)
    assert app_paths.start_dir("downloads", "a.md") == "a.md", \
        "a filesystem error must never break a save dialog"
    assert app_paths.start_dir("downloads") == ""


# ── backfill ─────────────────────────────────────────────────────────

@pytest.fixture()
def fake_artifacts(tmp_path, monkeypatch):
    root = tmp_path / "artifacts"
    adir = root / "abcdef1234567890"
    adir.mkdir(parents=True)
    (adir / "my_deck.pptx").write_bytes(b"pptx-bytes")
    (adir / "ignore.bin").write_bytes(b"nope")
    monkeypatch.setattr(docs_backfill, "_artifacts_root", lambda: root)
    return root


def test_backfill_copies_artifacts_idempotently(docs_tmp, fake_artifacts, empty_db):
    report = docs_backfill.run_backfill(empty_db.conn)
    assert report["errors"] == []
    assert report["exports_copied"] == 1
    copied = list((docs_tmp / "Exports").iterdir())
    assert [p.name for p in copied] == ["abcdef1234567890_my_deck.pptx"], \
        "full artifact id must prefix exports so two artifacts can never merge"

    report = docs_backfill.run_backfill(empty_db.conn)
    assert report["exports_copied"] == 0
    assert report["exports_skipped"] == 1, "unchanged files must not re-copy"


def test_backfill_exports_zendesk_drafts(docs_tmp, empty_db, monkeypatch):
    monkeypatch.setattr(docs_backfill, "_artifacts_root",
                        lambda: docs_tmp / "no-artifacts")
    from src.data import zendesk_store
    conn = empty_db.conn
    zendesk_store.save_article_draft(
        conn, title="Prior auth portal steps", body="body text",
        body_html="<p>portal steps</p>", rationale="portal replaced fax",
    )
    report = docs_backfill.run_backfill(conn)
    assert report["errors"] == []
    assert report["zendesk_edits_written"] == 1
    files = list((docs_tmp / "Zendesk Edits").glob("*.md"))
    assert len(files) == 1
    text = files[0].read_text(encoding="utf-8")
    assert "Prior auth portal steps" in text
    assert "portal replaced fax" in text
    assert "<p>portal steps</p>" in text

    report = docs_backfill.run_backfill(conn)
    assert report["zendesk_edits_written"] == 0
    assert report["zendesk_edits_skipped"] == 1, "unchanged drafts must not rewrite"


def test_backfill_exports_body_only_article_and_macro_drafts(docs_tmp, empty_db,
                                                             monkeypatch):
    """The canonical content columns: article `body` (body_html optional),
    macro comment_value action (reply_html optional) — both must export."""
    monkeypatch.setattr(docs_backfill, "_artifacts_root",
                        lambda: docs_tmp / "no-artifacts")
    from src.data import zendesk_store
    conn = empty_db.conn
    zendesk_store.save_article_draft(
        conn, title="Doc-derived draft", body="markdown body only",
        rationale="from doc_reader",
    )
    zendesk_store.save_macro_draft(
        conn, name="Eligibility macro",
        actions=[{"field": "comment_value", "value": "canned eligibility reply"}],
        rationale="renn proposal",
    )
    report = docs_backfill.run_backfill(conn)
    assert report["errors"] == []
    assert report["zendesk_edits_written"] == 2
    texts = {p.name: p.read_text(encoding="utf-8")
             for p in (docs_tmp / "Zendesk Edits").glob("*.md")}
    art = next(t for n, t in texts.items() if n.startswith("article-"))
    mac = next(t for n, t in texts.items() if n.startswith("macro-"))
    assert "markdown body only" in art, "article `body` column must export"
    assert "canned eligibility reply" in mac, \
        "macro comment_value action must export"


def test_backfill_crlf_draft_stays_idempotent(docs_tmp, empty_db, monkeypatch):
    monkeypatch.setattr(docs_backfill, "_artifacts_root",
                        lambda: docs_tmp / "no-artifacts")
    from src.data import zendesk_store
    conn = empty_db.conn
    zendesk_store.save_article_draft(
        conn, title="CRLF draft", body="line1\r\nline2",
        rationale="api-pulled content keeps CR",
    )
    first = docs_backfill.run_backfill(conn)
    assert first["zendesk_edits_written"] == 1
    second = docs_backfill.run_backfill(conn)
    assert second["zendesk_edits_written"] == 0, \
        "CR-bearing drafts must not rewrite on every run"
    assert second["zendesk_edits_skipped"] == 1
    data = next((docs_tmp / "Zendesk Edits").glob("*.md")).read_bytes()
    assert b"line1\r\nline2" in data, "draft bytes must round-trip uncorrupted"


def test_backfill_never_raises_without_db(docs_tmp, fake_artifacts, monkeypatch):
    def no_conn():
        raise OSError("no warehouse here")
    monkeypatch.setattr(docs_backfill, "_default_conn", no_conn)
    report = docs_backfill.run_backfill()
    assert report["exports_copied"] == 1, "artifact copy must survive a DB failure"
    assert any(e.startswith("zendesk:") for e in report["errors"])
