"""guru_analytics sync + read API against a stub client and empty_db.

Covers: watermark advance + 1-day overlap, overlap dedup via event_key,
90-day prune, per-section failure isolation, verification refresh,
comment resolution detection, comment→task idempotency, read-API math,
and the demo seed.
"""

from datetime import datetime, timedelta, timezone

from src.data import guru_analytics as ga


def _iso(days_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


class StubGuru:
    def __init__(self):
        self.events = [
            {"type": "card-viewed", "user": "a@x.com", "eventDate": _iso(2),
             "properties": {"cardId": "c-sso"}},
            {"type": "card-viewed", "user": "b@x.com", "eventDate": _iso(1),
             "properties": {"cardId": "c-sso"}},
            {"type": "card-copied", "user": "a@x.com", "eventDate": _iso(1.5),
             "properties": {"cardId": "c-pay"}},
            {"type": "card-viewed", "user": "a@x.com", "eventDate": _iso(0.5),
             "properties": {"cardId": "c-pay"}},
        ]
        self.comments = {
            "c-sso": [
                {"id": "cm1", "content": "Stale step 3", "author": "j@x.com",
                 "created_at": _iso(3), "status": "OPEN"},
                {"id": "cm2", "content": "Add thresholds", "author": "s@x.com",
                 "created_at": _iso(2), "status": "OPEN"},
            ],
        }
        self.stats = {"cards": 100, "trusted": 80}
        self.unverified = [
            {"id": "c-sso", "title": "SSO Setup",
             "verification_state": "NEEDS_VERIFICATION",
             "verification_reason": "", "next_verification_date": _iso(-2),
             "verification_interval": "90 days", "last_verified": _iso(95),
             "last_modified": _iso(100), "comment_count": 2,
             "collection": "Provider Enablement", "collection_id": "co1"},
        ]
        self.analytics_calls = []
        self.fail_stats = False
        self.fail_comments_for = set()

    def get_team_id(self):
        return "team-1"

    def get_analytics(self, team_id, from_date=None, to_date=None, **kw):
        self.analytics_calls.append(from_date)
        return list(self.events)

    def get_team_stats(self, team_id):
        if self.fail_stats:
            raise RuntimeError("boom")
        return dict(self.stats)

    def list_unverified_cards(self, **kw):
        return [dict(c) for c in self.unverified]

    def get_card_comments(self, card_id, status=None, **kw):
        if card_id in self.fail_comments_for:
            raise RuntimeError(
                "Guru API error 400: Comment operations can not be "
                "applied to a deleted card")
        return [dict(c) for c in self.comments.get(card_id, [])]


class TestSync:
    def test_inserts_and_watermark(self, empty_db):
        conn = empty_db.conn
        stub = StubGuru()
        summary = ga.sync(conn, stub)
        assert summary["ok"], summary
        assert summary["sections"]["events"]["inserted"] == 4
        wm = ga._get_state(conn, "events_watermark")
        assert wm == max(e["eventDate"] for e in stub.events)

    def test_resync_dedups_and_overlaps(self, empty_db):
        conn = empty_db.conn
        stub = StubGuru()
        ga.sync(conn, stub)
        summary = ga.sync(conn, stub)
        assert summary["sections"]["events"]["inserted"] == 0
        # second call started from watermark minus ~1 day, not days_back
        second_from = stub.analytics_calls[1]
        wm = ga._get_state(conn, "events_watermark")
        assert second_from < wm
        delta = (ga._parse_dt(wm) - ga._parse_dt(second_from)).total_seconds()
        assert 0 < delta <= 90_000  # ≈ 1 day + a bit

    def test_prune_old_events(self, empty_db):
        conn = empty_db.conn
        conn.execute(
            "INSERT INTO guru_events (event_key, event_type, event_date, "
            "card_id) VALUES ('old', 'card-viewed', ?, 'c-old')",
            (_iso(120),),
        )
        conn.commit()
        ga.sync(conn, StubGuru())
        n = conn.execute(
            "SELECT COUNT(*) FROM guru_events WHERE event_key='old'"
        ).fetchone()[0]
        assert n == 0

    def test_section_isolation(self, empty_db):
        conn = empty_db.conn
        stub = StubGuru()
        stub.fail_stats = True
        summary = ga.sync(conn, stub)
        assert not summary["ok"]
        assert "error" in summary["sections"]["stats"]
        assert summary["sections"]["events"]["inserted"] == 4
        assert ga._get_state(conn, "events_watermark")  # still advanced

    def test_comment_card_failure_isolated(self, empty_db):
        # A card deleted in Guru since its event fired 400s on
        # get_card_comments. That must not abort the comments section,
        # zero the cards that did sync, or flip the whole sync to ok=False.
        conn = empty_db.conn
        stub = StubGuru()
        # c-del is the most-active card (3 events) so the loop hits it
        # first; c-sso's comments must still sync after it raises.
        stub.events += [
            {"type": "card-viewed", "user": "a@x.com", "eventDate": _iso(0.4),
             "properties": {"cardId": "c-del"}},
            {"type": "card-viewed", "user": "b@x.com", "eventDate": _iso(0.3),
             "properties": {"cardId": "c-del"}},
            {"type": "card-viewed", "user": "c@x.com", "eventDate": _iso(0.2),
             "properties": {"cardId": "c-del"}},
        ]
        stub.fail_comments_for = {"c-del"}
        summary = ga.sync(conn, stub)
        assert summary["ok"], summary
        sec = summary["sections"]["comments"]
        assert "error" not in sec, sec
        assert sec["skipped"] == 1
        assert sec["synced"] == 2
        assert {c["comment_id"] for c in ga.open_comments(conn)} == {"cm1", "cm2"}

    def test_comment_resolution_detection(self, empty_db):
        conn = empty_db.conn
        stub = StubGuru()
        ga.sync(conn, stub)
        assert {c["comment_id"] for c in ga.open_comments(conn)} == {"cm1", "cm2"}
        stub.comments["c-sso"] = [stub.comments["c-sso"][0]]  # cm2 resolved
        ga.sync(conn, stub)
        open_ids = {c["comment_id"] for c in ga.open_comments(conn)}
        assert open_ids == {"cm1"}


class TestReadApi:
    def _synced(self, empty_db):
        conn = empty_db.conn
        ga.sync(conn, StubGuru())
        return conn

    def test_top_cards_math(self, empty_db):
        conn = self._synced(empty_db)
        cards = ga.top_cards(conn, days=30)
        by_id = {c["card_id"]: c for c in cards}
        assert by_id["c-sso"]["views"] == 2
        assert by_id["c-pay"]["views"] == 1
        assert by_id["c-pay"]["copies"] == 1
        assert cards[0]["card_id"] == "c-sso"
        # title joined from the verification table
        assert by_id["c-sso"]["title"] == "SSO Setup"

    def test_verification_kpis(self, empty_db):
        conn = self._synced(empty_db)
        kpis = ga.verification_kpis(conn)
        assert kpis["states"] == {"NEEDS_VERIFICATION": 1}
        assert kpis["queue_total"] == 1
        assert kpis["due_soon"] == 1
        assert kpis["open_comments"] == 2
        assert kpis["team_stats"]["cards"] == 100

    def test_cards_due_for_update(self, empty_db):
        conn = self._synced(empty_db)
        due = ga.cards_due_for_update(conn)
        assert due and due[0]["card_id"] == "c-sso"
        assert due[0]["reason"] == "unverified"

    def test_comment_to_task_idempotent(self, empty_db):
        conn = self._synced(empty_db)
        a = ga.create_task_from_comment(conn, "cm1")
        b = ga.create_task_from_comment(conn, "cm1")
        assert a["ok"] and b["ok"]
        assert a["task_id"] == b["task_id"]
        assert b.get("already")
        row = conn.execute(
            "SELECT source, kind FROM enablement_tasks WHERE task_id=?",
            (a["task_id"],),
        ).fetchone()
        assert tuple(row) == ("guru", "card_comment")
        n = conn.execute(
            "SELECT COUNT(*) FROM enablement_tasks WHERE dedup_key=?",
            ("guru_comment:cm1",),
        ).fetchone()[0]
        assert n == 1

    def test_missing_comment(self, empty_db):
        conn = self._synced(empty_db)
        assert not ga.create_task_from_comment(conn, "nope")["ok"]


class TestDemoSeed:
    def test_seed_renders_every_section(self, empty_db):
        from src.data.enablement_sim import seed_demo_analytics
        conn = empty_db.conn
        out = seed_demo_analytics(conn)
        assert out["events"] > 50
        assert len(ga.top_cards(conn, days=30)) >= 4
        kpis = ga.verification_kpis(conn)
        assert kpis["queue_total"] == 3
        assert kpis["open_comments"] == 4
        assert ga.cards_due_for_update(conn)
        assert ga.last_sync_at(conn)
