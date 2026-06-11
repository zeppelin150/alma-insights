"""Enablement pipeline simulation — data flow in → transform → out, no live creds.

Feeds mock "Drive" documents through the real store/draft/task path so the
end-to-end ETL can be exercised headlessly (tests, demos, manual smoke runs):

    Drive pull (mock)  →  enablement_documents (stored, full text)
                       →  draft_card_from_document (mock/real LLM)
                       →  guru_content_drafts (pending)
                       →  enablement_tasks (card_review)
                       →  publish_draft (mock/real Guru)  [optional]

Run from a shell for a manual check:
    python -m src.data.enablement_sim
"""

from __future__ import annotations

import re
import sqlite3

from src.data import enablement_store as store
from src.data import enablement_tasks as tasks

# ── mock "business Drive" contents (data in) ─────────────────────────

MOCK_DOCS: list[dict] = [
    {
        "id": "drv_sso_setup",
        "name": "SSO Setup.gdoc",
        "mime_type": "application/vnd.google-apps.document",
        "web_url": "https://docs.google.com/document/d/drv_sso_setup",
        "modified_time": "2026-06-08T09:12:00Z",
        "text": (
            "Provider SSO Self-Serve — Launch June 24, 2026.\n"
            "Providers can configure SSO from Admin Console > Security > SSO. "
            "Steps: choose IdP (Okta, Azure AD, Google), upload metadata XML, "
            "test with a pilot org, then enable org-wide. Existing logins are "
            "unaffected until SSO is enabled."
        ),
    },
    {
        "id": "drv_returns_policy",
        "name": "Returns Policy.gdoc",
        "mime_type": "application/vnd.google-apps.document",
        "web_url": "https://docs.google.com/document/d/drv_returns_policy",
        "modified_time": "2026-06-07T15:40:00Z",
        "text": (
            "Returns Policy v3 — effective July 1, 2026. Window extended from 14 "
            "to 30 days. Refund SLA is now 5 business days. Enablement to refresh "
            "the provider-facing returns card before the July 1 deadline."
        ),
    },
    {
        "id": "drv_payments_v2",
        "name": "Payments v2 Notes.pdf",
        "mime_type": "application/pdf",
        "web_url": "https://drive.google.com/file/d/drv_payments_v2",
        "modified_time": "2026-06-06T11:05:00Z",
        "text": (
            "Payments v2 rollout. New settlement timing and a redesigned payout "
            "dashboard. Targeted for Q3. Enablement should prepare a walkthrough "
            "card covering payout schedules and the new dashboard."
        ),
    },
]

_DATE_RE = re.compile(
    r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},?\s+\d{4}\b"
)


def simulate_drive_pull(docs: list[dict] | None = None) -> list[dict]:
    """Stand in for DriveMonitor: return the documents 'found' in the Drive."""
    return list(docs if docs is not None else MOCK_DOCS)


def _extract_dates(text: str) -> list[dict]:
    return [{"date": m, "context": "extracted"} for m in _DATE_RE.findall(text or "")]


class _StubLLM:
    """Deterministic offline LLM so the sim runs with no provider/creds."""

    def generate(self, prompt: str, *_, **__) -> str:
        m = re.search(r"SOURCE DOCUMENT:\s*(.+)", prompt)
        name = (m.group(1).strip() if m else "Document").rsplit(".", 1)[0]
        return (
            f"TITLE: {name} — Enablement Guide\n"
            "---\n"
            f"This card summarizes **{name}** for the enablement team.\n\n"
            "## Summary\nKey changes captured from the source document.\n\n"
            "## Steps\n1. Review the change.\n2. Update training material.\n"
            "3. Publish and announce.\n\n"
            "## FAQ\n**Does this change existing behavior?** See the source doc."
        )


