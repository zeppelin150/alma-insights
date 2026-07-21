"""
archetypes.py — multi-turn provider<->CXA thread builders, one per TRC family.

Each builder returns a list of (role, text) turns built from a *matched* scenario
pair (opener -> response) so content is always coherent. ``build_thread(ctx)``
wraps a builder with an optional identity exchange, an optional logistics tail, a
closing, and (for needle tickets) a cancellation-intent injection. Surface variety
comes from: which scenario, entity variation (real payer / synthetic people /
amounts / CPT / dates), varied acks/closings, and the optional tail.

Voice is grounded in real research: the 2026-05-20 Aetna/Alma rate change, Alma's
Payout Confidence / next-payout-deduction policy, COB / deductible "charged more
than expected" confusion, CAQH credentialing denials, EAP (Spring Health / Lyra)
auth gaps, and Alma billing/support pain points.

Roles:  provider = the therapist (Alma member, "end-user")
        client   = the therapist's client (occasionally writes in directly)
        agent    = Alma Customer Experience Associate (CXA)
"""
from __future__ import annotations

import random
from datetime import timedelta

_CPT_WEIGHTED = (
    ["90837"] * 9 + ["90834"] * 7 + ["90791"] * 3 + ["90847"] * 3
    + ["90832"] * 2 + ["99214"] * 2 + ["90853"] * 1 + ["90846"] * 1
)


def pick_cpt(rng: random.Random) -> str:
    return rng.choice(_CPT_WEIGHTED)


def money(rng: random.Random, lo: int, hi: int) -> str:
    cents = rng.choice([0, 0, 13, 25, 42, 50, 7, 88, 75, 60])
    return f"{rng.randint(lo, hi)}.{cents:02d}"


def claim_no(rng):
    return "CLM" + "".join(rng.choice("0123456789") for _ in range(9))


# ==========================================================================
# Shared phrase banks
# ==========================================================================
AGENT_ACK = [
    "Thanks for reaching out — happy to help with this.",
    "Thank you for flagging this, I can look into it for you.",
    "Appreciate you reaching out — let me dig into this.",
    "Thanks for the details — I can definitely help here.",
    "Got it, thank you. Let me take a look right away.",
    "Hi {pf}, thanks for writing in. I can help you sort this out.",
    "Thanks for your patience — let me pull this up.",
]
AGENT_INFO_REQUEST = [
    "To pull the right record, can you confirm the client's initials and the date of service?",
    "Can you confirm the member ID and date of service so I can locate the claim?",
    "To make sure I'm looking at the right account, what are the client's initials and the DOS?",
    "Could you share the date of service and the client's initials so I can find it?",
    "So I can verify, can you confirm the payer and the member ID on the card?",
]
PROVIDER_INFO_GIVE = [
    "Sure — client initials {ci}, date of service {dos}.",
    "Of course: {ci}, seen on {dos}. Member ID is {mid}.",
    "It's {ci}, DOS {dos}.",
    "Member ID {mid}, and the date of service was {dos}.",
    "{ci}, {dos} — let me know if you need anything else.",
]
PROVIDER_LOGISTICS_Q = [
    "How long should that take to resolve?",
    "Is there anything I need to do on my end?",
    "Will this affect my payout for that session?",
    "Should I tell my client anything in the meantime?",
    "Can you keep this ticket open until it's confirmed?",
    "Do I need to resubmit anything, or will you handle it?",
    "Is this happening to other providers too?",
]
AGENT_LOGISTICS_A = [
    "These typically resolve within 3–5 business days; I'll keep the ticket open and update you.",
    "Nothing needed on your end — I've taken care of it and will confirm once it's processed.",
    "It won't change your payout as long as the invoice is paid on time; I'll monitor it.",
    "You can let your client know we're reviewing it and will follow up directly if needed.",
    "Absolutely — I'll keep this open and ping you the moment it's confirmed.",
    "I'll handle the resubmission on my side; you won't need to touch anything.",
    "We have seen a few similar reports this week and the team is aware.",
]
AGENT_CLOSE = [
    "Is there anything else I can help with today?",
    "Let me know if any other questions come up — happy to help.",
    "I'll follow up here once it's confirmed. Anything else in the meantime?",
    "Thanks for your patience on this — reach out anytime.",
    "I've documented everything on the ticket. Anything else I can do?",
]
PROVIDER_CLOSE = [
    "Thank you, that's really helpful.",
    "Appreciate the quick response — that clears it up.",
    "Thanks so much for sorting this out.",
    "Great, thank you. I'll keep an eye out for the update.",
    "That makes sense, thanks for explaining.",
    "Perfect, thanks for your help.",
]
NEEDLE_CANCEL_LINES = [
    "Honestly, with this Aetna cut I'm seriously considering cancelling my Alma membership and going fully out-of-network.",
    "If 90837 really drops to the 90834 rate, I can't make the numbers work — I may need to cancel my Alma account.",
    "I've been with Alma two years but after the Aetna change I'm looking at leaving the platform entirely.",
    "Between the Aetna rate cut and the fees, I'm planning to cancel my membership before July 15.",
    "This is the last straw with the Aetna news — please tell me how to cancel my Alma membership.",
    "I'm going to drop Aetna, and if my other payers don't cover the membership cost I'll cancel Alma too.",
]
AGENT_RETENTION = [
    "I'm really sorry to hear that, and I understand the frustration. Before any decision, let me connect you with credentialing so you can see your options — including going out-of-network for Aetna only while keeping the rest of your panel on Alma.",
    "I hear you, and I don't want you to feel forced out. Let me document this for our payer relations team and walk you through what cancelling vs. dropping just Aetna would look like for your caseload.",
    "That's completely fair given the change. I can start a membership review and have retention reach out — there may be options that keep your other payers working without the Aetna hit.",
]


def _ms(rng, lst, **kw):
    return rng.choice(lst).format(**kw)


