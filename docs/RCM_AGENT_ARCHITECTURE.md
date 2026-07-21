# RCM Ticket-Resolution Agent — Architecture

**Status:** Draft / design proposal
**Author:** Enablement Engineering
**Date:** 2026-07-07
**Related:** [`docs/SECURITY_COMPLIANCE_REVIEW.md`](SECURITY_COMPLIANCE_REVIEW.md) · [`docs/WORKCLAUDE_IMPLEMENTATION_GUIDE.md`](WORKCLAUDE_IMPLEMENTATION_GUIDE.md) · [`docs/AI_REPORTS.md`](AI_REPORTS.md) · research brief (shareable artifact)

---

## 1. Summary

A Claude-on-Bedrock agent that reads an incoming Alma RCM support ticket, grounds itself in the Guru knowledge base, and then either (a) drafts a resolution for a human to approve and post to Zendesk, or (b) files a structured Asana task that hands the ticket to the ops team for last-mile resolution. It never acts autonomously on a provider-facing surface without a human gate.

This is deliberately **not** an autonomous ticket-closer. It is a **triage → ground → draft → verify → gate → act** pipeline whose value is measured by *deflection of the resolvable slice* and *quality of the handoff packet on everything else*. The design matches the pattern Anthropic prescribes in its customer-support guide and markets for healthcare RCM, and it reuses machinery Alma Insights already has.

### What already exists (verified)

| Capability | Where it lives today |
|---|---|
| Claude routed through **AWS Bedrock under BAA** | [`src/gemini/client_factory.py:99`](../src/gemini/client_factory.py) `build_client_for_task()` → `claude` provider comment: *"Enablement Claude is BAA-routed through AWS Bedrock (the org's PHI-approved…)"* |
| **Draft approval gate** (human sign-off) | `migrations/037_draft_approval_gate.sql` |
| **Action request + resolution** tables | `migrations/038_action_requests.sql`, `migrations/039_action_resolved.sql` |
| **Zendesk** content / macros | `migrations/030_zendesk_content.sql` |
| **Asana** write-back | `migrations/032_asana_writeback.sql` |
| **Grounding harness** (count/entity/time fidelity, 0.75 threshold) | [`src/data/report_grounding.py`](../src/data/report_grounding.py) |
| **Doc → Guru card update loop** (feedback) | `src/data/content_update/` (orchestrator, grounding, effectiveness, provenance, publish) |
| **Guru card health / signals** | `migrations/034_guru_card_health.sql`, `migrations/035_card_signal_snapshot.sql` |
| **PII redaction** (currently fails **open** — must change) | [`src/llm/claude_client.py:83`](../src/llm/claude_client.py) |

The net-new work is mostly **wiring**, plus one genuinely missing capability: **claim-level read access** (§7.2).

---

## 2. Goals / Non-goals

**Goals**
- Auto-resolve a *scoped, measured* subset of routine RCM tickets (with human approval before posting).
- Produce a high-quality, structured Asana handoff for everything else, so ops resolves faster.
- Ground every answer in Guru; refuse/escalate when unsupported.
- Keep PHI inside the BAA-covered Bedrock lane, fail-closed.
- Improve over time by feeding ops resolutions back into Guru.

