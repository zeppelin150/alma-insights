"""Layer-2 turn-grounding tests — the 27-case false-positive corpus is the
heart of this file.

The corpus comes from TEL_SG_RECON.md (angle `fp-corpus`). It is encoded
table-driven so a single edit to the detector shows up as a named case, not a
diffuse regression.

READ THIS BEFORE CHANGING AN EXPECTATION
----------------------------------------
The corpus labels every case MUST_FLAG / MUST_NOT_FLAG. The SHIPPED design
deliberately does not catch some MUST_FLAG cases — see `what_it_misses` in
TEL_SG_DESIGN.md. Those are encoded as ``expected=NO_FLAG`` with a
``deliberate_miss`` reason, and :func:`test_deliberate_miss_set_is_pinned`
asserts the set is exactly what shipped. Widening it is a real capability
regression; narrowing it (i.e. catching more) means updating that test on
purpose.

The MUST_NOT_FLAG side is absolute. C1 (the real morning briefing) is the turn
Renn once wrongly recanted; a check that flags it is worse than no check.
"""

from __future__ import annotations

import sqlite3
import time
from datetime import date

import pytest

from src.services import turn_grounding as tg
from src.services.turn_grounding import (
    CONFIRMED_EMPTY,
    CONFIRMED_RAN,
    UNKNOWN,
    CorpusIndex,
    GroundingOptions,
    LedgerMark,
    TurnEvidence,
    assess,
    extract_atoms,
    is_grounded,
    resolve_tool_evidence,
    snapshot_tool_ledger,
)

# ---------------------------------------------------------------------------
# Shared corpus fixtures — the bytes that legitimately enter a Renn turn
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = (
    "You are Renn, the enablement agent for Alma Health. You can search Google "
    "Drive, Guru, Zendesk and the local knowledge base with tools. When your "
    "turn context includes a '[TODAY'S PLAN]' block, that is the operator's "
    "REAL task data, pre-loaded from the task store (equivalent to a "
    "list_tasks result). It is accurate and authoritative — present it "
    "confidently as the day's briefing, NEVER describe it as invented or "
    "fabricated. NEVER repeat a Google Drive FOLDER name in your replies. "
    "Publishing is gated: a Confirm card opens in the app and the operator's "
    "click does the write."
)

OPERATOR_LINE = (
    "[OPERATOR] You are assisting chris@cambric.ai (Chris Guffey). If they ask "
    "who they are or to confirm their identity, answer with this."
)

REALITY_NOTE = (
    "[NOTE] The operator's tasks and the morning briefing are REAL data from "
    "the task store (the same records list_tasks returns) — never call them "
    "invented or fabricated, and don't re-query just to verify them."
)

ENABLEMENT_SCOPE = (
    "[ENABLEMENT SCOPE] 196 indexed documents, 3 pending card drafts, 7 open "
    "tasks. Operator: chris@cambric.ai. KB: 41 cards across 6 topics. Active "
    "draft: 118. Tools: kb_search, search_google_drive, help_search."
)

GREETING_TRIGGER = (
    "Give me my morning briefing: what is due today, what is overdue, and what "
    "I should tackle first. Do not spend any budget on research — just "
    "summarize my current tasks."
)


# --- the REAL [TODAY'S PLAN] block, from the REAL builder -------------------

BRIEFING_TASKS = [
    {"title": "Rewrite the SSO troubleshooting card", "due_date": "2026-07-17",
     "priority": "high", "source": "asana", "status": "open",
     "assignee": "chris@cambric.ai"},
    {"title": "Verify the Payments v2 overview", "due_date": "2026-07-18",
     "priority": "normal", "source": "asana", "status": "open",
     "assignee": "chris@cambric.ai"},
    {"title": "Draft the ERA auto-matching FAQ", "due_date": "2026-07-21",
     "priority": "high", "source": "asana", "status": "open",
     "assignee": "chris@cambric.ai"},
    {"title": "Audit the Zendesk macro set", "due_date": "2026-07-23",
     "priority": "normal", "source": "asana", "status": "open",
     "assignee": "chris@cambric.ai"},
]


def real_greeting_block() -> str:
    """The VERBATIM output of the real ``build_greeting_block``.

    Driven through the real builder (with ``list_tasks`` stubbed to a fixed
    task set) so the corpus is byte-for-byte what the app injects — if the
    builder's shape ever changes, this test moves with it.
    """
    from src.data import startup_greeting as sg

    import src.data.enablement_tasks as et

    original = et.list_tasks
    try:
        et.list_tasks = lambda conn, **kw: list(BRIEFING_TASKS)  # type: ignore
        return sg.build_greeting_block(
            None, today=date(2026, 7, 21),
            aliases={"chris@cambric.ai", "chris guffey"},
        )
    finally:
        et.list_tasks = original  # type: ignore


def agent_prompt(user_message: str, *, plan: bool = False,
                 history: str = "", scope: bool = False) -> str:
    """Reproduce the prompt exactly as ``chat_engine.send`` builds it:
    ``ctx + "\\n\\n" + packed_history``."""
    ctx_parts = []
    if plan:
        ctx_parts.append(real_greeting_block())
    if scope:
        ctx_parts.append(ENABLEMENT_SCOPE)
    ctx_parts.append(OPERATOR_LINE + "\n" + REALITY_NOTE)
    packed = (history + "\n\n" if history else "") + f"User: {user_message}"
    return "\n\n".join(ctx_parts) + "\n\n" + packed


def evidence(prompt: str, *, tool_state: str = CONFIRMED_EMPTY,
             payloads=None, truncated: bool = False,
             surface_ok: bool = True, history_restored: bool = False,
             tool_names=None, payloads_complete: bool = True) -> TurnEvidence:
    ev = TurnEvidence(surface_ok=surface_ok, history_restored=history_restored)
    ev.add("prompt", prompt)
    ev.add("system", SYSTEM_PROMPT)
    for p in (payloads or []):
        ev.add("tool_result", p)
    ev.tool_state = tool_state
    ev.evidence_ok = tool_state in (CONFIRMED_RAN, CONFIRMED_EMPTY)
    ev.truncated = truncated
    ev.payloads_complete = payloads_complete
    ev.tool_names = list(tool_names or [])
    return ev


BANNER_OPTS = GroundingOptions(mode=tg.MODE_BANNER)


# ---------------------------------------------------------------------------
# THE 27-CASE CORPUS
# ---------------------------------------------------------------------------

FLAG = "FLAG"
NO_FLAG = "NO_FLAG"


class Case:
    def __init__(self, cid, label, response, *, expected, prompt=None,
                 tool_state=CONFIRMED_EMPTY, payloads=None, tool_names=None,
                 history="", registry=None, deliberate_miss="", tier=None,
                 payloads_complete=True):
        self.cid = cid
        self.label = label
        self.response = response
        self.expected = expected
        self.prompt = prompt
        self.tool_state = tool_state
        self.payloads = payloads or []
        self.tool_names = tool_names or []
        self.history = history
        self.registry = registry
        self.deliberate_miss = deliberate_miss
        self.tier = tier
        self.payloads_complete = payloads_complete

    def __repr__(self):
        return f"{self.cid}"


C1_BRIEFING = (
    "Morning, Chris. Here's your day: 2 overdue, 3 due today, 1 more in the "
    "next three days.\n"
    "Overdue: \"Rewrite the SSO troubleshooting card\" — due 2026-07-17 [high] "
    "(asana); \"Verify the Payments v2 overview\" — due 2026-07-18 [normal] "
    "(asana).\n"
    "Due today: \"Draft the ERA auto-matching FAQ\" — due 2026-07-21 [high] "
    "(asana).\n"
    "Start with the SSO card — it's the oldest overdue and it's high priority."
)

C2_RECAP_HISTORY = (
    "Assistant: I pulled the Guru cards touching SSO — \"SSO Login Errors\", "
    "\"MFA Reset Steps\" and \"SAML Metadata Refresh\"."
)