# Opener variation: a lead-in + a family-specific trailing detail multiply the
# number of distinct openers per scenario so high-volume / low-entropy TRCs
# (e.g. add/remove-payer) don't read as copy-paste.
OPENER_LEADIN = ["", "", "", "Hi — ", "Hoping you can help. ", "Quick question — ",
                 "I'm a bit stuck. ", "Following up on this. ", "Hey, ", "Apologies if this is the wrong queue, but "]
DETAILS = {
    "credentialing": ["I have a few clients waiting on this.", "It's been pending longer than I expected.",
                      "I don't want to lose these clients to another provider.", "Let me know what documents you need from me.",
                      "I'm trying to plan my panel for next quarter.", "This is time-sensitive for me."],
    "claims": ["This is the third one this week with the same issue.", "I'd like to resolve it before the timely-filing window closes.",
               "The client keeps asking me about it.", "I've never run into this before.", "It's holding up my payout."],
    "eligibility": ["She's understandably upset about it.", "I want to give her an accurate answer.",
                    "This keeps happening with this plan.", "I'd rather sort it before her next session.", "Thanks for helping me explain it."],
    "invoicing": ["I want to make sure she's not overcharged.", "She's a long-term client and I don't want friction.",
                  "It's the second time this has come up.", "Just want to get it cleaned up.", "Let me know if you need the date of service."],
    "eap": ["Her sessions are scheduled and I don't want a gap in care.", "This EAP has been slow to sync before.",
            "I want to bill correctly the first time.", "The client is mid-treatment.", "Appreciate you looking into it."],
    "payout": ["I rely on this income and need to understand it.", "It's a meaningful amount for my practice.",
               "I keep careful records, so the gap stood out.", "I'd appreciate a clear breakdown."],
    "access": ["I have sessions today and need this working.", "It's been happening since this morning.",
               "I've already tried the usual fixes.", "This is really disrupting my day.", "Let me know if it's on my end."],
    "practice_mgmt": ["It's affecting my whole schedule.", "I noticed it after the last update.",
                      "I'd love a workaround in the meantime.", "A few clients have been affected.", "Happy to share a screenshot."],
    "practice_growth": ["I'm trying to grow my caseload.", "I want to make sure new clients can find me.",
                        "It's been quiet lately and I'm not sure why.", "Appreciate any tips to improve it."],
    "member_services": ["Just want to make sure I do this right.", "Trying to plan ahead.",
                        "Let me know if you need anything from me.", "Thanks for the help."],
    "tax": ["I need this sorted before I file.", "My accountant is asking for it.",
            "I want to make sure my records are accurate.", "It's tax season so I'd appreciate a quick turnaround."],
    "cancellation": ["I've thought about this for a while.", "I wanted to be upfront about the reason.",
                     "Happy to share more feedback if it helps.", "I appreciate the support I've gotten over the years."],
    "third_party": ["Wanted to flag it to be safe.", "Not sure if this is normal.", "Thanks for keeping an eye on it."],
    "aetna_incident": ["A lot of my colleagues are talking about this.", "I need to plan before July 15.",
                       "This really affects my bottom line.", "I want to understand all my options first."],
    "catchall": ["Thanks in advance.", "Appreciate any help.", "Let me know what you need from me.", "No rush, just curious."],
}
_DEFAULT_DETAILS = ["Thanks in advance.", "Appreciate any help.", "Let me know what you need from me."]


def _vary(ctx, text):
    rng = ctx.rng
    lead = rng.choice(OPENER_LEADIN)
    tail = ""
    if rng.random() < 0.7:
        tail = " " + rng.choice(DETAILS.get(ctx.family, _DEFAULT_DETAILS))
    return f"{lead}{text}{tail}"


def _dos(ctx):
    """A plausible recent date-of-service (M/D) within ~3 weeks before the ticket."""
    rng = ctx.rng
    dd = ctx.week_start - timedelta(days=rng.randint(2, 20))
    ctx.extra["session_date"] = dd.isoformat()
    return f"{dd.month}/{dd.day}"


def _ack(ctx):
    return _ms(ctx.rng, AGENT_ACK, pf=ctx.provider_first)


# ==========================================================================
# Family builders -> list[(role, text)]   (scenarios are matched opener->resp)
# ==========================================================================
def b_aetna(ctx):
    rng = ctx.rng
    cpt = rng.choice(["90837", "90837", "90834", "90847"])
    pct = rng.choice([30, 40, 45, 50, 60, 35])
    ctx.extra["cpt"] = cpt
    scenarios = [
        (f"I just got Alma's email about Aetna changing reimbursement on July 15 — paying the same for a 53+ minute session as a 45-minute one, and dropping doctoral-level pay to the master's rate. About {pct}% of my caseload is Aetna. Can someone tell me what my actual new rates will be?",
         f"You've got it right: effective July 15, 2026, extended sessions ({cpt}, 53+ min) are reimbursed at the 90834 rate, and E/M visits for doctoral-level providers align to the master's rate. Alma has publicly stated we disagree and have raised it directly with Aetna. I've attached your current-vs-projected rates for {cpt}."),
        (f"Saw the announcement that Aetna is flattening rates effective July 15. I bill mostly {cpt}. Does this mean my extended sessions get cut to the shorter rate? This is a major hit.",
         f"Unfortunately yes — {cpt} isn't zeroed out, but it loses the extended-session premium and will pay at the 90834 rate as of July 15. I've put your exact current and projected amounts on the ticket so you can see the difference per session."),
        (f"Can you explain the Aetna rate change from the 5/20 notice? I'm a PsyD and it sounds like my E/M visits will be paid at the master's-level rate now.",
         "That part is accurate: doctoral-level E/M visits will be reimbursed at the master's-level rate, and complex visits at the moderate rate. Alma disagrees with the change and has formally shared that with Aetna; I've documented your concern for payer relations as well."),
        (f"With {pct}% of my clients on Aetna, if 90837 pays at the 90834 rate I need to rethink whether I can stay in-network. Is Alma negotiating this?",
         "Alma has publicly disagreed with the changes and raised them with Aetna, but the effective date is still July 15. I can pull your projected rates and, if it helps, connect you with credentialing to discuss narrowing your Aetna participation while keeping your other payers on Alma."),
        ("Got the email about Aetna's changes taking effect July 15. What are my options if the new rates don't work for my practice?",
         "Totally understandable. You have a few options: stay in-network at the new rates, go out-of-network for Aetna specifically while keeping the rest of your panel on Alma, or adjust your schedule mix. I can loop in credentialing to walk through each — you wouldn't have to leave Alma to drop Aetna."),
    ]
    op, resp = rng.choice(scenarios)
    mid = [
        f"So {cpt} drops to the 90834 rate? That's the difference between this practice working and not.",
        "And there's no premium for the longer sessions at all anymore? That changes my whole schedule.",
        "Is 90847 affected too, or just the individual codes?",
        "What are my other payers paying for the same codes? I need to compare.",
    ]
    return [
        ("provider", op),
        ("agent", f"{_ack(ctx)} {resp}"),
        ("provider", rng.choice(mid)),
        ("agent", rng.choice([
            "I've attached a side-by-side of your top codes across payers so you can compare. I'd be glad to connect you with credentialing to talk through Aetna options whenever you're ready.",
            "Here's the comparison for your top codes. 90847 isn't part of this change. Let me know if you'd like credentialing to review your Aetna participation.",
            "Sent the breakdown. Nothing changes before July 15, so you have time to decide — and I've flagged your feedback for our payer relations team.",
        ])),
    ]