**Non-goals**
- Autonomous claim resolution in payer portals / the PM/billing system of record.
- Auto-posting provider-facing responses without human review.
- Replacing the ops team. The design *augments* it (cf. Klarna's public walk-back of over-automation).
- A full airgap — the system is not airgap-capable by design (per security review).

---

## 3. What "resolve" means here — the deflection dial

Success is **not** a single "solve rate." It is where we can safely set two dials, measured **per ticket category**:

- **Deflection** — fraction handled without a human doing the reasoning. Anthropic's *target* is 70–80%; independent enterprise benchmarks put the real-world median near **~41%**. Budget for ~40%, ramp up per category as evidence accrues.
- **Escalation accuracy** — how reliably the agent knows it *shouldn't* answer. Target **≥95%**. This is the safety-critical dial: a false "I can handle this" on a billing rule sends a wrong, money-affecting answer to a provider.

The two failure directions are asymmetric:
- Too aggressive → wrong answer reaches a provider (compliance/financial risk). **Dangerous.**
- Too conservative → everything escalates, no leverage (ROI collapse). **Value-killing.**

Default posture: **conservative, auto-resolve unlocked category-by-category** only after its reversal rate is measured.

---

## 4. Ticket taxonomy & routing tiers

Every ticket is classified into a tier that determines how far the agent is allowed to go.

| Tier | Ticket shape | Agent action | Human role |
|---|---|---|---|
| **T0 — Auto-resolvable** | Informational / policy, a confident Guru match exists (e.g. "timely-filing limit for payer X", "how to submit a corrected claim") | Draft answer, grounded + cited | Approve → post (gate can be relaxed per-category once reversal rate is proven low) |
| **T1 — Draft-for-review** | Routine but case-flavored; Guru covers the policy but specifics need a human eye | Draft answer + cite sources + flag uncertainties | **Always** approves before post |
| **T2 — Escalate-with-packet** | Requires action in a system the agent can't touch (resubmit, correct a code, appeal, payer portal) | File structured Asana task with full context + recommended action | Ops resolves last-mile; autocomms fire on *ops* resolution |
| **T3 — Hard escalate** | Ambiguous, urgent, PHI-sensitive edge case, or no Guru support at all | File Asana task flagged for human triage; no drafted answer | Ops owns entirely |

Tier is itself an LLM classification, gated by the escalation-accuracy dial (§8). When in doubt, the agent escalates *up* a tier, never down.

---

## 5. High-level architecture

```
                        ┌───────────────────────────────────────────────┐
   Zendesk ticket  ───► │  RCM Agent Orchestrator                        │
   (new / updated)      │  (src/services/rcm_agent/)                     │
                        │                                                │
                        │   1. Redact (FAIL-CLOSED)  ── src/llm redaction│
                        │   2. Triage / tier classify                    │
                        │   3. Retrieve  ──────────────► Guru KB (RAG)    │
                        │   4. Claim lookup ───────────► RCM system of    │
                        │                                record (read)    │
                        │   5. Draft (Generator)                         │
                        │   6. Verify (Verifier + grounding harness)     │
                        │   7. Confidence gate  ── report_grounding 0.75 │
                        │                                                │
                        └───────┬──────────────┬─────────────┬──────────┘
                                │              │             │
                     T0/T1 draft│      T2/T3 packet│   below threshold│
                                ▼              ▼             ▼
                     ┌──────────────┐  ┌──────────────┐  ┌────────────┐
                     │ Draft approval│  │ Asana task   │  │ Escalate   │
                     │ gate (mig 037)│  │ (mig 032/038)│  │ (mig 039)  │
                     └──────┬───────┘  └──────┬───────┘  └────────────┘
                            │ approved         │ resolved by ops
                            ▼                  ▼
                     ┌──────────────┐   ┌─────────────────────────────┐
                     │ Post response│   │ Feedback loop → Guru update  │
                     │ to Zendesk   │   │ (src/data/content_update/)   │
                     │ (mig 030)    │   └─────────────────────────────┘
                     └──────────────┘
```

All LLM calls go through `build_client_for_task()`; RCM tasks route to the **Claude/Bedrock BAA lane**. DB access is `get_connection()` only; config via `settings_manager`. Cross-thread Qt via signals/slots.

---

## 6. Agent topology — Generator → Verifier

We adopt Anthropic's documented **Generator–Verifier** support pattern (two roles, not one monolith), which the research identified as the closest real-world topology to a "multiple Claude bots" design.

1. **Triage agent** — classifies tier (§4), extracts entities (payer, TRC, claim id, member context), decides the retrieval query. Cheap/fast (Haiku-class where available).
2. **Generator agent** — drafts the resolution using the retrieved Guru cards + claim context. Sonnet-class.
3. **Verifier agent** — independently checks the draft *against the retrieved cards*: does every claim in the draft trace to a source? Tone/brand? All raised issues addressed? Runs the grounding harness. Can veto → downgrade to a lower tier.

> **Reliability note.** Multi-agent systems are non-deterministic between identical runs and minor failures compound in long stateful sessions (Anthropic engineering). Keep sessions short and stateless per ticket; make the Verifier a hard gate, not an advisory.

---

## 7. Tool surface

Four tools, exposed to the agent behind the resolver/approval pattern already used by Renn (`chat_action_requests`, two-phase resolver tools).

### 7.1 Read tools

- **`guru_search(query)`** — semantic + FTS retrieval over Guru cards. The grounding source of record. Returns cards with ids for citation.
- **`claim_lookup(ticket)`** — **the missing capability (see below)** — read-only access to the specific claim's status, denial/EOB codes, eligibility, remittance from the PM/billing system or clearinghouse.

### 7.2 The claim-data gate (the crux)

Guru tells the agent the *policy*; `claim_lookup` tells it the answer to *this* ticket. Without it, the agent can only give generically-correct guidance and the Asana packet degrades to "provider says claim denied, here's the ticket text" — which is what ops already had.

- **Without claim data:** the system still works as a *router* (T0 informational + T2/T3 escalation), but adds routing, not leverage.
- **With claim data:** the Asana packet becomes an *ops multiplier* — *"Claim 12345, denied CO-197, no auth on file, member eligible, Guru card X says submit retro-auth via portal — recommend ops do Y."*

**Recommendation:** treat read-only `claim_lookup` as a first-class dependency, not a phase-2 nicety. It is what separates "neat" from "cuts the queue." Its integration surface (which system, which API, PHI handling) is the top open question (§14).

### 7.3 Write tools (all behind the human gate)

- **`file_asana_task(packet)`** — creates the ops handoff (`migrations/032_asana_writeback.sql`, `038_action_requests.sql`). Never gated by human for T2/T3 *creation* (filing a task is low-risk), but the task carries the AI's recommendation clearly labeled as AI-generated.
- **`post_zendesk_response(draft)`** — posts the provider-facing reply (`migrations/030_zendesk_content.sql`). **Always** passes through the draft-approval gate (§9) for T0/T1.

---

## 8. Confidence gating & escalation

The escalate-vs-answer decision is a composite score, not a single number:

1. **Retrieval confidence** — semantic similarity of the top Guru card(s) to the ticket. Below a floor → no card matches → escalate (never generate from parametric memory).
2. **Grounding score** — the Verifier runs [`report_grounding.py`](../src/data/report_grounding.py)'s fidelity dimensions (count / entity / time). Reuse the existing **0.75** threshold as the initial gate; below → flag `low_confidence` and downgrade tier.
3. **Category policy** — each ticket category carries its own auto-resolve threshold, tightened or loosened from observed reversal rate.

This operationalizes Anthropic's ≥95% escalation-accuracy target into an implementable rule, and mirrors DoorDash's RAG + LLM-guardrail + LLM-judge stack (verified as an effective grounding pattern).

---

## 9. Human-in-the-loop & the autocomms checkpoint

**The single most important safety property:** no AI-drafted, provider-facing message is sent without a human in the loop, unless a category has *earned* auto-post through a measured-low reversal rate.

- **Draft approval gate** (`migrations/037_draft_approval_gate.sql`) sits between `post_zendesk_response` and Zendesk. A reviewer sees the draft, the cited Guru cards, and the grounding score.
- **Autocomms fire on *ops resolution*, not on the agent's classification.** This is the decisive design choice: automated outbound triggers off a human-resolved Asana task (`migrations/039_action_resolved.sql`), never off the agent's own triage. Firing autocomms on classification would re-open exactly the risk the handoff was meant to close.

**Why this is non-negotiable:** the Air Canada tribunal held the company liable for its chatbot's false output and rejected the "the bot is its own entity" defense. In healthcare, guidance further requires human oversight for AI-influenced billing decisions. An unreviewed wrong answer is *our* liability.

---

## 10. The Asana handoff packet

For T2/T3, the value is the packet's structure. Minimum schema:

```json
{
  "ticket_id": "...",
  "tier": "T2",
  "summary": "one-line problem statement",
  "extracted": { "payer": "...", "trc_code": "...", "claim_id": "...", "member_ctx": "..." },
  "claim_state": { "status": "denied", "denial_code": "CO-197", "eligibility": "active", "remit": "..." },
  "suspected_root_cause": "no prior auth on file",
  "guru_refs": ["card_id:...", "card_id:..."],
  "recommended_action": "submit retro-auth via payer portal",
  "confidence": 0.62,
  "ai_generated": true
}
```

The `claim_state` block is populated by `claim_lookup` (§7.2) — its presence is what makes the packet an accelerant rather than a hop.

---

## 11. PHI & compliance

- **Lane:** RCM tasks route to Claude via **Bedrock under the org's BAA** (already the behavior of the `claude` provider in `client_factory.py`). Do not allow override to a non-BAA path for any PHI-touching task — lock the routing for `rcm_*` task types.
- **Redaction must become fail-closed.** Today [`claude_client.py:83`](../src/llm/claude_client.py) logs `"PII redaction failed, sending un-redacted"` and proceeds — a **fail-open** path. For a system that sends PHI to Claude on every ticket, redaction failure must **block the call**, not degrade silently. This is a prerequisite, not a follow-up.
- **Auto-post boundary:** posting an AI-drafted, PHI-bearing response to Zendesk without human review is both a liability exposure (§9) and a compliance question. Keep the gate on until Legal/Compliance signs off per category.
- Cross-reference the existing [`docs/SECURITY_COMPLIANCE_REVIEW.md`](SECURITY_COMPLIANCE_REVIEW.md) findings (redaction fails open; PHI routing overridable; Claude CLI telemetry).

---

## 12. Feedback loop — deflection that climbs

When ops resolves an Asana task, the resolution should not evaporate. Route it through the existing **`src/data/content_update/`** pipeline:

1. `action_resolved` (mig 039) fires on ops close-out.
2. The resolution + the original ticket become a candidate Guru update (identify → draft → ground → validate → publish, already implemented in `content_update/`).
3. `effectiveness_proxy` / `effectiveness` track whether the updated card later deflects similar tickets.

Result: today's escalation becomes tomorrow's T0 auto-resolvable ticket, and the deflection rate rises over time instead of freezing at day-1 accuracy. This is the difference between a static router and a compounding system.

---

## 13. Metrics & rollout

**Instrument from day one (per category):**
- Deflection rate (T0 auto-resolved / total)
- **Reversal rate** (approved drafts later corrected / edited by a human) — the safety metric
- One-touch resolution (T2 tasks ops closed without back-and-forth)
- Grounding score distribution; escalation-accuracy (audited sample)
- Cost per ticket (Bedrock tokens)

**Rollout stages:**
1. **Shadow mode** — agent drafts + files silently; humans handle everything; compare agent output to human resolution. No provider-facing output.
2. **Assisted** — drafts surface in the reviewer queue behind the approval gate; humans approve/edit/reject. Measure reversal rate.
3. **Category-by-category auto-post** — unlock T0 auto-post only for categories with proven-low reversal, one at a time. Never a global switch.
4. **Feedback loop on** — ops resolutions flow to Guru; watch deflection climb.

---

## 14. Risks & open questions

1. **Claim-data access (top priority).** Which system of record exposes claim status / EOB / eligibility, via what API, and with what PHI controls? Without it the packet is thin. — *Owner: TBD*
2. **HIPAA boundary on auto-post.** Can an AI-drafted, PHI-bearing Zendesk reply ever go out without human review, and under what category conditions? — *Owner: Compliance*
3. **Fail-closed redaction.** Concrete change at `claude_client.py:83` + tests asserting the call blocks on redaction failure. — *Owner: Eng*
4. **Escalation-accuracy calibration.** Turning the ≥95% target into audited numeric thresholds per category. — *Owner: Eng + Ops*
5. **Multi-agent cost & latency.** Generator + Verifier + Triage per ticket — is the per-ticket cost justified vs. a single grounded call? Benchmark before committing to three roles.
6. **Guru coverage ceiling.** Reliability is bounded by KB coverage/freshness; the feedback loop mitigates but doesn't eliminate this.

---

## 15. Mapping to existing code

| Design component | Reuse / extend |
|---|---|
| LLM routing (Bedrock BAA) | `build_client_for_task("rcm_resolution")` — add task type to routing table in `client_factory.py` |
| Redaction (make fail-closed) | `src/llm/claude_client.py` `_redact_text` / call sites |
| Grounding / confidence gate | `src/data/report_grounding.py` (0.75 threshold) |
| Draft approval gate | `migrations/037_draft_approval_gate.sql` + reviewer UI |
| Asana handoff | `migrations/032_asana_writeback.sql`, `038_action_requests.sql`; `AsanaClient` |
| Zendesk post | `migrations/030_zendesk_content.sql` |
| Action lifecycle | `migrations/038_action_requests.sql` → `039_action_resolved.sql` |
| Feedback → Guru | `src/data/content_update/` (orchestrator, grounding, effectiveness, publish) |
| Two-phase resolver / action-request pattern | Renn `chat_action_requests` (mig 038) precedent |
| New orchestrator | `src/services/rcm_agent/` (proposed) |

---

## 16. One-paragraph verdict

With the human handoff, *"reliably resolve a defined slice of RCM tickets + reliably route the rest with an actionable packet"* is achievable, evidence-backed, and mostly a matter of wiring components Alma already has. The three things that decide *how* reliable: how tightly the deflection dial is calibrated per category, how much claim-level context `claim_lookup` can pull into the Asana packet, and keeping the human checkpoint firmly between the agent and any provider-facing message. Autonomous end-to-end resolution is **not** in scope and should not be promised.