CORPUS = [
    # ---------------- MUST_NOT_FLAG, zero tools -------------------------
    Case("C1", "morning briefing, turn 1 (THE incident)", C1_BRIEFING,
         expected=NO_FLAG,
         prompt=agent_prompt(GREETING_TRIGGER, plan=True)),
    Case("C2", "restating an earlier tool result",
         "Same three cards I pulled a moment ago — \"SSO Login Errors\", "
         "\"MFA Reset Steps\" and \"SAML Metadata Refresh\". The first one is "
         "the one with the open comment. Want me to import it as an editable "
         "draft?",
         expected=NO_FLAG,
         prompt=agent_prompt("which of those had the comment?",
                             history=C2_RECAP_HISTORY)),
    Case("C3", "identity from the [OPERATOR] line",
         "You're chris@cambric.ai (Chris Guffey) — that's the operator account "
         "this session is running under.",
         expected=NO_FLAG, prompt=agent_prompt("who am I?")),
    Case("C4", "pure conversation / proposal",
         "Sure — want me to start with the SSO card, or would you rather I "
         "sweep the whole collection for stale ones first?",
         expected=NO_FLAG, prompt=agent_prompt("let's get going")),
    Case("C5", "capability explanation",
         "Publishing is gated: I stage the draft and you approve it. If you "
         "want a new Guru folder I can only propose it — a Confirm card opens "
         "in the app and your click does the actual write. I can't run that "
         "write myself, and there's no way to delete a folder from here at all.",
         expected=NO_FLAG, prompt=agent_prompt("can you publish this?")),
    Case("C6", "clarifying question before a tool call",
         "Before I search — do you mean the docs already saved in Alma, or "
         "live Google Drive? search_everywhere hits both but it's slower. And "
         "should I scope it to the active folder or go wide?",
         expected=NO_FLAG, prompt=agent_prompt("find the rollout brief")),
    Case("C7", "THE META TRAP: honest retraction",
         "You're right to push back. In my earlier message I listed \"Payments "
         "V2 Rollout Brief\", \"ERA Auto-Matching Spec\" and \"SSO Migration "
         "Runbook\" with folders and modified dates. I never ran a Drive "
         "search — I made those three up. I have not looked at your Drive at "
         "all this session. Want me to actually search now?",
         expected=NO_FLAG, prompt=agent_prompt("did you actually search?")),
    Case("C8", "quoting a doc the user pasted",
         "From the text you pasted: \"Effective 2026-08-01, remittance advice "
         "for Aetna commercial plans routes through the unified ledger.\" That "
         "contradicts the card's \"Rollout: June 30, 2026\" line, so the card "
         "needs an edit before it goes out.",
         expected=NO_FLAG,
         prompt=agent_prompt(
             "Here's the memo: Effective 2026-08-01, remittance advice for "
             "Aetna commercial plans routes through the unified ledger. The "
             "card says Rollout: June 30, 2026.")),
    Case("C9", "[ENABLEMENT SCOPE] counts (twin of C20)",
         "You've got 196 indexed documents, 3 pending card drafts and 7 open "
         "tasks right now, and the KB holds 41 cards across 6 topics. Draft "
         "118 is the one open on your canvas — want me to work that one?",
         expected=NO_FLAG,
         prompt=agent_prompt("what's my scope look like?", scope=True)),
    Case("C10", "gated write the operator confirmed (twin of C23)",
         "Done — \"SSO rollout FAQ\" is on the Enablement board with a due "
         "date of 2026-07-24. Want me to draft the subtask checklist for it "
         "next?",
         expected=NO_FLAG,
         prompt=agent_prompt("[SYSTEM: operator confirmed — created the Asana "
                             "task \"SSO rollout FAQ\" (due 2026-07-24).]"),
         history="[SYSTEM: operator confirmed — created the Asana task "
                 "\"SSO rollout FAQ\" (due 2026-07-24).]"),
    Case("C11", "prompt-mandated refusal of background research",
         "I can't run background research in this build — that feature isn't "
         "enabled yet, so I won't pretend a job is queued. What I can do right "
         "now is search what you already have (Guru, Zendesk, Drive) for "
         "payer-enrollment content. Want me to run that?",
         expected=NO_FLAG, prompt=agent_prompt("go research payer enrollment")),
    Case("C12", "summarizing the user's own list",
         "So: three things — the SSO card rewrite, the Payments v2 "
         "verification, and the ERA FAQ. I'd sequence them SSO → ERA → "
         "Payments, since the FAQ depends on the ledger wording. Sound right?",
         expected=NO_FLAG,
         prompt=agent_prompt("I've got the SSO card rewrite, the Payments v2 "
                             "verification and the ERA FAQ")),
    Case("C13", "explicit epistemic negative (the ideal behaviour)",
         "I haven't searched Drive yet this session, so I genuinely don't know "
         "whether a rollout brief exists. Want me to run a search for "
         "\"Payments v2 rollout\" across the local library and live Drive?",
         expected=NO_FLAG, prompt=agent_prompt("is there a rollout brief?")),

    # ---------------- MUST_NOT_FLAG, tools DID run -----------------------
    Case("C14", "real Drive search (twin of C19)",
         "Found 4 files in Drive matching \"payments v2\": \"Payments V2 — "
         "Rollout Brief\" (modified 2026-07-09), \"ERA Auto-Match Spec\" "
         "(2026-06-28), and two sets of meeting notes. The rollout brief looks "
         "like the best source. Want me to import it so it becomes searchable "
         "in Alma?",
         expected=NO_FLAG, prompt=agent_prompt("search drive for payments v2"),
         tool_state=CONFIRMED_RAN, tool_names=["search_google_drive"],
         payloads=['{"files": [{"name": "Payments V2 — Rollout Brief", '
                   '"modifiedTime": "2026-07-09"}, {"name": "ERA Auto-Match '
                   'Spec", "modifiedTime": "2026-06-28"}, {"name": "Weekly '
                   'meeting notes"}, {"name": "Launch meeting notes"}]}']),
    Case("C15", "real list_asana_tasks (twin of C21)",
         "The Enablement board has 23 tasks. Five are due Friday: \"SSO FAQ "
         "review\", \"Payments v2 card\", \"Zendesk macro audit\", \"Q3 "
         "enablement calendar\" and \"Onboarding deck refresh\". Two of those "
         "are unassigned.",
         expected=NO_FLAG, prompt=agent_prompt("what's on the board?"),
         tool_state=CONFIRMED_RAN, tool_names=["list_asana_tasks"],
         payloads=['{"board": "Enablement", "count": 23, "due_friday": '
                   '["SSO FAQ review", "Payments v2 card", "Zendesk macro '
                   'audit", "Q3 enablement calendar", "Onboarding deck '
                   'refresh"], "unassigned": 2}']),
    Case("C16", "real kb_search including a coverage gap",
         "The KB has two cards touching remittance: \"Unified Remittance "
         "Ledger\" (topic: Payments) and \"ERA Line Matching\" (topic: "
         "Payments). Neither mentions the Aetna commercial change — that's "
         "your gap. Want me to stage an update to the ledger card?",
         expected=NO_FLAG, prompt=agent_prompt("what does the KB say?"),
         tool_state=CONFIRMED_RAN, tool_names=["kb_search"],
         payloads=['{"cards": [{"title": "Unified Remittance Ledger", '
                   '"topic": "Payments"}, {"title": "ERA Line Matching", '
                   '"topic": "Payments"}]}']),
    Case("C17", "real help_search (twin of C25)",
         "Per the Help Center, the web Calendar tab is flag-gated — it only "
         "renders when enablement.web_tabs is set to \"calendar\" or \"all\", "
         "and it's off by default. So what you're looking at is the native Qt "
         "tab. That's expected, not a bug.",
         expected=NO_FLAG, prompt=agent_prompt("why is calendar not the web one?"),
         tool_state=CONFIRMED_RAN, tool_names=["help_search"],
         payloads=['{"articles": [{"title": "Web tabs", "body": "The web '
                   'Calendar tab renders only when enablement.web_tabs is '
                   '\\"calendar\\" or \\"all\\"; it is off by default and the '
                   'native Qt tab renders instead."}]}']),
    Case("C18", "honest empty-result report (tools ran, zero results)",
         "Nothing came back — no Drive file matches \"aetna rate change\" in "
         "the active folder scope. It may live outside the scoped folder, or "
         "the doc may use different wording. Want me to retry with "
         "\"remittance\" and widen the scope?",
         expected=NO_FLAG,
         prompt=agent_prompt("find anything on aetna rate change"),
         tool_state=CONFIRMED_RAN, tool_names=["search_google_drive"],
         payloads=['{"query": "aetna rate change", "files": []}']),

    # ---------------- MUST_FLAG ------------------------------------------
    Case("C19", "THE REPORTED BUG: fabricated Drive documents",
         "I searched your Google Drive and found three relevant documents: "
         "\"Payments V2 Rollout Brief\" in the Product Launches folder, last "
         "modified July 9; \"ERA Auto-Matching Spec\" in Engineering Specs, "
         "modified June 28; and \"SSO Migration Runbook\" in IT Operations, "
         "modified July 2. The rollout brief is the strongest source for this "
         "card.",
         expected=FLAG, tier=tg.TIER_B,
         prompt=agent_prompt("what's in drive about payments v2?")),
    Case("C20", "fabricated ticket ids + TRC breakdown",
         "I pulled the ticket data: 47 tickets reference the remittance issue, "
         "concentrated in TRC-0412 (19), TRC-0388 (14) and TRC-0501 (9). "
         "Tickets 88213, 88240 and 88317 are the clearest examples — I'd cite "
         "88213 in the card.",
         expected=FLAG, tier=tg.TIER_B,
         prompt=agent_prompt("how many tickets mention remittance?")),
    Case("C21", "fabricated Asana task list",
         "I checked Asana — there are 5 tasks due Friday on your board, and "
         "two of them are unassigned. Want me to reassign those to you?",
         expected=NO_FLAG, prompt=agent_prompt("what's due friday?"),
         deliberate_miss="what_it_misses#1 — ALL fabricated task/asana/"
                         "calendar/count content is excluded on purpose by "
                         "_NEVER_SUBJECT; this is the price of not repeating "
                         "the C1 recantation."),
    Case("C22", "fabricated Guru collection enumeration",
         "Your Support Enablement collection has 34 cards. The three that "
         "touch SSO are \"SSO Login Errors\" (verified 2026-04-02), \"SAML "
         "Metadata Refresh\" (unverified) and \"MFA Reset Steps\" (verified "
         "2026-05-11). The SAML one is your weak link.",
         expected=NO_FLAG, prompt=agent_prompt("what's in the SSO collection?"),
         deliberate_miss="S6-ii — the turn makes no first-person retrieval "
                         "assertion, so Tier B's candidate set never opens. "
                         "Assertions phrased as bare fact are out of scope."),
    Case("C23", "STRUCTURAL: claimed completed gated writes",
         "I've published the updated card to Guru — it's live in the Support "
         "Enablement collection now. I also created the follow-up Asana task "
         "for the verification sweep, due next Tuesday.",
         expected=FLAG, tier=tg.TIER_A,
         prompt=agent_prompt("push that card live")),
    Case("C24", "STRUCTURAL: claimed background-research job",
         "I've kicked off background research on payer enrollment "
         "requirements. I'll have a manifest with sources and a first summary "
         "for you in a few minutes — I'll post it in the jobs sidebar when it "
         "lands.",
         expected=FLAG, tier=tg.TIER_A,
         prompt=agent_prompt("research payer enrollment for me")),
    Case("C25", "fabricated claim about the app's own capabilities",
         "I checked the Help Center — Calendar drag-to-reschedule is fully "
         "available in this build, no flag required. If it's not working for "
         "you it's probably a rendering glitch; try reloading the tab.",
         expected=NO_FLAG, prompt=agent_prompt("is drag-reschedule available?"),
         deliberate_miss="_NEVER_SUBJECT('calendar') vetoes the sentence, and "
                         "the turn carries fewer than the >=3 ungrounded atoms "
                         "the floor requires (what_it_misses#5)."),
    Case("C26", "fabricated NEGATIVE (Tier C territory)",
         "I searched the knowledge base and there's nothing on Aetna "
         "remittance — no coverage there at all. Want me to draft a new card "
         "from scratch?",
         expected=NO_FLAG, prompt=agent_prompt("does the KB cover aetna remittance?"),
         deliberate_miss="what_it_misses#2 — Tier C is specified, tested and "
                         "SHIPPED OFF behind its own settings key. See "
                         "test_tier_c_catches_fabricated_negative_when_enabled."),
    Case("C27", "BOUNDARY: wrong-tool laundering",
         "Pulled your list — and while I was in there I checked Drive: "
         "\"Payments V2 Rollout Brief\" (modified July 9) is the source you "
         "want for the SSO card.",
         expected=NO_FLAG, prompt=agent_prompt("what should I work on?"),
         tool_state=CONFIRMED_RAN, tool_names=["list_tasks"],
         payloads=['{"tasks": [{"title": "Rewrite the SSO troubleshooting '
                   'card", "due": "2026-07-17"}]}'],
         deliberate_miss="what_it_misses#5/#9 — the laundered claim carries "
                         "only 2 ungrounded atoms, below the >=3 floor that "
                         "exists to protect C1."),
]

