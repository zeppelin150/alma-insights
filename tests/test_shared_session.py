"""Renn's two surfaces share one session via the pointer file.

Audit finding 5: the Agent page and the Workbench assistant panel each called
create_session("enablement"), minting different session ids and fighting over
the .current_chat_session pointer, so the two surfaces held separate
transcripts. resolve_or_create_session reuses the pointed-at session so both
resolve to one conversation.
"""

import pytest

from src.services import chat_session as cs


def test_first_call_creates_and_writes_the_pointer(empty_db, tmp_path):
    sid = cs.resolve_or_create_session("enablement", empty_db.conn,
                                       pointer_dir=tmp_path)
    assert sid
    pointer = tmp_path / ".current_chat_session"
    assert pointer.exists() and pointer.read_text(encoding="utf-8").strip() == sid


def test_second_surface_reuses_the_same_session(empty_db, tmp_path):
    """The core fix: a second surface pointed at the same dir gets the SAME
    session, not a new one."""
    first = cs.resolve_or_create_session("enablement", empty_db.conn,
                                         pointer_dir=tmp_path)
    second = cs.resolve_or_create_session("enablement", empty_db.conn,
                                          pointer_dir=tmp_path)
    assert first == second, "the two surfaces got different sessions"
    n = empty_db.conn.execute(
        "SELECT COUNT(*) FROM chat_sessions").fetchone()[0]
    assert n == 1, "a duplicate session was created"


def test_stale_pointer_to_a_deleted_session_creates_a_fresh_one(empty_db,
                                                                tmp_path):
    (tmp_path / ".current_chat_session").write_text("does-not-exist",
                                                    encoding="utf-8")
    sid = cs.resolve_or_create_session("enablement", empty_db.conn,
                                       pointer_dir=tmp_path)
    assert sid != "does-not-exist"
    assert cs._session_exists(sid, empty_db.conn)


def test_no_pointer_dir_always_creates(empty_db):
    """Legacy callers (no pointer) keep minting fresh sessions."""
    a = cs.resolve_or_create_session("enablement", empty_db.conn)
    b = cs.resolve_or_create_session("enablement", empty_db.conn)
    assert a != b


def test_empty_pointer_file_creates(empty_db, tmp_path):
    (tmp_path / ".current_chat_session").write_text("   ", encoding="utf-8")
    sid = cs.resolve_or_create_session("enablement", empty_db.conn,
                                       pointer_dir=tmp_path)
    assert cs._session_exists(sid, empty_db.conn)