def b_claims(ctx):
    rng = ctx.rng
    cpt = pick_cpt(rng)
    clm = claim_no(rng)
    amt = money(rng, 90, 240)
    ctx.extra.update(cpt=cpt, dispute_amount=amt)
    scenarios = [
        (["cpt", "does_not_match", "credentialing"],
         f"I can't submit a claim for {ctx.client_initials} on {ctx.payer_name} — the portal blocks it saying my CPT doesn't match my credentialing. I am credentialed though. What's going on?",
         f"I pulled it — the {cpt} you're billing isn't on your active credentialing roster with {ctx.payer_name} yet, which is why it's blocked. I've started the request to add that code; once it's on, you'll be able to resubmit claim {clm}."),
        (["draft", "ineligb", "eligibility_check", "eligibility"],
         f"A claim came back denied for {ctx.client_initials} ({ctx.payer_name}). Reason looks like the eligibility check tied to it expired. How do I fix and resubmit?",
         f"That's exactly it — the eligibility check attached to the draft claim expired, so it flags as ineligible. I've triggered a fresh eligibility check; once it returns active I'll reattach it to {clm} and the claim will go through."),
        (["differs", "returned", "outcome", "adjustment", "denial"],
         f"Claim {clm} returned differently than the eligibility check estimated, and now my client owes more than I told her. Can you look at why the outcome differs from the EC?",
         f"The claim returned toward the client's deductible rather than the copay the EC estimated — that's why the outcome differs. It isn't an error; it's how {ctx.payer_name} adjudicated it. I'll send a breakdown you can share with her."),
        (["prior", "authorization", "auth", "second_auth"],
         f"I'm blocked from submitting because the system says prior authorization is required for {ctx.client_initials}'s {ctx.payer_name} plan. How do I proceed?",
         f"Correct — this plan needs prior auth from {ctx.payer_name} before the claim can pay. I've attached the auth form and their behavioral-health line; once the auth is on file, reply here and I'll release {clm}."),
        (["payout_confidence", "confidence", "ineligible_for_payout"],
         f"I got a notice that claim {clm} is ineligible for Payout Confidence. Does that mean it'll be deducted from my next payout?",
         f"If documentation is deemed inadequate and the claim is denied, that amount is deducted from your next payout. The flag here was a documentation issue; if you upload a corrected note I can request a re-review before the deduction posts."),
        (["deadline", "missed", "cancel"],
         f"My claim for {ctx.client_initials} missed the portal deadline to cancel and now it's stuck. Can you help me sort out the submission?",
         f"I see it — the cancel window closed, but I can still correct and resubmit {clm} on the back end. I've done that; it should move out of the stuck state within a couple of business days."),
    ]
    op, resp = _choose(ctx, scenarios)
    return [
        ("provider", op),
        ("agent", f"{_ack(ctx)} {_ms(rng, AGENT_INFO_REQUEST)}"),
        ("provider", _ms(rng, PROVIDER_INFO_GIVE, ci=ctx.client_initials, dos=_dos(ctx), mid=ctx.member_id)),
        ("agent", resp),
    ]