def run_simulation(
    conn: sqlite3.Connection,
    docs: list[dict] | None = None,
    *,
    llm_client=None,
    guru_client=None,
    publish: bool = False,
    collection: str = "Provider Enablement",
    collection_id: str = "coll-enablement",
) -> dict:
    """Run the full Drive→card→task pipeline over `docs`. Returns a summary.

    Idempotent: re-running with the same docs re-indexes documents in place and
    dedups tasks (so it's safe to call repeatedly in a poll loop or a test).
    """
    llm = llm_client or _StubLLM()
    pulled = simulate_drive_pull(docs)

    out = {"documents": [], "drafts": [], "tasks": [], "published": []}
    for d in pulled:
        doc_id = store.save_document(
            conn,
            source="drive",
            doc_id=d["id"],
            source_ref=d["id"],
            name=d["name"],
            mime_type=d.get("mime_type", ""),
            web_url=d.get("web_url", ""),
            modified_time=d.get("modified_time"),
            full_text=d.get("text", ""),
            due_dates=_extract_dates(d.get("text", "")),
        )
        out["documents"].append(doc_id)

        draft = store.draft_card_from_document(conn, doc_id, llm, collection=collection)
        out["drafts"].append(draft["id"])

        task_id = tasks.create_task(
            conn,
            source="drive",
            kind="card_review",
            title=f"Draft Guru card from “{d['name']}”",
            source_ref=doc_id,
            source_url=d.get("web_url"),
            summary=f"AI-drafted a card from {d['name']}. Review and publish.",
            draft_id=draft["id"],
            llm_rationale="New/changed product doc detected in the watched Drive folder.",
        )
        out["tasks"].append(task_id)

        if publish:
            res = store.publish_draft(
                conn, draft["id"], guru_client=guru_client, collection_id=collection_id
            )
            if res.get("ok"):
                out["published"].append(draft["id"])
                tasks.update_task(conn, task_id, status="done")

    return out


def seed_demo_tasks(conn) -> list[dict]:
    """Enrich the scan's card-review tasks + add a few cross-source tasks so the
    Task list and Calendar have varied, realistic demo content. Idempotent.
    Returns the full task list (with subtask counts)."""
    from src.data import enablement_tasks as tasks

    # Enrich the SSO card-review task (due date + subtasks + scratch pad).
    for t in tasks.list_tasks(conn):
        if "SSO Setup" in t["title"] and t["kind"] == "card_review":
            if t.get("subtask_total", 0) == 0:
                tasks.update_task(conn, t["task_id"], due_date="2026-06-24", priority="high")
                for text, done in [("Draft card from doc", True), ("Add product screenshots", True),
                                   ("SME technical review", False), ("Publish to Guru", False),
                                   ("Announce in #enablement-updates", False)]:
                    tasks.add_subtask(conn, t["task_id"], text, created_by="agent", done=done)
                tasks.set_scratchpad(conn, t["task_id"],
                                     "Waiting on final launch date from Product (Maya).\n"
                                     "Confirm rollout regions before publishing.\n"
                                     "Pilot org: acme-health (test SSO first).")
            break

    extras = [
        dict(source="asana", kind="request", title="Triage: SSO bulk-provisioning request",
             due_date="2026-06-15", priority="high", assignee="J. Rivera",
             subs=[("Confirm scope with requester", True), ("Draft response", False), ("Loop in IT", False)]),
        dict(source="guru", kind="product_update", title="Payments v2 — product update posted to Guru",
             priority="normal", subs=[]),
        dict(source="drive", kind="doc_due_date", title="Index Q3 launch deadlines from “Returns Policy.gdoc”",
             due_date="2026-06-20", priority="normal",
             subs=[("Extract milestone dates", False), ("Create calendar entries", False)]),
        dict(source="drive", kind="card_review", title="Onboarding deck — extract milestone dates",
             due_date="2026-06-12", priority="high", subs=[]),
    ]
    for e in extras:
        tid = tasks.create_task(conn, source=e["source"], kind=e["kind"], title=e["title"],
                                due_date=e.get("due_date"), priority=e.get("priority", "normal"),
                                created_by="agent")
        if e.get("assignee"):
            tasks.update_task(conn, tid, assignee=e["assignee"])
        if not tasks.list_subtasks(conn, tid):
            for text, done in e.get("subs", []):
                tasks.add_subtask(conn, tid, text, created_by="agent", done=done)

    return tasks.list_tasks(conn)


if __name__ == "__main__":  # pragma: no cover — manual smoke run
    from src.data.connection_factory import get_connection
    c = get_connection()
    summary = run_simulation(c, publish=True)
    print("Simulation summary:", summary)
    print("Docs in store:", len(store.list_documents(c)))
    print("Lookup 'SSO':", [d["name"] for d in store.search_documents(c, "SSO")])


# ── demo analytics (Analytics page in demo mode) ─────────────────────

_DEMO_ANALYTICS_CARDS = [
    ("demo-card-sso", "Setting up SSO for Providers", "Provider Enablement", 9),
    ("demo-card-payments", "Payments v2 Overview", "Product", 7),
    ("demo-card-returns", "Returns & Refunds Policy", "CX", 5),
    ("demo-card-claims", "Claims Resubmission Guide", "CX", 3),
    ("demo-card-pricing", "Q3 Pricing Tiers", "Product", 2),
]


