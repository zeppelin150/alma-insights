"""Tests for src/data/canonical_label_generator.py (Phase 5)."""

from __future__ import annotations

import sqlite3
import struct
from pathlib import Path

import pytest

from src.data.canonical_label_generator import (
    ClusterContext,
    LabelDecision,
    call_llm_for_labels,
    decide_label,
    extractive_label,
    generate_labels,
    load_cluster_contexts,
    medoid_label,
    validate_label,
    validate_label_grounding,
    validate_label_pii,
    validate_label_schema,
    _extract_json_object,
)
from src.data.db_manager import DatabaseManager


# ──────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────


@pytest.fixture
def fresh_db(tmp_path: Path):
    db_path = tmp_path / "label.db"
    mgr = DatabaseManager(db_path)
    mgr.initialize()
    conn = mgr.get_connection()
    conn.execute("PRAGMA foreign_keys = OFF")
    yield conn
    try:
        conn.close()
    except Exception:
        pass


def _zero_blob(dim: int = 1024) -> bytes:
    return struct.pack(f"<{dim}f", *([0.0] * dim))


def _insert_cluster(
    conn,
    *,
    cluster_id: str,
    trc: str = "Billing issue",
    label: str | None = None,
    label_source: str | None = None,
    member_count: int = 5,
    rep_id: str | None = None,
):
    conn.execute(
        """
        INSERT INTO canonical_clusters
          (cluster_id, trc, canonical_label, label_source,
           centroid_blob, representative_ticket_id, member_count, discovered_scan_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'scan-x')
        """,
        (cluster_id, trc, label, label_source, _zero_blob(), rep_id, member_count),
    )
    conn.commit()


def _insert_classification(
    conn,
    *,
    ticket_id: str,
    trc: str,
    sub_cluster: str,
    key_phrases: list[str],
    summary: str = "",
):
    import json as _json
    conn.execute(
        """
        INSERT INTO nlp_ticket_classifications
          (classification_id, batch_id, scan_id, ticket_id, trc, sub_cluster,
           sub_cluster_confidence, key_phrases, summary, created_at)
        VALUES (?, 'b', 'scan-x', ?, ?, ?, 0.8, ?, ?, CURRENT_TIMESTAMP)
        """,
        (f"c-{ticket_id}", ticket_id, trc, sub_cluster, _json.dumps(key_phrases), summary),
    )


def _insert_ticket_index(conn, *, ticket_id: str, trc: str, snippet: str, canonical_issue_id: str):
    conn.execute(
        """
        INSERT INTO ticket_index
          (ticket_id, first_seen_scan_id, last_seen_scan_id, first_seen_date,
           trc_code, trc_label, issue_snippet, canonical_issue_id,
           classification_confidence)
        VALUES (?, 'scan-x', 'scan-x', '2026-01-01', ?, ?, ?, ?, 0.8)
        """,
        (ticket_id, trc, trc, snippet, canonical_issue_id),
    )


# ──────────────────────────────────────────────────────────────────────
# L1 schema validator
# ──────────────────────────────────────────────────────────────────────


class TestSchemaValidator:
    def test_valid_label(self):
        r = validate_label_schema("Claim eligibility mismatch dispute")
        assert r.valid, r.reason

    def test_too_short(self):
        assert not validate_label_schema("Too short").valid
        assert not validate_label_schema("").valid

    def test_too_long(self):
        label = "A B C D E F G H I J"  # 10 words
        assert not validate_label_schema(label).valid

    def test_abstain(self):
        assert not validate_label_schema("ABSTAIN").valid

    def test_strip_trailing_punct(self):
        # Trailing period stripped OK, but ? should fail
        assert validate_label_schema("Claim eligibility dispute.").valid
        assert not validate_label_schema("Claim eligibility dispute?").valid

    def test_control_chars(self):
        assert not validate_label_schema("Claim\ndispute resolved").valid


# ──────────────────────────────────────────────────────────────────────
# L2 grounding validator
# ──────────────────────────────────────────────────────────────────────