def b_eligibility(ctx):
    rng = ctx.rng
    est = money(rng, 20, 45)
    actual = money(rng, 95, 210)
    ctx.extra.update(dispute_amount=actual)
    client_writes = rng.random() < 0.22
    if client_writes:
        scenarios = [
            (["payment_responsibility", "copay", "deductible", "dispute"],
             f"I'm a client of one of your providers and I'm confused — I was told my session would be a ${est} copay but Alma charged my card ${actual}. Why was I charged more than expected?",
             f"The ${est} was an estimate from the eligibility check; once {ctx.payer_name} adjudicated, the visit applied to your deductible (not yet met), so the ${actual} is the contracted rate against your deductible rather than a copay. It isn't a duplicate charge, and once your deductible is met your per-session cost should drop."),
            (["payment_responsibility", "copay", "deductible"],
             f"My EOB from {ctx.payer_name} says my copay is $0 but Alma billed me ${actual}. Can someone explain what I actually owe?",
             f"Great question. The EOB 'allowed amount' isn't what you owe — your responsibility is the portion {ctx.payer_name} applied to your deductible or coinsurance, which here is ${actual}. I've attached a plain-language breakdown that maps the EOB to your charge."),
        ]
        first = "client"
    else:
        scenarios = [
            (["payment_responsibility", "deductible", "copay", "dispute"],
             f"My client {ctx.client_initials} just got charged ${actual} by Alma for a session she thought was a ${est} copay — that's what the intake estimate showed. She's upset and I don't know what to tell her.",
             f"The intake number is an estimate from the eligibility check; once {ctx.payer_name} adjudicated, the visit applied to her deductible (not met yet), so ${actual} is the contracted rate against the deductible, not the ${est} copay. I'll send you an estimate-vs-EOB breakdown to share with her."),
            (["eligibility_status", "inn_or_oon", "ineligible", "disputes_alma_eligibility"],
             f"{ctx.client_initials} shows as ineligible in the portal but has active {ctx.payer_name} coverage — I confirmed it with the plan. Can you fix the eligibility status so I can bill?",
             f"I re-ran it — the member ID had a transposed digit on file, so the check failed. I've corrected it to {ctx.member_id} and eligibility now returns active. You should be able to bill normally."),
            (["cannot_be_completed", "incorrect_insurance", "eligibility_check", "intake"],
             f"The eligibility check won't complete for {ctx.client_initials} — it says the insurance details are incorrect, but I entered exactly what's on the {ctx.payer_name} card (member ID {ctx.member_id}).",
             f"Thanks — the issue was the group number field, which {ctx.payer_name} requires for this plan type. I've added it and the check now completes; you're clear to submit."),
            (["has_not_yet_returned", "disputes_alma_payment", "payment_responsibility"],
             f"My client is disputing Alma's payment-responsibility amount. {ctx.payer_name} hasn't even returned the claim yet — why is she being asked to pay ${actual} now?",
             f"You're right that the claim hasn't returned. I've paused her invoice until {ctx.payer_name} adjudicates so she isn't charged prematurely; once it returns we'll true-up to the actual responsibility."),
            (["re-check", "deductible_accumulations", "accumulations", "health_plan_update"],
             f"Can you re-check {ctx.client_initials}'s deductible accumulations? The amount Alma shows doesn't match what {ctx.payer_name} told her she's met.",
             f"Re-pulled it — {ctx.payer_name}'s accumulator had refreshed since the last check. Updated numbers are on the ticket; her remaining deductible is lower than the portal showed, so her responsibility going forward is less."),
        ]
        first = "provider"
    op, resp = _choose(ctx, scenarios)
    return [
        (first, op),
        ("agent", f"{_ack(ctx)} {_ms(rng, AGENT_INFO_REQUEST)}"),
        (first, _ms(rng, PROVIDER_INFO_GIVE, ci=ctx.client_initials, dos=_dos(ctx), mid=ctx.member_id)),
        ("agent", resp),
    ]


def _choose(ctx, scenarios):
    """Pick a scenario, preferring ones whose keywords match the exact TRC leaf so
    the conversation fits the specific reason code (not just the family). Each
    scenario is (kw_list, opener, response) — or (opener, response) for no keying."""
    rng = ctx.rng
    leaf = ctx.trc.lower()
    norm = [s if len(s) == 3 else ([], s[0], s[1]) for s in scenarios]
    matches = [s for s in norm if s[0] and any(k in leaf for k in s[0])]
    _, op, resp = rng.choice(matches if matches else norm)
    return op, resp


def _simple(ctx, scenarios):
    """opener -> ack+response (no info exchange)."""
    op, resp = _choose(ctx, scenarios)
    return [("provider", op), ("agent", f"{_ack(ctx)} {resp}")]


def b_invoicing(ctx):
    rng = ctx.rng
    amt = money(rng, 40, 220)
    fee = money(rng, 50, 150)
    ctx.extra.update(dispute_amount=amt)
    return _simple(ctx, [
        (["refund_cash_pay", "refund", "cash_pay"],
         f"I need to request a refund on a cash-pay invoice for {ctx.client_initials} — she was charged ${amt} but her {ctx.payer_name} coverage should have applied. Can you refund and rebill to insurance?",
         f"Done — I've refunded the ${amt} cash-pay charge and queued the session to rebill against {ctx.payer_name}; the client will see the refund in 5–7 business days."),
        (["cancellation_fee", "charge_cancellation", "unexpected_cancellation"],
         f"My client no-showed and I'd like to charge the ${fee} cancellation fee per my policy. How do I get Alma to apply it?",
         f"I've applied the ${fee} cancellation fee to the client's account per your documented no-show policy. It'll appear on her next invoice labeled as a missed-appointment fee."),
        (["adjustment", "disputes_invoice", "returned_claims", "unpaid"],
         f"{ctx.client_initials} received an invoice adjustment of ${amt} after her claim returned from {ctx.payer_name} and is disputing it. Where did the adjustment come from?",
         f"The ${amt} adjustment is the difference between the estimated and actual {ctx.payer_name} responsibility once the claim returned. I've attached the before/after line items so you and the client can see exactly what changed."),
        (["do_not_know", "does_not_know", "provider_they", "insurance_invoice"],
         f"One of my clients says she got an Alma invoice from a provider she's never seen — that's not from me. Can you look into a billing error?",
         "Thanks for flagging — that looks like a member-matching error on our side. I've pulled the invoice for review and placed a hold so she isn't pursued while we correct it."),
        (["unpaid_client_invoices", "unpaid", "overdue"],
         f"There's an unpaid client invoice for ${amt} that's overdue, but the client says she already paid. Can you check whether the payment posted?",
         f"I checked — the ${amt} payment did post on our end; the overdue flag was a sync delay. I've cleared it and the invoice now shows paid."),
        (["duplicate", "double", "twice"],
         f"My client was double-charged — ${amt} hit her card twice for the same session. Please reverse one.",
         f"Confirmed two charges for the same DOS. I've reversed the duplicate ${amt}; she'll see it returned within a few business days."),
    ])