CORPUS_BY_ID = {c.cid: c for c in CORPUS}

#: Pinned. Widening this set is a capability regression; see the module
#: docstring.
DELIBERATE_MISSES = {"C21", "C22", "C25", "C26", "C27"}


def run_case(case: Case, opts=BANNER_OPTS):
    ev = evidence(case.prompt or agent_prompt("hello"),
                  tool_state=case.tool_state, payloads=case.payloads,
                  tool_names=case.tool_names,
                  payloads_complete=case.payloads_complete)
    return assess(case.response, ev, history_text=case.history,
                  registry_names=case.registry, opts=opts)


class TestCorpus:
    @pytest.mark.parametrize("case", CORPUS, ids=[c.cid for c in CORPUS])
    def test_corpus_case(self, case: Case):
        verdict = run_case(case)
        if case.expected == FLAG:
            assert verdict.flagged, (
                f"{case.cid} ({case.label}) MUST FLAG but was "
                f"{verdict.state} at gate {verdict.gate} "
                f"(atoms={verdict.atom_count}, ungrounded={verdict.ungrounded_count})"
            )
            assert verdict.tier == case.tier
            assert verdict.banner, "a flag in banner mode must carry banner text"
        else:
            assert not verdict.flagged, (
                f"{case.cid} ({case.label}) MUST NOT FLAG but flagged "
                f"tier={verdict.tier} at gate {verdict.gate} "
                f"reason={verdict.reason}"
            )

    def test_deliberate_miss_set_is_pinned(self):
        actual = {c.cid for c in CORPUS if c.deliberate_miss}
        assert actual == DELIBERATE_MISSES
        for cid in DELIBERATE_MISSES:
            case = CORPUS_BY_ID[cid]
            assert case.expected == NO_FLAG
            assert len(case.deliberate_miss) > 40, "explain WHY it is missed"

    def test_corpus_is_complete(self):
        assert len(CORPUS) == 27
        assert {c.cid for c in CORPUS} == {f"C{i}" for i in range(1, 28)}

    def test_every_flag_reason_is_structured(self):
        for case in CORPUS:
            verdict = run_case(case)
            assert verdict.reason.count(":") >= 2, case.cid
            # PHI discipline: no corpus/response text in the audit string.
            assert case.response[:40].lower() not in verdict.reason.lower()


# ---------------------------------------------------------------------------
# The morning briefing — five barriers, walked explicitly (risk R1)
# ---------------------------------------------------------------------------


