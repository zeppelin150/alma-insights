"""
trc_router.py — map any :: TRC code to a content *family* + a humanised topic.

The matrix has 246 TRC rows across 22 top-level segments. Rather than author a
bespoke builder for each leaf, we route every code to one of 14 families. The
leaf string is self-describing, so we also return a humanised "topic" that the
archetype builders splice into openings / subject lines for per-leaf specificity.
"""
from __future__ import annotations

# top-level segment -> family
TOP_TO_FAMILY = {
    "client_eligibility": "eligibility",
    "client_invoicing": "invoicing",
    "invoicing": "invoicing",
    "client_billing": "invoicing",
    "provider_credentialing": "credentialing",
    "clinical_excellence": "practice_mgmt",
    "_practice_management": "practice_mgmt",
    "practice_management": "practice_mgmt",
    "alma_member_services": "member_services",
    "member_management": "member_services",
    "helpline": "member_services",
    "helpline_contact": "member_services",
    "alma_process_automation": "member_services",
    "payer_escalation": "claims",
    "client_intake": "access",
    "third_party_outreach": "third_party",
    "client_eap_benefits": "eap",
    "claims_adjustment": "claims",
    "claim_submission": "claims",
    "claims_submission": "claims",
    "provider_practice_growth": "practice_growth",
    "provider_sales": "practice_growth",
    "provider_platform_access": "access",
    "alma_portal_tools": "access",
    "provider_payment": "payout",
    "provider_payouts": "payout",
    "provider_onboarding": "access",
    "operational_friction": "aetna_incident",
    "membership_cancellation_root_cause": "cancellation",
    "no_applicable_reason_code": "catchall",
    "executive_escalation": "catchall",
    "test_ignore": "catchall",
    "internal": "catchall",
    "cx_redirect_bulk_solve": "catchall",
    "alma_outbound_comms": "catchall",
}

# Families that do NOT centre on a specific client (membership / credentialing /
# payout / cancellation / onboarding are about the provider's own account).
NO_CLIENT_FAMILIES = {"credentialing", "payout", "cancellation"}


def humanize(leaf: str) -> str:
    """`provider_cpt_code_does_not_match_credentialing` -> readable phrase."""
    txt = leaf.replace("_", " ").replace("::", " — ").strip()
    # tidy a couple of common tokens
    for a, b in (("oon", "out-of-network"), ("inn", "in-network"),
                 ("ec ", "eligibility check "), ("ais", "AIS"),
                 ("ehr", "EHR"), ("cob", "coordination of benefits"),
                 ("1099", "1099"), ("eap", "EAP"), ("cpt", "CPT")):
        txt = txt.replace(a, b)
    return txt


def route(trc: str):
    """Return (family, leaf, topic_phrase, needs_client)."""
    code = trc.lower()
    leaf = trc.split("::")[-1]
    top = trc.split("::")[0]

    # keyword overrides (order matters) ------------------------------------
    if "aetna_rate_change" in code:
        fam = "aetna_incident"
    elif "cancellation_root_cause" in code:
        fam = "cancellation"
    elif "eap" in code:
        fam = "eap"
    elif "spam" in code:
        fam = "third_party"
    elif "1099" in code or "tax_document" in code:
        fam = "tax"
    elif ("refund_cash_pay" in code or "cancellation_fee" in code
          or "unexpected_cancellation" in code or "does_not_know" in code):
        fam = "invoicing"
    elif "cpt_code_does_not_match_credentialing" in code:
        fam = "claims"
    elif "payment_responsibility_dispute" in code or "coordination_of_benefit" in code:
        fam = "eligibility"
    else:
        fam = TOP_TO_FAMILY.get(top, "catchall")

    needs_client = fam not in NO_CLIENT_FAMILIES
    # membership / phone / community / CE member-services items rarely have a client
    if fam == "member_services" and not any(
        k in code for k in ("eap", "claim", "eligibility", "invoice")
    ):
        needs_client = False
    if fam == "tax":
        needs_client = False
    return fam, leaf, humanize(leaf), needs_client