def b_credentialing(ctx):
    rng = ctx.rng
    days = rng.choice([60, 75, 90, 120])
    return _simple(ctx, [
        (["add_or_remove", "add", "wants_to_add", "update_credentialing"],
         f"I want to add {ctx.payer_name} to my credentialing so I can see those clients in-network. How do I start and how long does it take?",
         f"I've submitted the request to add {ctx.payer_name} to your panel. Credentialing with them typically runs about {days} days from a complete file; I'll track it and update you at each milestone."),
        (["add_or_remove", "remove"],
         f"I need to remove {ctx.payer_name} from my panel — I'm no longer taking that insurance. Can you process that?",
         f"Done — I've initiated removal of {ctx.payer_name} from your active payers. Existing clients can finish current authorizations, but no new in-network bookings will route there."),
        (["cannot_locate", "locate", "status_in_alma", "status"],
         "I can't find my credentialing status anywhere in the Alma portal. Where do I check whether I'm approved?",
         f"It lives under Settings → Credentialing, but it wasn't rendering for you — I've refreshed your view. You're approved with three payers and pending with {ctx.payer_name}, about {days} days in."),
        (["caqh", "attestation"],
         "I got a denial that says my CAQH attestation lapsed. I thought Alma handled that — what do I need to re-attest?",
         f"The lapse is on CAQH's side — attestation expires every 120 days. I've sent the re-attestation link; once you confirm, {ctx.payer_name} will resume processing. Expired CAQH is the most common cause of these holds."),
        (["status_update", "status", "in_progress"],
         f"My credentialing with {ctx.payer_name} has been 'in progress' for months. Can you give me a real status update? I'm turning away clients.",
         f"I escalated to our credentialing team — {ctx.payer_name} had an outstanding request for a corrected license expiration date. I've supplied it, which should move you out of the queue within {days} days."),
        (["license", "liability", "document", "expired"],
         "My liability insurance on file expired and I think it's holding up credentialing. I uploaded the new certificate — can you confirm it's unblocked?",
         f"Confirmed — your new liability certificate is accepted and the block is cleared. {ctx.payer_name} processing has resumed."),
    ])


def b_eap(ctx):
    rng = ctx.rng
    auth = "AUTH-" + "".join(rng.choice("0123456789") for _ in range(7))
    sessions = rng.choice([6, 8, 12, 3, 5])
    pe = ctx.payer_name[:-4] if ctx.payer_name.endswith(" EAP") else ctx.payer_name
    return _simple(ctx, [
        (["not_yet_available", "benefits_not", "not_available", "not_yet"],
         f"My client's {pe} EAP benefit isn't showing in the Alma portal yet, so I can't submit the claim. The authorization is {auth}. Can you get it loaded?",
         f"EAP benefits sometimes lag syncing into the portal. I've manually loaded authorization {auth} for {ctx.payer_name}; you should be able to submit now. If it still blocks, reply and I'll push it again."),
        (["submission_blocked", "claim_submission", "blocked"],
         f"I'm blocked from submitting an EAP claim for {ctx.client_initials} — {ctx.payer_name} approved {sessions} sessions but Alma shows the benefit as unavailable.",
         f"I see the {sessions}-session approval on {ctx.payer_name}'s side, but it hadn't propagated to Alma. I've forced the sync and the benefit now shows active for {ctx.client_initials}."),
        (["second_auth", "second_authorization", "auth"],
         f"My client used all {sessions} authorized {pe} EAP sessions and needs more. How do I request a second authorization?",
         f"For a second auth, {pe} EAP needs an updated treatment summary. I've started the request and attached the form; once they approve additional sessions I'll load them for you."),
        (["care_coordination", "coordination", "rep_support"],
         f"I need to coordinate care for an EAP client with another Alma provider — {ctx.payer_name} is asking who the treating provider of record is. How do I handle that?",
         f"I've recorded you as the treating provider of record for that EAP case and notified {ctx.payer_name}, so the other provider's notes won't conflict with your claims."),
        (["denial", "expired", "reauth", "benefit_denial"],
         f"The EAP authorization from {ctx.payer_name} expired mid-treatment and now my claims are denying. Can you help me get it reauthorized?",
         f"I submitted a reauthorization request to {pe} EAP with the lapse dates. Hold the affected claims for now and I'll release them once the new auth is on file."),
    ])


def b_payout(ctx):
    rng = ctx.rng
    exp_n = rng.randint(110, 190)
    ded_n = rng.randint(30, max(40, exp_n - 60))
    got_n = exp_n - ded_n
    exp, got, ded = f"{exp_n}.00", f"{got_n}.00", f"{ded_n}.00"
    ctx.extra.update(dispute_amount=exp)
    return _simple(ctx, [
        (f"My payout this cycle was ${got} but I expected around ${exp}. Can you tell me why it's lower?",
         f"I broke it down: the ${exp} you expected minus a ${ded} deduction for a denied claim equals the ${got} you received. The denial was a documentation flag from a medical audit; upload a corrected note and I can request a re-review."),
        ("I see a deduction on my payout tied to a medical audit. What was denied, and can I dispute it?",
         f"The ${ded} deduction is from a claim {ctx.payer_name} denied after a records audit. Per Payout Confidence, denied amounts come from the next payout. I've attached the audit notes — if the documentation supports it, we can appeal."),
        (f"My payout rate for {ctx.payer_name} seems lower than what I was quoted at onboarding. Is the rate correct?",
         f"I checked your contracted rate for {ctx.payer_name} and it matches your onboarding agreement. Payouts reflect the payer's contracted amount, not billed charges — I've sent the rate sheet so you can see each code."),
        ("A claim was marked ineligible for Payout Confidence and it looks like it came out of my payout. I'd like to contest it.",
         f"You can — I've opened a Payout Confidence review for that claim. If the note meets the documentation standard, the ${ded} will be restored on your next cycle."),
        (f"I'm frustrated with my {ctx.payer_name} payout rate — it barely covers my time. Any way to renegotiate, or is this just the contracted rate?",
         f"I understand. Alma's rates follow each payer's contract, so I can't change the {ctx.payer_name} rate directly, but I've logged your feedback for payer relations and can show which of your payers reimburse highest for your top codes."),
    ])