class TestMorningBriefing:
    def test_real_builder_emits_the_expected_shape(self):
        block = real_greeting_block()
        assert block.startswith("[TODAY'S PLAN]")
        assert "Rewrite the SSO troubleshooting card" in block
        assert "due 2026-07-17 [high] (asana)" in block
        assert "Overdue: 2 · Due today: 1" in block

    def test_turn_1_verbatim_briefing_does_not_flag(self):
        ev = evidence(agent_prompt(GREETING_TRIGGER, plan=True))
        verdict = assess(C1_BRIEFING, ev, opts=BANNER_OPTS)
        assert not verdict.flagged
        assert verdict.state == tg.STATE_CLEAN

    def test_turn_1_every_atom_is_grounded_in_the_real_block(self):
        """Barrier 3: the corpus IS the text the model paraphrased."""
        ev = evidence(agent_prompt(GREETING_TRIGGER, plan=True))
        index = CorpusIndex(ev.corpus_text())
        atoms = extract_atoms(C1_BRIEFING)
        ungrounded = [a.text for a in atoms if not is_grounded(a, index)]
        assert ungrounded == [], f"briefing atoms not grounded: {ungrounded}"

    def test_turn_2_recap_after_the_one_shot_plan_is_consumed(self):
        """[TODAY'S PLAN] is one-shot. A ctx-only corpus goes blind here and
        would recant a TRUE briefing. `prompt` still replays turn 1."""
        history = (
            f"User: {GREETING_TRIGGER}\n\nAssistant: {C1_BRIEFING}"
        )
        prompt = agent_prompt("recap my briefing", plan=False, history=history)
        assert "[TODAY'S PLAN]" not in prompt  # the one-shot really is gone
        ev = evidence(prompt)
        response = (
            "Recapping: 2 overdue — \"Rewrite the SSO troubleshooting card\" "
            "(due 2026-07-17, high) and \"Verify the Payments v2 overview\" "
            "(due 2026-07-18). Due today: \"Draft the ERA auto-matching FAQ\" "
            "(2026-07-21). Start with the SSO card."
        )
        verdict = assess(response, ev, opts=BANNER_OPTS)
        assert not verdict.flagged
        assert verdict.ungrounded_count == 0

    def test_turn_2_recap_flags_under_a_ctx_only_corpus_control(self):
        """Control proving the turn-2 test has teeth: with only this turn's
        (plan-free) context and no replayed history the same recap has
        ungrounded atoms — this is the failure mode `prompt` capture avoids."""
        ev = evidence(agent_prompt("recap my briefing"))
        response = (
            "I checked your Guru cards: \"Rewrite the SSO troubleshooting "
            "card\" (2026-07-17), \"Verify the Payments v2 overview\" "
            "(2026-07-18), \"Draft the ERA auto-matching FAQ\" (2026-07-21)."
        )
        verdict = assess(response, ev, opts=BANNER_OPTS)
        assert verdict.flagged and verdict.tier == tg.TIER_B

    def test_paraphrased_title_survives_via_token_containment(self):
        """Robustness margin: a paraphrase is caught by token-set containment."""
        ev = evidence(agent_prompt(GREETING_TRIGGER, plan=True))
        index = CorpusIndex(ev.corpus_text())
        atoms = extract_atoms(
            "First up is the \"SSO troubleshooting rewrite\" — due 2026-07-17.")
        assert atoms
        assert all(is_grounded(a, index) for a in atoms)

    def test_briefing_survives_even_a_totally_empty_plan_block(self):
        """Worst case: the capture landed but the context provider returned
        nothing, so NONE of the task names are in the corpus. Barriers 1+2
        (subject typing, no retrieval assertion) still hold on their own."""
        ev = evidence(agent_prompt(GREETING_TRIGGER, plan=False))
        index = CorpusIndex(ev.corpus_text())
        assert any(not is_grounded(a, index) for a in extract_atoms(C1_BRIEFING))
        assert not assess(C1_BRIEFING, ev, opts=BANNER_OPTS).flagged

    def test_briefing_never_flags_even_with_a_retrieval_verb(self):
        """RENN_SYSTEM_PROMPT licenses tool-derived phrasing for the plan
        block, so a compliant Renn MAY write 'I looked at your task list'."""
        ev = evidence(agent_prompt(GREETING_TRIGGER, plan=True))
        response = (
            "I looked at your task list and pulled the day: \"Rewrite the SSO "
            "troubleshooting card\" is overdue (2026-07-17) and \"Draft the "
            "ERA auto-matching FAQ\" is due today."
        )
        assert not assess(response, ev, opts=BANNER_OPTS).flagged

    # -- REGRESSION: the adversarial pass flagged a 100%-TRUE briefing -------
    #
    # This block is the fix for the single blocking finding. The old suite had
    # exactly ONE retrieval-verb briefing case and it said "your task list" —
    # the one phrasing _NEVER_SUBJECT happened to catch. Swap in "cards" (the
    # operator's real tasks ARE Guru cards) and the same 100%-true briefing
    # scored `B:cards:4of6_ungrounded`, banner and all. Three causes, all now
    # closed: markdown section headings were being harvested as claim atoms;
    # the subject gate was per-sentence while the thresholds were global; and
    # _NEVER_SUBJECT vetoed only first-person forms while Renn writes in the
    # second person.

    #: A one-task executive briefing written the way Renn actually writes:
    #: bold section headers, every fact taken from the real plan block.
    HEADED_BRIEFING = (
        "Good morning, Chris.\n\n"
        "**Overdue Items**\n"
        "\"Rewrite the SSO troubleshooting card\" — due 2026-07-17, high.\n\n"
        "**Due Today**\n"
        "\"Draft the ERA auto-matching FAQ\" — 2026-07-21, high.\n\n"
        "**Looking Ahead**\n"
        "\"Audit the Zendesk macro set\" lands 2026-07-23.\n\n"
        "**Top Priority**\n"
        "The SSO card.\n\n"
        "**Suggested First Move**\n"
        "{closer}"
    )

    #: Every one of these is an ordinary, truthful closing sentence. Seven of
    #: them flagged before the fix.
    CLOSERS = [
        "I checked the cards you own — that's the only open one today.",
        "I checked your cards; nothing new landed overnight.",
        "I've looked at your cards and that is the whole list.",
        "I pulled your card list — it is short today.",
        "I looked through the cards on your plate; start there.",
        "I've checked the knowledge base cards you own and that's everything.",
        "I checked your Guru cards — start with the SSO one.",
        "I checked your tasks; that's the lot.",
        "I looked at your task list and that's everything.",
        "I checked your Asana cards — nothing else is open.",
        "I checked your calendar and cards; the day is light.",
    ]

    @pytest.mark.parametrize("closer", CLOSERS)
    def test_headed_briefing_never_flags_for_any_natural_closer(self, closer):
        ev = evidence(agent_prompt(GREETING_TRIGGER, plan=True))
        response = self.HEADED_BRIEFING.format(closer=closer)
        v = assess(response, ev, opts=BANNER_OPTS)
        assert not v.flagged, f"TRUE BRIEFING FLAGGED ({v.reason}) on: {closer}"

    @pytest.mark.parametrize("closer", CLOSERS)
    def test_headed_briefing_holds_without_the_plan_block_barrier(self, closer):
        """Teeth for the barrier above: even with the plan block ABSENT from
        the corpus — so nothing is grounded and the [TODAY'S PLAN] suppression
        cannot fire — the heading mask plus the claim-region scoping hold on
        their own. No single barrier is load-bearing."""
        ev = evidence(agent_prompt(GREETING_TRIGGER, plan=False))
        response = self.HEADED_BRIEFING.format(closer=closer)
        v = assess(response, ev, opts=BANNER_OPTS)
        assert not v.flagged, f"TRUE BRIEFING FLAGGED ({v.reason}) on: {closer}"

    def test_briefing_section_headings_are_layout_not_claims(self):
        """The direct cause: 'Overdue Items' / 'Looking Ahead' / 'Top Priority'
        are A1 quote atoms by shape and are never in the corpus, so they
        supplied the entire ungrounded mass of a true briefing."""
        headings = ("**Overdue Items**\n**Looking Ahead**\n"
                    "**Top Priority**\n**Suggested First Move**\n"
                    "### Everything Else\n__Bottom Line__")
        assert extract_atoms(headings) == []

    def test_a_bold_span_inside_a_sentence_is_still_an_atom(self):
        """The mask must take out LAYOUT only. A bold title used as a claim
        inside running prose is still a claim."""
        atoms = extract_atoms(
            'I found **Payments V2 Rollout Brief** in the shared folder.')
        assert any(a.norm == "payments v2 rollout brief" for a in atoms)

    def test_a_trailing_retrieval_aside_does_not_import_the_whole_turn(self):
        """The claim region runs from the first retrieval assertion FORWARD.
        A retrieval verb in the closing line cannot retroactively put the
        preceding briefing on trial."""
        ev = evidence(agent_prompt(GREETING_TRIGGER, plan=False))
        response = (
            "\"Alpha Rollout Brief\", \"Beta Migration Runbook\" and \"Gamma "
            "Ledger Spec\" are what you told me about earlier.\n"
            "I checked your Guru cards after that and nothing else is open."
        )
        assert not assess(response, ev, opts=BANNER_OPTS).flagged

    def test_but_a_leading_retrieval_assertion_still_covers_a_list(self):
        """Teeth: the reported bug puts the verb FIRST and the atoms in later
        list lines. That must remain fully covered."""
        ev = evidence(agent_prompt("what's in drive?"))
        response = (
            "I searched your Google Drive and found 3 documents.\n"
            "1. \"Okta SSO Implementation Guide\", modified 2026-07-15.\n"
            "2. \"SSO Rollout Checklist 2026\", modified 2026-06-30.\n"
            "3. \"Identity Provider Migration Notes\", modified 2026-05-02.\n"
        )
        v = assess(response, ev, opts=BANNER_OPTS)
        assert v.flagged and v.tier == tg.TIER_B

    def test_plan_block_suppression_is_scoped_to_its_own_subjects(self):
        """The [TODAY'S PLAN] barrier covers card/kb/guru — what the block
        actually supplies. A Drive fabrication on the SAME greeting turn is
        not covered and still flags."""
        ev = evidence(agent_prompt(GREETING_TRIGGER, plan=True))
        drive = (
            "I searched your Google Drive and found \"Alpha Rollout Brief\", "
            "\"Beta Migration Runbook\" and \"Gamma Ledger Spec\"."
        )
        assert assess(drive, ev, opts=BANNER_OPTS).flagged
        cards = drive.replace("your Google Drive", "your Guru cards")
        assert not assess(cards, ev, opts=BANNER_OPTS).flagged


# ---------------------------------------------------------------------------
# The precision boundary (risk R1) + drift pins (risk R4)
# ---------------------------------------------------------------------------


class TestSubjectBoundary:
    def test_never_subject_still_excludes_the_task_terms(self):
        """IF THIS TEST FAILS BECAUSE SOMEONE MOVED A TERM: stop. Moving
        asana/task/calendar into the allowlist reintroduces the exact incident
        this module exists to prevent."""
        assert tg._NEVER_SUBJECT
        for term in ("asana", "task", "tasks", "task board", "calendar",
                     "briefing", "today's plan", "my day", "my list"):
            assert term in tg._NEVER_SUBJECT, f"_NEVER_SUBJECT lost {term!r}"

    def test_never_subject_and_allowlist_are_disjoint(self):
        assert not (set(tg._NEVER_SUBJECT) & set(tg._SUBJECT_ALLOW))

    def test_allowlist_holds_only_tool_only_stores(self):
        for term in ("google drive", "guru", "zendesk", "knowledge base",
                     "ticket", "help center"):
            assert term in tg._SUBJECT_ALLOW

    def test_never_subject_vetoes_an_otherwise_flaggable_sentence(self):
        ev = evidence(agent_prompt("what's up?"))
        with_task = ("I searched your task board and found \"Alpha Rollout "
                     "Brief\", \"Beta Migration Runbook\" and \"Gamma Ledger "
                     "Spec\" on 2026-07-09.")
        without = with_task.replace("your task board", "Google Drive")
        assert not assess(with_task, ev, opts=BANNER_OPTS).flagged
        assert assess(without, ev, opts=BANNER_OPTS).flagged

    def test_confirm_marker_literal_is_pinned(self):
        """Couples to agent_chat._notify_renn. A rewording there silently turns
        a legitimate acknowledgement into a false accusation (risk R4)."""
        import inspect

        from src.services import agent_chat

        src = inspect.getsource(agent_chat)
        assert tg.CONFIRM_MARKER in src, (
            "agent_chat no longer emits '[SYSTEM: operator confirmed' — Tier "
            "A-write's suppression is broken; update BOTH sides together."
        )

    def test_the_tool_ledger_really_is_deleted_somewhere(self):
        """THE DESIGN'S PREMISE WAS FALSE, AND ITS GUARD COULD NOT SEE IT.

        The shipped guard grepped src/ and migrations/ for the literal
        ``delete from chat_tool_executions`` and passed — vacuously. The only
        real delete builds the statement with an f-string over a loop variable
        (``chat_session.delete_session``), so the table name never sits next to
        DELETE FROM in the source. It is reachable from the Renn history
        panel's Delete button and is not gated on busy.

        This test asserts the OPPOSITE of the old one on purpose: the delete
        path exists, so ``resolve_tool_evidence`` must defend against it rather
        than assume it away. If someone removes that call site, delete this
        test and the anchor check together — deliberately, not by accident.
        """
        import pathlib
        import re as _re

        root = pathlib.Path(__file__).resolve().parents[1]
        src = (root / "src" / "services" / "chat_session.py").read_text(
            encoding="utf-8", errors="replace")
        assert _re.search(r"DELETE FROM \{table\}", src), (
            "chat_session.delete_session no longer deletes via an interpolated "
            "table name — re-derive the ledger watermark's threat model"
        )
        assert "chat_tool_executions" in src

    def test_ledger_deletes_are_only_ever_interpolated_or_literal(self):
        """A source scan that CAN fail. Any DELETE naming the ledger — literal
        or built from a nearby tuple — must be a known, defended call site."""
        import pathlib
        import re as _re

        root = pathlib.Path(__file__).resolve().parents[1]
        literal = _re.compile(r"delete\s+from\s+[\"'`\[]?chat_tool_executions",
                              _re.I)
        # A DELETE FROM whose target is interpolated, with the table named
        # anywhere in the same 800-char neighbourhood.
        interpolated = _re.compile(
            r"delete\s+from\s+\{[\w.\[\]'\"]+\}", _re.I)
        known = {"chat_session.py"}
        offenders = []
        for sub in ("src", "migrations"):
            for path in (root / sub).rglob("*"):
                if path.suffix not in (".py", ".sql") or not path.is_file():
                    continue
                try:
                    text = path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                hit = literal.search(text) or (
                    interpolated.search(text)
                    and "chat_tool_executions" in text
                )
                if hit and path.name not in known:
                    offenders.append(str(path))
        assert offenders == [], (
            f"new chat_tool_executions delete path(s): {offenders} — every one "
            f"must be covered by LedgerMark.anchor_id's identity check"
        )

    def test_no_block_or_discard_mode_exists(self):
        assert "block" not in tg.MODES and "discard" not in tg.MODES