def seed_demo_analytics(conn) -> dict:
    """Deterministic synthetic Guru analytics for demo mode.

    30 days of view/copy events over five demo cards, a stats snapshot,
    a verification queue with two due cards, and four open comments —
    enough for every Analytics section to render.
    """
    import json
    import random
    from datetime import datetime, timedelta, timezone

    from src.data.connection_factory import atomic

    rng = random.Random(2026)
    now = datetime.now(timezone.utc)
    inserted = 0
    with atomic(conn):
        for day in range(30):
            date = now - timedelta(days=29 - day)
            for cid, _title, _coll, weight in _DEMO_ANALYTICS_CARDS:
                views = rng.randint(0, weight)
                for n in range(views):
                    ts = (date + timedelta(minutes=7 * n)).isoformat()
                    conn.execute(
                        "INSERT OR IGNORE INTO guru_events "
                        "(event_key, event_type, user_email, event_date, "
                        " card_id, properties, fetched_at) VALUES (?,?,?,?,?,?,?)",
                        (f"demo-{cid}-{day}-{n}", "card-viewed",
                         "demo@alma.com", ts, cid, "{}", now.isoformat()),
                    )
                    inserted += 1
                if rng.random() < 0.18:
                    conn.execute(
                        "INSERT OR IGNORE INTO guru_events "
                        "(event_key, event_type, user_email, event_date, "
                        " card_id, properties, fetched_at) VALUES (?,?,?,?,?,?,?)",
                        (f"demo-copy-{cid}-{day}", "card-copied",
                         "demo@alma.com", date.isoformat(), cid, "{}",
                         now.isoformat()),
                    )
                    inserted += 1

        conn.execute(
            "INSERT OR REPLACE INTO guru_team_stats(snapshot_at, stats_json) "
            "VALUES (?,?)",
            (now.isoformat(), json.dumps({"cards": 412, "trusted": 318,
                                          "needsVerification": 61,
                                          "stale": 33})),
        )

        verif = [
            ("demo-card-sso", "NEEDS_VERIFICATION", 2),
            ("demo-card-returns", "STALE", 6),
            ("demo-card-claims", "TRUSTED", 21),
        ]
        for cid, state, due_days in verif:
            title = next(t for c, t, _co, _w in _DEMO_ANALYTICS_CARDS if c == cid)
            coll = next(co for c, _t, co, _w in _DEMO_ANALYTICS_CARDS if c == cid)
            conn.execute(
                "INSERT OR REPLACE INTO guru_card_verification "
                "(card_id, title, collection_id, collection_name, "
                " verification_state, verification_reason, "
                " next_verification_date, verification_interval, "
                " last_verified_at, last_modified, comment_count, fetched_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (cid, title, f"coll-{coll.lower().replace(' ', '-')}", coll,
                 state, "", (now + timedelta(days=due_days)).isoformat(),
                 "90 days", (now - timedelta(days=80)).isoformat(),
                 (now - timedelta(days=95)).isoformat(), 1, now.isoformat()),
            )

        comments = [
            ("demo-comment-1", "demo-card-sso", "jordan@alma.com",
             "The Okta steps changed in the June release — step 3 screenshot is stale."),
            ("demo-comment-2", "demo-card-payments", "sam@alma.com",
             "Can we add the ERA auto-match thresholds here?"),
            ("demo-comment-3", "demo-card-returns", "alex@alma.com",
             "Policy says 30 days but the portal enforces 45 — which is right?"),
            ("demo-comment-4", "demo-card-sso", "casey@alma.com",
             "Worth a callout that pilot orgs keep legacy login until cutover."),
        ]
        for n, (cmid, cid, author, text) in enumerate(comments):
            title = next(t for c, t, _co, _w in _DEMO_ANALYTICS_CARDS if c == cid)
            conn.execute(
                "INSERT OR REPLACE INTO guru_card_comments "
                "(comment_id, card_id, card_title, author, text, created_at, "
                " status, fetched_at) VALUES (?,?,?,?,?,?,?,?)",
                (cmid, cid, title, author, text,
                 (now - timedelta(days=3 + n)).isoformat(), "OPEN",
                 now.isoformat()),
            )

        conn.execute(
            "INSERT INTO guru_sync_state(key, value, updated_at) "
            "VALUES('last_sync_at', ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
            "updated_at=excluded.updated_at",
            (now.isoformat(), now.isoformat()),
        )
    return {"events": inserted, "comments": len(comments)}