def b_access(ctx):
    rng = ctx.rng
    return _simple(ctx, [
        (["login", "log_in", "platform_access", "reloads"],
         "I can't log into the Alma portal — I enter my password and it just reloads the login page. I've reset twice.",
         "Sorry about that — a reload loop is usually a stale session cookie. I've cleared your session server-side; please try an incognito window or clear cache and log in again."),
        (["blank", "dashboard"],
         "My dashboard is completely blank after I log in — no clients, no calendar. It was working yesterday.",
         "A blank dashboard after login is almost always cached data. I've forced a re-sync on your account; hard-refresh (Ctrl/Cmd-Shift-R) and your clients and calendar should repopulate."),
        (["two_factor", "2fa", "factor", "locked"],
         "I'm locked out by two-factor — I'm not receiving the verification code on my phone. Can you reset my 2FA?",
         "I've reset your two-factor enrollment — you'll set it up fresh on next login. If SMS keeps failing, an authenticator app is more reliable; I can walk you through it."),
        (["client_portal", "cannot_access_client", "eligible_but_cannot", "client_requirements"],
         f"My client {ctx.client_initials} is eligible but can't access the client portal to pay her invoice or book. She never got the invite.",
         f"I've resent the client portal invite to {ctx.client_initials} and confirmed her email on file is correct. She'll get a fresh link to register and pay."),
        (["setup", "onboarding", "account_setup"],
         "I'm setting up my Alma account and I'm stuck — it won't let me finish onboarding past the practice-details step.",
         "I see where it's stuck — your NPI field needs to be completed before onboarding advances. I've highlighted it on your setup page; once saved you can continue."),
        (["logout", "session", "timeout"],
         "The portal keeps logging me out every few minutes. It's impossible to chart between sessions.",
         "That points to a session-timeout setting that was too aggressive on your account. I've extended it; you shouldn't be kicked out mid-session now."),
    ])


def b_practice_mgmt(ctx):
    rng = ctx.rng
    return _simple(ctx, [
        (["availability", "not_saving", "calendar_settings"],
         "My availability settings won't save — I set my hours, hit save, and they revert. Clients are booking times I'm not open.",
         "The revert is a known issue when availability is edited from the mobile view. I've saved your correct hours from our side and flagged it to product; please edit from desktop until the fix ships."),
        (["calendar", "double", "appointment", "provider_calendar"],
         "My calendar is double-booking clients into the same slot. How do I stop it accepting overlaps?",
         "I've enabled overlap-prevention on your calendar so it will reject double-bookings. The two existing conflicts are listed on the ticket so you can rebook one."),
        (["note_assist", "note", "assist", "telehealth"],
         "The EHR note-assist tool isn't generating my progress note for telehealth sessions — it just spins.",
         "Note-assist spinning on telehealth is something engineering is actively working on. As a workaround you can generate the note from the session summary; I've credited the affected sessions and subscribed you to the fix update."),
        (["reminder", "appointment_reminder", "no_show"],
         "Appointment reminders aren't going out to my clients, so I'm getting no-shows. Can you check my reminder settings?",
         "Your reminder cadence was set to 'off,' likely from a recent settings reset. I've turned reminders back on (24h + 1h before) and sent a test to confirm delivery."),
        (["conduct", "complaint", "unexpected"],
         "I had a conduct complaint from a client come through and I'm not sure how to respond appropriately within Alma.",
         "I've routed your conduct concern to our clinical excellence team, who handle these sensitively; they'll reach out with guidance and documentation steps within one business day."),
        (["progress_note", "template", "ehr", "clinical_tools"],
         "Something's off with my progress-note templates in the EHR — fields aren't carrying over between sessions.",
         "I reproduced that and filed it with product; meanwhile I've applied the standard workaround to your account so fields persist, and I'll update you when the full fix lands."),
    ])


def b_practice_growth(ctx):
    rng = ctx.rng
    return _simple(ctx, [
        ("I'm not showing up in the Alma provider directory when I search my own specialty and state. New clients can't find me.",
         "Found it — your listing was filtered out because your accepting-new-clients toggle was off. I've turned it on and re-indexed your profile; you should appear in search within a few hours."),
        ("My directory profile lists the wrong specialties and an old photo. How do I get it corrected?",
         "I've updated your specialties to match your credentials and removed the old photo so you can upload a new one. The directory reflects changes on tonight's refresh."),
        ("I've barely gotten any client referrals through Alma in two months. Is my profile misconfigured, or is this just slow?",
         "Your profile is healthy, but two fields that drive matching — modalities and client focus — were blank. I've flagged them; completing them usually improves referral volume noticeably."),
        (f"I want to make sure I appear for {ctx.payer_name} clients searching in-network providers near me. How do I confirm I'm listed correctly?",
         f"Confirmed you're credentialed and listed for {ctx.payer_name} in your state. Your service radius was set narrow, though — widening it surfaces you to more nearby clients. Want me to adjust it?"),
        ("My profile says 'not accepting new clients' but I am — I never set that. Can you fix my availability flag?",
         "Fixed — the 'not accepting' flag was toggled during a sync. You're now shown as accepting new clients and back in active matching."),
    ])