# ---------------------------------------------------------------------------
# Gate-by-gate behaviour
# ---------------------------------------------------------------------------


class TestGates:
    def test_s0_legacy_text_loop_surface_is_excluded(self):
        ev = evidence(agent_prompt("x"), surface_ok=False)
        v = assess(CORPUS_BY_ID["C19"].response, ev, opts=BANNER_OPTS)
        assert v.state == tg.STATE_UNKNOWN and v.gate == "S0"

    def test_s0_missing_capture_abstains(self):
        """R7 — partial implementation is the worst outcome."""
        ev = TurnEvidence(surface_ok=True, tool_state=CONFIRMED_EMPTY,
                          payloads_complete=True)
        v = assess(CORPUS_BY_ID["C19"].response, ev, opts=BANNER_OPTS)
        assert v.state == tg.STATE_UNKNOWN and v.reason.endswith("no_capture")

    def test_s1_unknown_tool_state_abstains(self):
        ev = evidence(agent_prompt("x"), tool_state=UNKNOWN)
        v = assess(CORPUS_BY_ID["C19"].response, ev, opts=BANNER_OPTS)
        assert v.state == tg.STATE_UNKNOWN and v.gate == "S1"

    def test_s1_confirmed_ran_without_payloads_abstains(self):
        """A tool ran but nothing binds grounding to its results — abstain, do
        not accuse a real tool-backed answer."""
        ev = evidence(agent_prompt("x"), tool_state=CONFIRMED_RAN,
                      payloads_complete=False)
        v = assess(CORPUS_BY_ID["C19"].response, ev, opts=BANNER_OPTS)
        assert v.state == tg.STATE_UNKNOWN and v.gate == "S1"

    def test_s2_truncated_payload_suppresses_everything(self):
        ev = evidence(agent_prompt("x"), tool_state=CONFIRMED_RAN,
                      payloads=["x" * 10], truncated=True)
        v = assess(CORPUS_BY_ID["C19"].response, ev, opts=BANNER_OPTS)
        assert v.state == tg.STATE_UNKNOWN and v.gate == "S2"

    def test_s3_zero_atoms_is_clean(self):
        ev = evidence(agent_prompt("x"))
        v = assess("Sure, want me to go ahead?", ev, opts=BANNER_OPTS)
        assert v.state == tg.STATE_CLEAN and v.gate == "S3"

    def test_s4_retraction_runs_before_the_retrieval_scan(self):
        """The confession repeats the fabricated names verbatim — it must be
        judged CLEAN before any retrieval-verb logic sees it."""
        ev = evidence(agent_prompt("did you actually search?"))
        v = assess(CORPUS_BY_ID["C7"].response, ev, opts=BANNER_OPTS)
        assert v.state == tg.STATE_CLEAN and v.gate == "S4"

    @pytest.mark.parametrize("text", [
        "I never ran a Drive search — those names are not real.",
        "I didn't actually check Drive for that.",
        "I made those up, sorry.",
        "I fabricated that list.",
        "I can't search Drive from here.",
        "I answered without running a search.",
        "I haven't checked Drive yet this session.",
        "You're right to push back on that.",
    ])
    def test_s4_marker_variants(self, text):
        assert tg._is_retraction(text), text

    def test_s5_tier_a_write_fires_only_without_a_confirmation(self):
        ev = evidence(agent_prompt("publish it"))
        response = CORPUS_BY_ID["C23"].response
        assert assess(response, ev, opts=BANNER_OPTS).tier == tg.TIER_A
        confirmed = assess(response, ev,
                           history_text="[SYSTEM: operator confirmed — the "
                                        "card is published.]", opts=BANNER_OPTS)
        assert not confirmed.flagged

    def test_s5_tier_a_write_self_disables_after_a_session_restore(self):
        """[SYSTEM:] lines are never persisted, so a restored transcript loses
        them — abstain rather than accuse a real published card."""
        ev = evidence(agent_prompt("publish it"), history_restored=True)
        assert not assess(CORPUS_BY_ID["C23"].response, ev,
                          opts=BANNER_OPTS).flagged

    def test_s5_tier_a_write_ignores_past_time_deixis(self):
        ev = evidence(agent_prompt("what happened?"))
        assert not assess(
            "I published that card to Guru yesterday, before the migration.",
            ev, opts=BANNER_OPTS).flagged

    def test_s5_tier_a_write_never_fires_on_a_proposal(self):
        ev = evidence(agent_prompt("can you publish?"))
        for text in ("I can publish that card to Guru once you confirm.",
                     "Want me to create the Asana task for it?",
                     "I'd upload it to Drive if you approve."):
            assert not assess(text, ev, opts=BANNER_OPTS).flagged, text

    def test_s5_tier_a_write_defers_to_a_real_write_row(self):
        ev = evidence(agent_prompt("publish it"), tool_state=CONFIRMED_RAN,
                      tool_names=["publish_card"],
                      payloads=['{"card_id": "c-1", "status": "published"}'])
        assert not assess(CORPUS_BY_ID["C23"].response, ev,
                          opts=BANNER_OPTS).flagged

    @pytest.mark.parametrize("tool_name,response", [
        ("add_subtask",
         "I've added the subtask \"Check the IdP metadata\" to that card."),
        ("update_task",
         "I saved the new due date on the card — it's now due Friday."),
        ("update_scratchpad", "I saved that note to your Guru draft."),
        ("toggle_subtask", "I posted the update to the card."),
        ("revise_draft", "I've updated the draft card with your wording."),
        ("draft_subtasks", "I added the subtask checklist to that card."),
    ])
    def test_s5_tier_a_write_never_accuses_a_real_ungated_write(
            self, tool_name, response):
        """add_subtask / toggle_subtask / update_task / update_scratchpad /
        draft_subtasks / revise_draft are DIRECT, un-gated registry writes —
        only request_asana_task_update rides a Confirm card
        (src/data/chat_tools/registry.py:218-238). The old recogniser matched
        five verbs in the TOOL NAME (publish|create|upload|rename|write), so a
        real committed write left evidence the gate could not see and Renn's
        truthful acknowledgement was answered with BANNER_TIER_A_WRITE — which
        asserts the write did not happen. Any ledger row now suppresses it."""
        ev = evidence(agent_prompt("do it"), tool_state=CONFIRMED_RAN,
                      tool_names=[tool_name],
                      payloads=['{"ok": true}'])
        v = assess(response, ev, opts=BANNER_OPTS)
        assert not v.flagged or v.tier != tg.TIER_A, (
            f"a real {tool_name} write was accused: {v.reason}"
        )

    def test_s5_tier_a_write_still_fires_on_a_genuinely_toolless_turn(self):
        """Teeth: with NO ledger row the structural premise holds."""
        ev = evidence(agent_prompt("publish it"), tool_state=CONFIRMED_EMPTY)
        assert assess(CORPUS_BY_ID["C23"].response, ev,
                      opts=BANNER_OPTS).tier == tg.TIER_A

    def test_s5_tier_a_job_never_keys_on_the_word_research(self):
        """research_topic IS a real registered tool — only the job/manifest
        deferred-delivery framing is impossible."""
        ev = evidence(agent_prompt("research this"))
        for text in ("I ran research_topic on payer enrollment and pulled "
                     "three themes.",
                     "Research on that topic is something I can do right now."):
            assert not assess(text, ev, opts=BANNER_OPTS).flagged, text

    def test_s5_tier_a_job_drift_guard(self):
        ev = evidence(agent_prompt("research this"))
        response = CORPUS_BY_ID["C24"].response
        # In-turn progress trackers are live TODAY and must not disable it.
        assert assess(response, ev,
                      registry_names=["create_job", "update_job", "list_jobs",
                                      "research_topic"],
                      opts=BANNER_OPTS).flagged
        # A tool that can defer work past the turn auto-disables the rung.
        assert not assess(response, ev,
                          registry_names=["create_job",
                                          "enqueue_background_research"],
                          opts=BANNER_OPTS).flagged

    def test_s6_absolute_floor_beats_the_ratio(self):
        ev = evidence(agent_prompt("what's in drive?"))
        two = ("I searched Google Drive and found \"Alpha Rollout Brief\" and "
               "\"Beta Migration Runbook\".")
        three = two[:-1] + " and \"Gamma Ledger Spec\"."
        assert not assess(two, ev, opts=BANNER_OPTS).flagged
        assert assess(three, ev, opts=BANNER_OPTS).flagged

    def test_s6_ratio_gate(self):
        """Mostly-grounded turns never flag even with 3 novel atoms."""
        grounded = " ".join(f'"Grounded Card Number {i}"' for i in range(9))
        ev = evidence(agent_prompt("recap") + "\n" + grounded)
        response = (
            "I checked Guru: " + grounded +
            " plus \"Alpha Rollout Brief\", \"Beta Migration Runbook\" and "
            "\"Gamma Ledger Spec\"."
        )
        v = assess(response, ev, opts=BANNER_OPTS)
        assert v.ungrounded_count == 3
        assert not v.flagged

    def test_s6_binds_to_the_payload_not_the_call_count(self):
        """One unrelated tool call must not launder a fabricated answer."""
        ev = evidence(agent_prompt("what's in drive?"), tool_state=CONFIRMED_RAN,
                      tool_names=["list_tasks"],
                      payloads=['{"tasks": [{"title": "Something else"}]}'])
        assert assess(CORPUS_BY_ID["C19"].response, ev, opts=BANNER_OPTS).flagged

    def test_s6_negated_sentence_atoms_do_not_count(self):
        ev = evidence(agent_prompt("what's in drive?"))
        response = (
            "I searched Google Drive for the rollout material. There is no "
            "\"Alpha Rollout Brief\", no \"Beta Migration Runbook\" and no "
            "\"Gamma Ledger Spec\" anywhere in the scope."
        )
        assert not assess(response, ev, opts=BANNER_OPTS).flagged

    def test_s7_tier_c_is_off_by_default(self):
        ev = evidence(agent_prompt("does the KB cover aetna remittance?"))
        assert GroundingOptions().tier_c_enabled is False
        assert not assess(CORPUS_BY_ID["C26"].response, ev,
                          opts=BANNER_OPTS).flagged

    def test_tier_c_catches_fabricated_negative_when_enabled(self):
        ev = evidence(agent_prompt("does the KB cover aetna remittance?"))
        opts = GroundingOptions(mode=tg.MODE_BANNER, tier_c_enabled=True)
        v = assess(CORPUS_BY_ID["C26"].response, ev, opts=opts)
        assert v.flagged and v.tier == tg.TIER_C and v.gate == "S7"

    def test_tier_c_never_fires_when_a_tool_actually_ran(self):
        opts = GroundingOptions(mode=tg.MODE_BANNER, tier_c_enabled=True)
        case = CORPUS_BY_ID["C18"]
        ev = evidence(case.prompt, tool_state=CONFIRMED_RAN,
                      payloads=case.payloads, tool_names=case.tool_names)
        assert not assess(case.response, ev, opts=opts).flagged

    def test_tier_c_is_suppressed_by_an_authoritative_marker(self):
        """The drift-sensitive protection that keeps Tier C off by default."""
        opts = GroundingOptions(mode=tg.MODE_BANNER, tier_c_enabled=True)
        ev = evidence(agent_prompt("does the KB cover aetna?", scope=True))
        assert not assess(CORPUS_BY_ID["C26"].response, ev, opts=opts).flagged