class TestGroundingValidator:
    def test_phrase_match(self):
        ctx = ClusterContext(
            cluster_id="c1", trc="Billing", existing_label=None,
            existing_source=None, member_count=10, representative_ticket_id=None,
            key_phrases=["eligibility dispute", "portal error"],
        )
        assert validate_label_grounding("Patient eligibility dispute review", ctx).valid

    def test_bigram_overlap(self):
        ctx = ClusterContext(
            cluster_id="c1", trc="B", existing_label=None, existing_source=None,
            member_count=10, representative_ticket_id=None,
            key_phrases=["provider payout rate", "insurance carrier"],
        )
        assert validate_label_grounding("Provider payout rate dispute", ctx).valid

    def test_no_overlap_fails(self):
        ctx = ClusterContext(
            cluster_id="c1", trc="B", existing_label=None, existing_source=None,
            member_count=10, representative_ticket_id=None,
            key_phrases=["alpha bravo", "charlie delta"],
        )
        r = validate_label_grounding("Foxtrot golf hotel issue", ctx)
        assert not r.valid
        assert r.layer == "L2_grounding"

    def test_snippet_fallback(self):
        ctx = ClusterContext(
            cluster_id="c1", trc="B", existing_label=None, existing_source=None,
            member_count=10, representative_ticket_id=None,
            key_phrases=["zzz"],  # no match
            snippets=["Provider says payout rate is too low for this session."],
        )
        assert validate_label_grounding("Provider payout rate concern", ctx).valid


# ──────────────────────────────────────────────────────────────────────
# L3 PII validator
# ──────────────────────────────────────────────────────────────────────


class TestPIIValidator:
    def test_clean(self):
        assert validate_label_pii("Claim eligibility dispute").valid

    def test_reject_ssn(self):
        assert not validate_label_pii("Claim 123-45-6789 dispute").valid

    def test_reject_amount(self):
        assert not validate_label_pii("Claim $150.00 dispute billing").valid

    def test_reject_email(self):
        assert not validate_label_pii("Claim @alice dispute").valid

    def test_reject_iso_date(self):
        assert not validate_label_pii("Outage 2026-04-14 impact review").valid


# ──────────────────────────────────────────────────────────────────────
# Extractive fallback
# ──────────────────────────────────────────────────────────────────────


class TestExtractive:
    def test_picks_top_phrase(self):
        ctx = ClusterContext(
            cluster_id="c1", trc="B", existing_label=None, existing_source=None,
            member_count=10, representative_ticket_id=None,
            key_phrases=[
                "insurance eligibility dispute",
                "portal ineligible error",
                "insurance eligibility dispute",
                "policy active status",
            ],
        )
        label = extractive_label(ctx)
        assert label is not None
        assert len(label.split()) >= 3

    def test_returns_none_on_empty(self):
        ctx = ClusterContext(
            cluster_id="c1", trc="B", existing_label=None, existing_source=None,
            member_count=10, representative_ticket_id=None,
            key_phrases=[], snippets=[],
        )
        assert extractive_label(ctx) is None


# ──────────────────────────────────────────────────────────────────────
# Medoid fallback
# ──────────────────────────────────────────────────────────────────────


class TestMedoid:
    def test_returns_trc_if_valid(self):
        ctx = ClusterContext(
            cluster_id="c1", trc="Provider payout rate dispute",
            existing_label=None, existing_source=None,
            member_count=10, representative_ticket_id=None,
        )
        assert medoid_label(ctx) == "Provider payout rate dispute"

    def test_prefers_existing_label(self):
        ctx = ClusterContext(
            cluster_id="c1", trc="Too short",
            existing_label="Claim eligibility dispute process",
            existing_source="medoid",
            member_count=10, representative_ticket_id=None,
        )
        assert medoid_label(ctx) == "Claim eligibility dispute process"

    def test_truncates_long_strings(self):
        ctx = ClusterContext(
            cluster_id="c1",
            trc="Provider believes the payout amount reported is absolutely incorrect today",
            existing_label=None, existing_source=None, member_count=10,
            representative_ticket_id=None,
        )
        r = medoid_label(ctx)
        # Either truncated to 6 words, or the original if ≤ 8
        assert r is not None
        assert 3 <= len(r.split()) <= 8


# ──────────────────────────────────────────────────────────────────────
# decide_label fallback chain
# ──────────────────────────────────────────────────────────────────────