def b_member_services(ctx):
    rng = ctx.rng
    return _simple(ctx, [
        (["pause"],
         "I'd like to pause my Alma membership for a couple of months while I'm on leave. How does that work and will I keep my credentialing?",
         "A membership pause keeps your credentialing active so you don't have to re-credential when you return; billing pauses while you're out. I've set it up — just tell me your intended return month."),
        (["restart", "reactivate"],
         "I need to restart my membership — I paused earlier this year and I'm ready to come back.",
         "Welcome back! I've reactivated your membership effective today. Your previous credentialing is intact, so you can resume booking immediately."),
        (["start_date"],
         "Can I change my membership start date? I signed up but won't see clients until next month and don't want to pay early.",
         "Done — I've moved your start date to the first of next month, so you won't be billed until then. Your onboarding tasks stay available in the meantime."),
        (["upgrade", "downgrade"],
         "I want to upgrade my plan to get the additional features. What's the difference and how do I switch?",
         "The upgraded plan adds the features you mentioned. I've outlined the cost difference on the ticket; reply 'confirm' and I'll switch you for the next cycle."),
        (["locate", "agreement", "invoice_dispute"],
         "I can't locate my membership agreement anywhere in the portal. Can you send me a copy?",
         "Sent — your membership agreement is attached as a PDF, and I've added a permanent copy under Settings → Documents."),
        (["phone", "live_support", "helpline", "live_chat"],
         "I've been trying to reach someone by phone for two days and there's no live support. I need to talk to a person about my account.",
         "I'm sorry about the wait — phone coverage has been limited this week. I'm here now and can resolve this over the ticket; what do you need help with on your account?"),
    ])


def b_tax(ctx):
    rng = ctx.rng
    return _simple(ctx, [
        ("My 2025 1099 from Alma has the wrong amount — it doesn't match what I was actually paid out. Can you issue a correction?",
         "I'm sorry about that — I've opened a 1099 correction request. Finance will reconcile your 2025 payouts and issue a corrected form, usually within 5–7 business days."),
        ("The secure link to download my 1099 expired after 48 hours and now I can't access it. Can you resend it?",
         "The download link expires after 48 hours for security. I've generated a fresh secure link to your email on file — it's valid for the next 48 hours."),
        ("I can't access my 1099 — it says my TIN or address doesn't match. I think you have an old address on file.",
         "That mismatch is from an outdated address. I've updated it to your current one; please re-verify and the 1099 should unlock. Let me know if it still blocks."),
        ("I didn't receive a 1099 at all, and I'm sure I earned over the threshold last year. Can you check whether one was issued?",
         "You did cross the $600 threshold, and the 1099 was generated but bounced from an old email. I've corrected the contact info and re-sent it securely."),
        ("My 1099 shows a TIN that isn't mine. This needs correcting before I file — what do you need from me?",
         "A TIN mismatch is serious, so I've escalated to finance. Please re-submit your W-9 via the secure link I've attached and we'll issue an amended 1099."),
    ])


def b_cancellation(ctx):
    rng = ctx.rng
    trc = ctx.trc
    leaf = ctx.leaf.lower()
    if "pricing" in trc or "value" in trc or "budget" in leaf or "cost" in leaf:
        reason = "the membership cost is outpacing what I'm getting back, especially with payer rates dropping"
    elif "product" in trc or "feature" in leaf or "usability" in leaf or "functional" in leaf:
        reason = "the platform is missing functionality I need and the workflow is clunky for how I practice"
    elif "support" in trc:
        reason = "I've had to follow up too many times and support has been hard to reach"
    elif "operational_friction" in trc or "claims" in leaf or "credentialing" in leaf or "billing" in leaf:
        reason = "the billing and claims friction has cost me too much time and money"
    elif "external" in trc or "closing" in leaf or "business_model" in leaf:
        reason = "I'm changing my practice model and won't need an insurance platform"
    else:
        reason = "it's no longer the right fit for my practice"
    return _simple(ctx, [
        (f"I've decided to cancel my Alma membership — {reason}. How do I close my account and what happens to my in-progress claims?",
         f"I'm sorry to see you go, and I appreciate you telling me why. I can process the cancellation effective at cycle end; your in-progress claims will keep being worked and paid out as they return, so none are dropped."),
        (f"Please cancel my membership at the end of this cycle. Honestly, {reason}.",
         "Understood, and thank you for the honest feedback — I've logged the reason for our team. Before I finalize, I can also walk through a pause instead of a full cancellation if you might return, but I'll respect whichever you choose."),
        (f"I want to end my Alma membership. {reason.capitalize()}. What's the offboarding process?",
         "I've started offboarding. You'll keep portal access through cycle end to download your records and 1099, and I'll confirm the close date in writing on this ticket."),
    ])


def b_third_party(ctx):
    rng = ctx.rng
    if "spam" in ctx.trc.lower():
        scenarios = [
            ("I keep getting outreach through my Alma inbox that's clearly spam — marketing solicitations, not clients. Can you filter these out?",
             "I've marked that sender as spam and tightened the filter on your inbox. If more slip through, forward them here and we'll block the pattern."),
            ("Got a suspicious message in my Alma portal asking me to verify my login on an external link. Looks like phishing.",
             "Good catch, and thank you for not clicking — Alma will never ask you to verify credentials via an external link. I've reported it to our security team and blocked the sender."),
        ]
    else:
        scenarios = [
            (f"A rep from {ctx.payer_name} is requesting clinical info to coordinate care for a shared client. How much can I share, and how do I document it through Alma?",
             f"For {ctx.payer_name} care-coordination requests, share only the minimum necessary and log the exchange under the client's record. I've attached the consent template and the steps to document it in Alma."),
            (f"{ctx.payer_name} reached out about care coordination for one of my clients. Can you help me handle the request appropriately?",
             f"I've recorded the {ctx.payer_name} outreach on the client's file and attached our care-coordination guidance so your disclosure stays within consent. Loop me in if they ask for anything beyond the summary."),
        ]
    return _simple(ctx, scenarios)