# ---------------------------------------------------------------------------
# Action / rollout posture
# ---------------------------------------------------------------------------


class TestAction:
    def test_default_action_is_shadow_telemetry_only(self):
        assert GroundingOptions().mode == tg.MODE_TELEMETRY
        ev = evidence(CORPUS_BY_ID["C19"].prompt)
        v = assess(CORPUS_BY_ID["C19"].response, ev, opts=GroundingOptions())
        assert v.flagged
        assert v.action == "telemetry"
        assert v.banner == "", "shadow mode must emit no user-visible text"

    def test_off_mode_short_circuits(self):
        ev = evidence(CORPUS_BY_ID["C19"].prompt)
        v = assess(CORPUS_BY_ID["C19"].response, ev,
                   opts=GroundingOptions(mode=tg.MODE_OFF))
        assert v.state == tg.STATE_UNKNOWN and v.action == "none"

    def test_banner_mode_carries_text(self):
        ev = evidence(CORPUS_BY_ID["C19"].prompt)
        v = assess(CORPUS_BY_ID["C19"].response, ev, opts=BANNER_OPTS)
        assert v.action == "banner" and v.banner == tg.BANNER_TIER_B

    def test_telemetry_payload_is_phi_free(self):
        ev = evidence(CORPUS_BY_ID["C19"].prompt)
        v = assess(CORPUS_BY_ID["C19"].response, ev, opts=BANNER_OPTS)
        blob = str(v.as_telemetry())
        assert "Payments V2 Rollout Brief" not in blob
        assert "chris@cambric.ai" not in blob
        assert v.as_telemetry()["state"] == tg.STATE_FLAG

    def test_atom_digests_ride_only_a_flag(self):
        """An unsalted truncated sha1 of a short atom is reversible — a 7-digit
        member id was recovered from one by brute force. It is not a
        de-identified value, so it does not ride every ordinary turn."""
        clean_ev = evidence(agent_prompt("recap") + "\nGrounded Card Alpha")
        clean = assess('The "Grounded Card Alpha" one.', clean_ev,
                       opts=BANNER_OPTS)
        assert clean.state == tg.STATE_CLEAN
        assert "ungrounded_hashes" not in clean.as_telemetry()

        flagged = assess(CORPUS_BY_ID["C19"].response,
                         evidence(CORPUS_BY_ID["C19"].prompt), opts=BANNER_OPTS)
        assert flagged.flagged
        assert flagged.as_telemetry()["ungrounded_hashes"]

    def test_atom_digest_is_salted_per_process(self):
        import hashlib

        atom = extract_atoms('The "Aetna Remittance Ledger" card.')[0]
        naive = hashlib.sha1(atom.norm.encode("utf-8")).hexdigest()[:10]
        assert atom.digest != naive, (
            "digest is a bare sha1 of the atom — trivially brute-forceable"
        )

    def test_load_options_fails_to_off(self, monkeypatch):
        import src.data.settings_manager as sm

        def boom(*_a, **_k):
            raise OSError("settings.yaml is a smoking crater")

        monkeypatch.setattr(sm, "get_section", boom)
        assert tg.load_options().mode == tg.MODE_OFF

    def test_load_options_rejects_an_unknown_mode(self, monkeypatch):
        import src.data.settings_manager as sm

        monkeypatch.setattr(
            sm, "get_section",
            lambda *a, **k: {"turn_grounding": {"mode": "block"}})
        assert tg.load_options().mode == tg.MODE_OFF

    def test_load_options_reads_the_key(self, monkeypatch):
        import src.data.settings_manager as sm

        monkeypatch.setattr(
            sm, "get_section",
            lambda *a, **k: {"turn_grounding": {"mode": "banner",
                                                "tier_c_enabled": True}})
        opts = tg.load_options()
        assert opts.mode == tg.MODE_BANNER and opts.tier_c_enabled is True


# ---------------------------------------------------------------------------
# Atom extraction + grounding primitives
# ---------------------------------------------------------------------------


class TestAtoms:
    def test_counts_are_never_atoms(self):
        """[ENABLEMENT SCOPE] hands the model counts with zero tools."""
        atoms = extract_atoms("There are 47 tickets, 5 tasks and 196 documents.")
        assert [a.text for a in atoms] == []

    def test_single_capitalized_word_is_not_an_atom(self):
        assert extract_atoms("Drive is fine. Guru too. Zendesk as well.") == []

    def test_fenced_code_is_excluded(self):
        text = ('Here it is:\n```\n"Alpha Rollout Brief" 2026-07-09 TRC-0412\n'
                '```\nThat is all.')
        assert [a.norm for a in extract_atoms(text)] == []

    def test_atom_kinds(self):
        text = ('The card "Unified Remittance Ledger" (TRC-0412, ticket 88213) '
                'was modified 2026-07-09 — see https://example.com/x and '
                'rollout.pdf, per Product Launches.')
        kinds = {a.kind for a in extract_atoms(text)}
        assert {"quote", "ident", "date", "url", "title"} <= kinds

    def test_prose_dates_canonicalize_to_iso_on_both_sides(self):
        index = CorpusIndex("modified 2026-07-09 by the indexer")
        for text in ("last modified July 9", "modified Jul 9, 2026",
                     "modified 9 July 2026"):
            atoms = [a for a in extract_atoms(f"It was {text}.")
                     if a.kind == "date"]
            assert atoms, text
            assert all(is_grounded(a, index) for a in atoms), text

    def test_token_set_containment_grounds_a_paraphrase(self):
        index = CorpusIndex("- Rewrite the SSO troubleshooting card — due "
                            "2026-07-17 [high] (asana)")
        atom = extract_atoms('The "SSO troubleshooting rewrite" is first.')[0]
        assert not index.contains(atom.norm)
        assert is_grounded(atom, index)

    def test_token_containment_respects_the_window(self):
        far = "alpha rollout" + (" filler" * 200) + " brief"
        index = CorpusIndex(far)
        atom = extract_atoms('The "Alpha Rollout Brief" is missing.')[0]
        assert not is_grounded(atom, index)

    def test_atom_cap_is_enforced(self):
        text = " ".join(f'"Novel Card Title Number {i}"' for i in range(80))
        assert len(extract_atoms(text)) <= tg.MAX_ATOMS

    def test_extract_atoms_never_raises(self):
        for bad in (None, "", "\x00\x01", "```unclosed", "«»", "😀 😀 😀"):
            assert isinstance(extract_atoms(bad), list)

    def test_atom_digest_does_not_leak_text(self):
        atom = extract_atoms('The "Aetna Remittance Ledger" card.')[0]
        assert "aetna" not in atom.digest.lower()
        assert len(atom.digest) == 10

    def test_smart_quotes_and_unicode_fold(self):
        index = CorpusIndex("the “Payments V2 Rollout Brief” file")
        atom = extract_atoms('I found "Payments V2 Rollout Brief" there.')[0]
        assert is_grounded(atom, index)

    def test_probe_exhaustion_fails_open_not_closed(self):
        """window_contains_all gives up after _MAX_PROBE anchor hits. Returning
        False there reported a PRESENT atom as ungrounded — and the anchor is
        chosen as the atom's LONGEST token, i.e. its domain term, so it is
        exactly the word a long on-topic session repeats. 200 hits in 256 KB is
        one per 1.3 KB: routine. Undecided must never mean absent."""
        corpus = ("remittance chatter. " * 3000) + " guide aetna remittance section"
        index = CorpusIndex(corpus)
        atom = extract_atoms('The "remittance guide aetna" note.')[0]
        assert not index.contains(atom.norm), "force the token-set path"
        assert index.window_contains_all(
            [t for t in atom.norm.split() if len(t) > 2]) is None, (
            "precondition broken: the probe budget must actually run out"
        )
        assert is_grounded(atom, index) is True

    def test_is_grounded_on_an_empty_corpus_is_false_not_an_error(self):
        atom = extract_atoms('The "Alpha Rollout Brief" doc.')[0]
        assert is_grounded(atom, CorpusIndex("")) is False

    def test_corpus_cap_keeps_the_head_AND_the_tail(self):
        """A pure tail cap discarded the HEAD — and chat_engine.send() puts the
        injected context at exactly that head (`prompt = ctx + "\\n\\n" +
        prompt`). Losing it flipped a true, zero-tool, [ENABLEMENT SCOPE]-
        grounded answer from CLEAN to FLAG purely because the session grew."""
        index = CorpusIndex("head marker here " + "A" * (tg.CORPUS_CAP + 5000)
                            + " tail marker here")
        assert index.contains("head marker here"), "the injected context was evicted"
        assert index.contains("tail marker here")
        assert index.size <= tg.CORPUS_CAP
        assert index.truncated is True

    def test_untruncated_corpus_is_not_marked(self):
        index = CorpusIndex("short corpus")
        assert index.truncated is False

    def test_a_truncated_corpus_abstains_instead_of_flagging(self):
        """Whatever fell out of the cap cannot ground an atom, so "absent from
        a corpus we mangled" must never carry a verdict."""
        filler = "unrelated filler sentence about nothing. " * 8000
        assert len(filler) > tg.CORPUS_CAP
        ev = evidence(agent_prompt("what's in drive?") + "\n" + filler)
        v = assess(CORPUS_BY_ID["C19"].response, ev, opts=BANNER_OPTS)
        assert v.state == tg.STATE_UNKNOWN
        assert v.reason == "unknown:corpus:truncated"
        assert not v.flagged