class TestDecideLabel:
    def _ctx(self, **kw):
        defaults = dict(
            cluster_id="c1", trc="Billing dispute", existing_label="Billing dispute",
            existing_source="medoid", member_count=10, representative_ticket_id=None,
            key_phrases=["insurance eligibility", "payout rate dispute"],
            snippets=["Patient says eligibility was denied on portal."],
        )
        defaults.update(kw)
        return ClusterContext(**defaults)

    def test_prefers_valid_llm(self):
        ctx = self._ctx()
        llm_out = {
            "canonical_label": "Insurance eligibility denial dispute",
            "confidence": 0.85,
            "rationale": "matches key_phrases",
            "reused_existing": False,
            "grounded_phrases": ["insurance eligibility"],
        }
        d = decide_label(ctx, llm_out)
        assert d.final_source == "llm"
        assert d.final_label == "Insurance eligibility denial dispute"

    def test_llm_reused_existing(self):
        ctx = self._ctx()
        llm_out = {
            "canonical_label": "Payout rate dispute review",
            "confidence": 0.9, "rationale": "", "reused_existing": True,
        }
        d = decide_label(ctx, llm_out)
        assert d.final_source == "existing_retained"

    def test_llm_abstain_falls_through(self):
        ctx = self._ctx()
        d = decide_label(ctx, {"canonical_label": "ABSTAIN"})
        assert d.final_source in ("extractive", "medoid", "abstain")
        assert d.final_label

    def test_llm_invalid_falls_through_to_extractive(self):
        ctx = self._ctx()
        # Invalid (1 word)
        d = decide_label(ctx, {"canonical_label": "TooShort"})
        assert d.final_source in ("extractive", "medoid")
        assert any(f.startswith("llm:") for f in d.validator_failures)

    def test_no_llm_uses_extractive_or_medoid(self):
        ctx = self._ctx()
        d = decide_label(ctx, None)
        assert d.final_source in ("extractive", "medoid")

    def test_all_tiers_fail_yields_abstain(self):
        # Empty context, no label hints
        ctx = ClusterContext(
            cluster_id="c1", trc="", existing_label=None, existing_source=None,
            member_count=5, representative_ticket_id=None,
        )
        d = decide_label(ctx, None)
        assert d.final_label  # non-empty fallback string
        assert d.final_source == "abstain"


# ──────────────────────────────────────────────────────────────────────
# JSON extraction
# ──────────────────────────────────────────────────────────────────────


class TestJSONExtraction:
    def test_plain_json(self):
        parsed = _extract_json_object('{"labels": []}')
        assert parsed == {"labels": []}

    def test_fenced_markdown(self):
        raw = "```json\n{\"labels\": [{\"cluster_id\": \"a\"}]}\n```"
        parsed = _extract_json_object(raw)
        assert parsed["labels"][0]["cluster_id"] == "a"

    def test_prose_with_json(self):
        raw = "Here is the output:\n{\"labels\": [{\"cluster_id\": \"b\"}]}\nThanks!"
        parsed = _extract_json_object(raw)
        assert parsed["labels"][0]["cluster_id"] == "b"

    def test_garbage(self):
        assert _extract_json_object("not json at all") is None
        assert _extract_json_object("") is None


# ──────────────────────────────────────────────────────────────────────
# Fake LLM client
# ──────────────────────────────────────────────────────────────────────


class _FakeLLMClient:
    def __init__(self, response: str):
        self._response = response
        self.calls = 0

    def generate(self, prompt: str, timeout: int = 180) -> str:
        self.calls += 1
        self._last_prompt = prompt
        return self._response


class TestLLMInvocation:
    def test_batched_call(self):
        ctxs = [
            ClusterContext(
                cluster_id=f"c{i}", trc="T", existing_label="Label",
                existing_source="medoid", member_count=5,
                representative_ticket_id=None,
                key_phrases=["foo bar"], snippets=["x"],
            )
            for i in range(3)
        ]
        resp = (
            '{"labels": ['
            '{"cluster_id": "c0", "canonical_label": "Foo bar baz issue", "confidence": 0.9},'
            '{"cluster_id": "c1", "canonical_label": "Another foo bar thing", "confidence": 0.8},'
            '{"cluster_id": "c2", "canonical_label": "ABSTAIN", "confidence": 0.1}'
            ']}'
        )
        client = _FakeLLMClient(resp)
        out = call_llm_for_labels(ctxs, client)
        assert client.calls == 1
        assert set(out) == {"c0", "c1", "c2"}
        assert out["c2"]["canonical_label"] == "ABSTAIN"

    def test_unparseable_returns_empty(self):
        client = _FakeLLMClient("no json here")
        out = call_llm_for_labels(
            [ClusterContext("c0", "T", "L", "medoid", 5, None)],
            client,
        )
        assert out == {}

    def test_no_client_returns_empty(self):
        out = call_llm_for_labels(
            [ClusterContext("c0", "T", "L", "medoid", 5, None)],
            None,
        )
        assert out == {}


# ──────────────────────────────────────────────────────────────────────
# End-to-end generate_labels (no LLM)
# ──────────────────────────────────────────────────────────────────────