def b_catchall(ctx):
    rng = ctx.rng
    cpt = pick_cpt(rng)
    amt = money(rng, 40, 200)
    return _simple(ctx, [
        ("Quick question — where do I find my year-to-date payout summary in the portal?",
         "Your year-to-date payout summary lives under Payments → Statements; I've also attached the current export to this ticket."),
        ("Is there a way to export my client list for my own records?",
         "Yes — I've generated a client-list export (de-identified per your privacy settings) and attached it here."),
        (f"How do I add {ctx.payer_name} as an accepted insurance on my profile?",
         f"I can add {ctx.payer_name} once you're credentialed with them — I checked and you are, so I've added it to your profile."),
        ("I think there's a typo in my display name on my public profile. Who fixes that?",
         "Good catch — I've corrected the display-name typo on your public profile; it'll update on the next directory refresh."),
        (f"Is CPT {cpt} covered for telehealth under {ctx.payer_name} on my plan?",
         f"Yes — {cpt} is covered for telehealth with {ctx.payer_name} using the standard place-of-service and modifier. Details on the ticket."),
        ("Following up on a previous request I never heard back on. Can you check the status?",
         "Apologies for the delay — I found it; it had stalled in the queue, so I've pushed it forward and added an update for you."),
        (f"I was charged a ${amt} fee I don't recognize on my last statement. What is it?",
         f"That ${amt} line is your monthly membership fee, billed on your renewal date. I've itemized your statement on the ticket so you can see each charge."),
        (f"Does {ctx.payer_name} require a referral before I see a new client on their plan?",
         f"For most {ctx.payer_name} behavioral-health plans no referral is needed, but a few require one — I've checked this plan type and noted the answer on the ticket."),
        ("How do I update my W-9 / banking info for payouts?",
         "You can update banking under Settings → Payments; for the W-9 I've sent a secure link. Changes take effect on your next payout cycle."),
        ("Can I see which of my clients have an outstanding balance?",
         "Done — I've attached an outstanding-balance report by client (de-identified) so you can follow up where needed."),
        (f"A client asked for a superbill for {ctx.payer_name} reimbursement — how do I generate one?",
         "You can generate a superbill from the client's billing tab; I've also attached a sample and the steps so it's ready to send."),
        ("My session notes from last week seem to have disappeared from the chart. Can you check?",
         "They're not lost — a sync delay hid them from your view. I've restored visibility; your notes from last week are back in the chart."),
    ])


BUILDERS = {
    "aetna_incident": b_aetna, "claims": b_claims, "eligibility": b_eligibility,
    "invoicing": b_invoicing, "credentialing": b_credentialing, "eap": b_eap,
    "payout": b_payout, "access": b_access, "practice_mgmt": b_practice_mgmt,
    "practice_growth": b_practice_growth, "member_services": b_member_services,
    "tax": b_tax, "cancellation": b_cancellation, "third_party": b_third_party,
    "catchall": b_catchall,
}

SUBJECTS = {
    "aetna_incident": ["Aetna July 15 rate change — impact on my rates",
                       "Questions about the Aetna reimbursement change",
                       "Aetna rate cut — can I see my projected rates?"],
    "claims": ["Claim blocked — {topic}", "Denied claim — need to resubmit",
               "Claim submission issue", "Claim outcome differs from estimate"],
    "eligibility": ["Client charged more than expected", "Eligibility / payment responsibility question",
                    "Client shows ineligible but has active coverage", "Deductible / COB question"],
    "invoicing": ["Invoice / refund request", "Cancellation fee request",
                  "Client disputes an invoice", "Billing error on a client invoice"],
    "credentialing": ["Credentialing — {topic}", "Add/remove a payer", "Credentialing status update"],
    "eap": ["EAP benefit not showing in portal", "EAP claim blocked", "Second authorization request"],
    "payout": ["Payout lower than expected", "Payout deduction question", "Payout rate question"],
    "access": ["Can't access the portal", "Portal / login issue", "Client can't access client portal"],
    "practice_mgmt": ["Availability settings not saving", "Calendar / scheduling issue", "Clinical tools issue"],
    "practice_growth": ["Not appearing in provider directory", "Profile / directory correction", "Referral question"],
    "member_services": ["Membership change request", "Membership question", "Account / membership help"],
    "tax": ["1099 correction needed", "Can't access my 1099", "1099 question"],
    "cancellation": ["Membership cancellation request", "Cancelling my Alma membership", "Account closure"],
    "third_party": ["Spam / suspicious outreach", "Payer care-coordination request", "Third-party outreach"],
    "catchall": ["General question", "Quick question about my account", "Help request"],
}


def short_topic(topic, n=40):
    if len(topic) <= n:
        return topic
    cut = topic[:n]
    return cut[:cut.rfind(" ")] if " " in cut else cut


def build_thread(ctx):
    """Family builder + optional logistics tail + closing (+ needle injection).

    Returns (subject, turns) where turns is list[(role, text)].
    """
    rng = ctx.rng
    fam = ctx.family
    turns = BUILDERS.get(fam, b_catchall)(ctx)

    # vary the opener so high-volume / low-entropy TRCs don't read as copy-paste
    turns[0] = (turns[0][0], _vary(ctx, turns[0][1]))

    if ctx.is_needle:
        turns.append(("provider", rng.choice(NEEDLE_CANCEL_LINES)))
        turns.append(("agent", rng.choice(AGENT_RETENTION)))

    if rng.random() < 0.55:
        turns.append(("provider", _ms(rng, PROVIDER_LOGISTICS_Q)))
        turns.append(("agent", _ms(rng, AGENT_LOGISTICS_A)))

    turns.append(("provider", rng.choice(PROVIDER_CLOSE)))
    if rng.random() < 0.45:
        turns.append(("agent", _ms(rng, AGENT_CLOSE)))

    subj = rng.choice(SUBJECTS.get(fam, SUBJECTS["catchall"])).format(topic=short_topic(ctx.topic))
    return subj, turns