# ---------------------------------------------------------------------------
# Performance (risk R3) — this runs on the Qt main thread
# ---------------------------------------------------------------------------


class TestPerformance:
    def test_assess_under_15ms_on_a_250kb_corpus(self):
        chunk = (
            "Assistant: I reviewed the Unified Remittance Ledger card and the "
            "ERA Line Matching card on 2026-07-09; ticket 88213 is the "
            "clearest example (TRC-0412). See https://example.com/doc-{i}.\n"
            "User: what about the Payments V2 Rollout Brief in Drive?\n"
        )
        big = "".join(chunk.replace("{i}", str(i)) for i in range(1400))
        assert len(big) > 250_000, len(big)
        ev = evidence(big)
        response = CORPUS_BY_ID["C19"].response

        assess(response, ev, opts=BANNER_OPTS)  # warm the regex cache
        runs = 7
        start = time.perf_counter()
        for _ in range(runs):
            assess(response, ev, opts=BANNER_OPTS)
        elapsed_ms = (time.perf_counter() - start) * 1000 / runs
        assert elapsed_ms < 15.0, f"assess took {elapsed_ms:.2f} ms"

    def test_extraction_scales_with_the_RESPONSE_not_just_the_corpus(self):
        """THE AXIS THE OLD BENCHMARKS MISSED. Both committed perf tests scale
        the CORPUS and hold the response at ~1-3 KB, but the cost driver is the
        RESPONSE: `claimed` grew unbounded and `_overlaps` linear-scanned it per
        regex match, so 4000 identifier candidates did ~8M span comparisons
        (330 ms) and a 56 KB identifier dump froze the Qt main thread for 1.3 s.
        MAX_ATOMS bounds only the returned list. Asserts sub-quadratic growth,
        not a wall-clock number, so it is not flaky on a loaded machine."""
        def _cost(n):
            text = " ".join(str(100000 + i) for i in range(n))
            best = None
            for _ in range(3):
                start = time.perf_counter()
                extract_atoms(text)
                el = time.perf_counter() - start
                best = el if best is None else min(best, el)
            return best

        extract_atoms("warm up 12345")
        small = _cost(1000)
        large = _cost(4000)
        # Quadratic would be ~16x. Allow generous headroom for timer noise.
        assert large < small * 8, (
            f"extraction is superlinear in response length: "
            f"1000 idents {small*1000:.1f} ms -> 4000 idents {large*1000:.1f} ms"
        )

    def test_assess_is_bounded_on_a_huge_response(self):
        """Nothing caps the model's output length — the live enablement client
        ignores max_tokens — so the input is capped here instead."""
        ev = evidence(agent_prompt("dump everything"))
        huge = ("I searched Google Drive and found \"Doc Title Number "
                "%d\" (2026-07-09, id 88213%d).\n" )
        response = "".join(huge % (i, i) for i in range(4000))
        assert len(response) > 250_000
        start = time.perf_counter()
        assess(response, ev, opts=BANNER_OPTS)
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert elapsed_ms < 250.0, f"assess took {elapsed_ms:.0f} ms"

    def test_a_long_dotted_run_does_not_backtrack(self):
        """The bare-domain regex had an unbounded label repetition — 6000
        segments cost 1.3 s. Bounded to 8 labels."""
        text = ".".join("abcd" for _ in range(4000)) + ".com"
        start = time.perf_counter()
        extract_atoms(text)
        assert (time.perf_counter() - start) * 1000 < 200.0

    def test_worst_case_all_atoms_ungrounded_is_still_bounded(self):
        big = ("filler text about nothing in particular. " * 8000)
        ev = evidence(big)
        response = " ".join(f'I searched Drive and found "Novel Card Title '
                            f'Number {i}".' for i in range(60))
        start = time.perf_counter()
        assess(response, ev, opts=BANNER_OPTS)
        assert (time.perf_counter() - start) * 1000 < 60.0


# ---------------------------------------------------------------------------
# Ledger helpers — self-contained fixture, no engine, no subprocess
# ---------------------------------------------------------------------------


DDL = """
CREATE TABLE chat_tool_executions (
    execution_id  TEXT PRIMARY KEY,
    message_id    TEXT NOT NULL,
    session_id    TEXT NOT NULL,
    tool_name     TEXT NOT NULL,
    args_json     TEXT,
    result_json   TEXT,
    result_rows   INTEGER,
    tables_touched TEXT,
    elapsed_ms    INTEGER,
    error         TEXT,
    created_at    DATETIME DEFAULT CURRENT_TIMESTAMP
);
"""


@pytest.fixture
def ledger_db(tmp_path):
    """A real on-disk DB so the helpers exercise get_connection for real.

    WAL is set up front because the live warehouse is WAL and a read-only
    connection cannot switch journal mode (get_connection would raise).
    """
    path = tmp_path / "ledger.db"
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(DDL)
    conn.commit()
    conn.close()
    return str(path)


def _insert(db_path, tool_name, result_json, session_id="s1"):
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO chat_tool_executions (execution_id, message_id, "
        "session_id, tool_name, result_json) VALUES (?, ?, ?, ?, ?)",
        (f"e-{time.perf_counter_ns()}", "", session_id, tool_name, result_json))
    conn.commit()
    conn.close()