class TestGenerateLabelsE2E:
    def test_fallback_chain_no_llm(self, fresh_db):
        conn = fresh_db
        _insert_cluster(
            conn, cluster_id="cl1",
            trc="Claim eligibility dispute unresolved",
            label=None, label_source=None, member_count=3, rep_id="t1",
        )
        _insert_ticket_index(
            conn, ticket_id="t1", trc="B",
            snippet="Patient eligibility on portal is denied",
            canonical_issue_id="cl1",
        )
        _insert_classification(
            conn, ticket_id="t1", trc="B",
            sub_cluster="Claim eligibility dispute",
            key_phrases=["eligibility dispute", "portal denied", "claim unresolved"],
            summary="Eligibility dispute summary",
        )
        conn.commit()

        result = generate_labels(conn, scan_id="scan-test", llm_client=None, force=True)
        assert result.total_clusters == 1
        d = result.decisions[0]
        assert d.final_label
        assert d.final_source in ("extractive", "medoid")

        # Persisted?
        row = conn.execute(
            "SELECT canonical_label, label_source, label_version FROM canonical_clusters WHERE cluster_id=?",
            ("cl1",),
        ).fetchone()
        assert row[0] == d.final_label
        assert row[1] == d.final_source
        assert row[2] == 2  # migration default 1 + our +1

    def test_force_false_skips_labeled(self, fresh_db):
        conn = fresh_db
        _insert_cluster(
            conn, cluster_id="cl2", trc="B",
            label="Existing good label here", label_source="llm", member_count=3,
        )
        conn.commit()
        r = generate_labels(conn, scan_id="s", llm_client=None, force=False)
        assert r.source_counts.get("existing_retained", 0) == 1


class TestFeedForwardSection:
    """Phase 5.5 — canonical menu feed-forward to classify prompt."""

    def test_empty_menu_is_placeholder(self):
        from src.agents.worker_agent import _format_canonical_menu
        s = _format_canonical_menu([], "BILLING")
        assert "first scan" in s.lower()

    def test_labels_rendered_as_bullets(self):
        from src.agents.worker_agent import _format_canonical_menu
        s = _format_canonical_menu(
            ["Claim eligibility dispute", "Portal access failure"],
            "BILLING",
        )
        assert '"Claim eligibility dispute"' in s
        assert '"Portal access failure"' in s
        assert "EXACT label" in s

    def test_menu_caps_at_40(self):
        from src.agents.worker_agent import _format_canonical_menu
        big = [f"Label number {i}" for i in range(100)]
        s = _format_canonical_menu(big, "T")
        # Bullets only for the first 40 — labels 41+ should not appear
        assert '"Label number 39"' in s
        assert '"Label number 40"' not in s

    def test_prompt_template_has_placeholder(self):
        from src.agents.worker_agent import CLASSIFY_PROMPT_PATH
        tpl = CLASSIFY_PROMPT_PATH.read_text(encoding="utf-8")
        assert "{canonical_menu_section}" in tpl

    def test_prompt_template_formats(self):
        from src.agents.worker_agent import CLASSIFY_PROMPT_PATH
        tpl = CLASSIFY_PROMPT_PATH.read_text(encoding="utf-8")
        out = tpl.format(
            trc_path="B", statistical_context="x", sub_taxonomy_section="y",
            canonical_menu_section="SENTINEL_MENU",
            n_tickets=1, n_comments=2, date_start="a", date_end="b",
            chunk_n=1, chunk_total=1, ticket_jsonl="{}",
        )
        assert "SENTINEL_MENU" in out


class TestLoadClusterContexts:
    def test_joins_classifications_and_tickets(self, fresh_db):
        conn = fresh_db
        _insert_cluster(
            conn, cluster_id="clX", trc="B",
            label=None, member_count=2, rep_id="t1",
        )
        _insert_ticket_index(
            conn, ticket_id="t1", trc="B",
            snippet="First snippet text.", canonical_issue_id="clX",
        )
        _insert_ticket_index(
            conn, ticket_id="t2", trc="B",
            snippet="Second snippet text.", canonical_issue_id="clX",
        )
        _insert_classification(
            conn, ticket_id="t1", trc="B", sub_cluster="X",
            key_phrases=["alpha beta", "gamma"],
        )
        _insert_classification(
            conn, ticket_id="t2", trc="B", sub_cluster="X",
            key_phrases=["alpha beta", "delta"],
        )
        conn.commit()

        ctxs = load_cluster_contexts(conn)
        assert len(ctxs) == 1
        ctx = ctxs[0]
        assert ctx.cluster_id == "clX"
        # Aggregated phrases: "alpha beta" (twice) should come first
        assert "alpha beta" in ctx.key_phrases
        assert len(ctx.snippets) >= 1