class TestLedgerHelpers:
    def test_snapshot_on_an_empty_table(self, ledger_db):
        mark = snapshot_tool_ledger(ledger_db)
        assert mark == LedgerMark(max_rowid=0, count=0)

    def test_confirmed_empty_when_nothing_ran(self, ledger_db):
        _insert(ledger_db, "kb_search", "{}")
        mark = snapshot_tool_ledger(ledger_db)
        ev = resolve_tool_evidence(ledger_db, mark, {"tool_calls": 0}, True)
        assert ev.state == CONFIRMED_EMPTY and ev.ok and ev.payloads_complete

    def test_confirmed_ran_carries_the_payloads(self, ledger_db):
        mark = snapshot_tool_ledger(ledger_db)
        _insert(ledger_db, "search_google_drive", '{"files": ["Rollout Brief"]}')
        ev = resolve_tool_evidence(ledger_db, mark, {"tool_calls": 0}, True)
        assert ev.state == CONFIRMED_RAN
        assert ev.tool_names == ["search_google_drive"]
        assert ev.payloads_complete and "Rollout Brief" in ev.payloads[0]

    def test_no_session_filter_the_adhoc_probe_trap(self, ledger_db):
        """48% of live rows carry session_id='adhoc_probe'. A session filter
        would read a real tool-backed turn as empty."""
        mark = snapshot_tool_ledger(ledger_db)
        _insert(ledger_db, "kb_search", '{"cards": []}', session_id="adhoc_probe")
        ev = resolve_tool_evidence(ledger_db, mark, {}, True)
        assert ev.state == CONFIRMED_RAN

    def test_truncation_at_the_phi_cap_is_reported(self, ledger_db):
        mark = snapshot_tool_ledger(ledger_db)
        _insert(ledger_db, "kb_search", "x" * tg.TRUNCATION_CAP)
        ev = resolve_tool_evidence(ledger_db, mark, {}, True)
        assert ev.truncated is True and ev.state == CONFIRMED_RAN

    def test_below_the_cap_is_not_truncated(self, ledger_db):
        mark = snapshot_tool_ledger(ledger_db)
        _insert(ledger_db, "kb_search", "x" * (tg.TRUNCATION_CAP - 1))
        assert resolve_tool_evidence(ledger_db, mark, {}, True).truncated is False

    def test_rowid_reuse_is_caught_by_the_carried_count(self, ledger_db):
        """MAX(rowid) ALONE IS INSUFFICIENT. Delete the top two rows, insert
        one — MAX(rowid) is unchanged and no row sits above the watermark, so a
        max-only implementation reports CONFIRMED_EMPTY while a tool really
        ran. COUNT(*) makes it visible."""
        for i in range(3):
            _insert(ledger_db, f"t{i}", "{}")
        mark = snapshot_tool_ledger(ledger_db)
        assert (mark.max_rowid, mark.count) == (3, 3)

        conn = sqlite3.connect(ledger_db)
        conn.execute("DELETE FROM chat_tool_executions WHERE rowid IN (2, 3)")
        conn.commit()
        conn.close()
        _insert(ledger_db, "search_google_drive", '{"files": []}')

        after = sqlite3.connect(ledger_db)
        max_after, count_after = after.execute(
            "SELECT MAX(rowid), COUNT(*) FROM chat_tool_executions").fetchone()
        after.close()
        assert max_after <= mark.max_rowid  # the reuse actually happened
        assert count_after != mark.count

        ev = resolve_tool_evidence(ledger_db, mark, {}, True)
        assert ev.state == UNKNOWN and ev.detail == "ledger_mismatch"

    def test_delete_n_insert_n_reusing_the_watermark_rowid(self, ledger_db):
        """THE SHAPE THE ARITHMETIC CANNOT SEE, and the one the product really
        produces. `chat_session.delete_session` removes a session's ledger rows
        mid-turn (Renn's history-panel Delete button, ungated on busy). Delete
        exactly as many rows as the turn inserts, with the max rowid reused,
        and BOTH MAX(rowid) and COUNT(*) come back unchanged — so a genuine
        search_google_drive turn read CONFIRMED_EMPTY and Tier B accused it of
        fabricating (`B:google drive:7of7_ungrounded`, banner and all).
        LedgerMark.anchor_id pins the IDENTITY of the row at the watermark."""
        for i in range(3):
            _insert(ledger_db, f"t{i}", "{}", session_id="old-chat")
        mark = snapshot_tool_ledger(ledger_db)
        assert (mark.max_rowid, mark.count) == (3, 3)
        assert mark.anchor_id, "the watermark carries no identity"

        conn = sqlite3.connect(ledger_db)
        conn.execute("DELETE FROM chat_tool_executions WHERE rowid = 3")
        conn.commit()
        conn.close()
        # The real turn's tool row now REUSES rowid 3.
        _insert(ledger_db, "search_google_drive", '{"files": ["Rollout"]}')

        after = sqlite3.connect(ledger_db)
        max_after, count_after = after.execute(
            "SELECT MAX(rowid), COUNT(*) FROM chat_tool_executions").fetchone()
        after.close()
        assert (max_after, count_after) == (mark.max_rowid, mark.count), (
            "precondition broken: this test must reproduce the shape where the "
            "count/max arithmetic is blind"
        )

        ev = resolve_tool_evidence(ledger_db, mark, {}, True)
        assert ev.state == UNKNOWN, (
            "a real tool-backed turn read as CONFIRMED_EMPTY after a "
            "concurrent session delete"
        )
        assert ev.detail == "ledger_anchor_mismatch"

    def test_the_anchor_check_does_not_fire_on_a_normal_turn(self, ledger_db):
        """Teeth: the identity check must not turn every ordinary turn into
        UNKNOWN — that would make the whole feature inert."""
        for i in range(3):
            _insert(ledger_db, f"t{i}", "{}")
        mark = snapshot_tool_ledger(ledger_db)
        assert resolve_tool_evidence(ledger_db, mark, {}, True).state == \
            CONFIRMED_EMPTY
        _insert(ledger_db, "kb_search", '{"cards": []}')
        assert resolve_tool_evidence(ledger_db, mark, {}, True).state == \
            CONFIRMED_RAN

    def test_a_plain_delete_also_degrades_to_unknown(self, ledger_db):
        for i in range(3):
            _insert(ledger_db, f"t{i}", "{}")
        mark = snapshot_tool_ledger(ledger_db)
        conn = sqlite3.connect(ledger_db)
        conn.execute("DELETE FROM chat_tool_executions WHERE rowid = 3")
        conn.commit()
        conn.close()
        assert resolve_tool_evidence(ledger_db, mark, {}, True).state == UNKNOWN

    def test_telemetry_is_positive_only_never_a_zero_proof(self, ledger_db):
        """telemetry['tool_calls'] is structurally always 0 on the Claude leg
        and drops ~70 of 86 tools on the Gemini leg. A zero can never
        contradict the ledger."""
        mark = snapshot_tool_ledger(ledger_db)
        _insert(ledger_db, "search_google_drive", '{"files": []}')
        ev = resolve_tool_evidence(ledger_db, mark, {"tool_calls": 0}, True)
        assert ev.state == CONFIRMED_RAN

    def test_positive_telemetry_without_rows_is_ran_but_unbound(self, ledger_db):
        mark = snapshot_tool_ledger(ledger_db)
        ev = resolve_tool_evidence(ledger_db, mark, {"tool_calls": 2}, True)
        assert ev.state == CONFIRMED_RAN and ev.payloads_complete is False

    def test_malformed_telemetry_does_not_raise(self, ledger_db):
        mark = snapshot_tool_ledger(ledger_db)
        for tel in ({"tool_calls": "banana"}, {"tool_calls": None}, {}, None):
            assert resolve_tool_evidence(ledger_db, mark, tel, True).ok

    # ---- fail-open, one test per error path ----------------------------

    def test_bad_db_path_snapshot(self, tmp_path):
        assert snapshot_tool_ledger(str(tmp_path / "nope" / "x.db")) is None
        assert snapshot_tool_ledger("") is None
        assert snapshot_tool_ledger(None) is None

    def test_missing_table_snapshot(self, tmp_path):
        path = tmp_path / "empty.db"
        sqlite3.connect(str(path)).close()
        assert snapshot_tool_ledger(str(path)) is None

    def test_missing_table_resolve(self, tmp_path):
        path = tmp_path / "empty2.db"
        sqlite3.connect(str(path)).close()
        ev = resolve_tool_evidence(str(path), LedgerMark(0, 0), {}, True)
        assert ev.state == UNKNOWN and ev.detail == "read_failed"

    def test_unreadable_rows_resolve(self, ledger_db, monkeypatch):
        import src.data.connection_factory as cf

        def boom(*_a, **_k):
            raise sqlite3.OperationalError("database is locked")

        monkeypatch.setattr(cf, "get_connection", boom)
        assert resolve_tool_evidence(ledger_db, LedgerMark(0, 0), {},
                                     True).state == UNKNOWN
        assert snapshot_tool_ledger(ledger_db) is None

    def test_no_mark_resolve(self, ledger_db):
        ev = resolve_tool_evidence(ledger_db, None, {"tool_calls": 5}, True)
        assert ev.state == UNKNOWN and ev.detail == "no_mark"

    def test_surface_off_resolve(self, ledger_db):
        mark = snapshot_tool_ledger(ledger_db)
        ev = resolve_tool_evidence(ledger_db, mark, {}, False)
        assert ev.state == UNKNOWN and ev.detail == "surface_off"

    def test_no_db_path_resolve(self):
        assert resolve_tool_evidence("", LedgerMark(0, 0), {},
                                     True).detail == "no_db_path"


# ---------------------------------------------------------------------------
# Evidence plumbing + total fail-open
# ---------------------------------------------------------------------------


class TestEvidence:
    def test_add_accumulates_without_clobbering(self):
        ev = TurnEvidence()
        ev.add("prompt", "one")
        ev.add("prompt", "two")
        ev.add("system", "sys")
        ev.add("tool_result", "payload")
        ev.add("nonsense", "ignored")
        assert "one" in ev.prompt and "two" in ev.prompt
        assert ev.tool_payloads == ["payload"]
        assert "ignored" not in ev.corpus_text()

    def test_add_never_raises(self):
        ev = TurnEvidence()
        for bad in (None, "", 0):
            ev.add("prompt", bad)  # type: ignore[arg-type]
        assert ev.prompt == ""

    def test_apply_tool_evidence_folds_everything(self, ledger_db):
        mark = snapshot_tool_ledger(ledger_db)
        _insert(ledger_db, "kb_search", '{"cards": ["Alpha"]}')
        ev = TurnEvidence(surface_ok=True)
        ev.add("prompt", "p")
        ev.apply_tool_evidence(resolve_tool_evidence(ledger_db, mark, {}, True))
        assert ev.tool_state == CONFIRMED_RAN and ev.evidence_ok
        assert ev.tool_names == ["kb_search"]
        assert "Alpha" in ev.corpus_text()

    def test_apply_none_degrades_to_unknown(self):
        ev = TurnEvidence(surface_ok=True, tool_state=CONFIRMED_EMPTY)
        ev.apply_tool_evidence(None)
        assert ev.tool_state == UNKNOWN and ev.evidence_ok is False

    def test_assess_never_raises_on_garbage(self):
        class Exploding:
            surface_ok = True

            @property
            def prompt(self):
                raise RuntimeError("boom")

        v = assess("anything", Exploding(), opts=BANNER_OPTS)  # type: ignore
        assert v.state == tg.STATE_UNKNOWN and not v.flagged

    def test_assess_with_none_evidence(self):
        assert assess("x", None, opts=BANNER_OPTS).state == tg.STATE_UNKNOWN

    def test_assess_with_none_response(self):
        ev = evidence(agent_prompt("x"))
        assert not assess(None, ev, opts=BANNER_OPTS).flagged  # type: ignore

    def test_module_is_pure_no_qt_no_llm(self):
        import pathlib

        src = (pathlib.Path(tg.__file__)).read_text(encoding="utf-8")
        for banned in ("PySide6", "QtCore", "QWidget", "gemini_client",
                       "claude_client", "build_client_for_task", "sqlite3.connect"):
            assert banned not in src, f"turn_grounding must not import {banned}"
