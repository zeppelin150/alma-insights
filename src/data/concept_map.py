"""
Alma Insights — Concept Map
Maps synonymous terms to canonical concept IDs.
Used to normalize text before TF-IDF vectorization.
"""

# Key = canonical concept, Value = set of synonyms/variants
# These take priority over any automatic synonym detection.
DOMAIN_CONCEPTS = {
    "billing_charge": {
        "billed", "charged", "charge", "charges", "billing",
        "payment", "payments", "debit", "debited", "deducted",
        "auto_pay", "autopay", "recurring_charge", "recurring",
        "auto_debit",
    },
    "claim_denial": {
        "denied", "denial", "denials", "denied_claim", "claim_denial",
        "rejected", "rejection", "not_covered", "non_covered",
    },
    "cancellation_churn": {
        "cancel", "cancelled", "canceled", "cancellation",
        "terminate", "terminated", "termination",
        "leaving", "left", "churn", "client_churn", "attrition",
        "discontinue", "drop", "dropped", "quit",
    },
    "authorization": {
        "prior_authorization", "authorization", "auth", "pre_auth",
        "preauth", "approved", "approval", "authorized",
    },
    "eligibility": {
        "eligible", "eligibility", "eligibility_verification",
        "ineligible", "inactive", "coverage", "verified",
        "verification", "elig",
    },
    "timely_filing": {
        "timely_filing", "timely", "filing_deadline", "deadline",
        "late_submission", "past_deadline",
    },
    "resubmission": {
        "resubmit", "resubmission", "resubmitted", "corrected_claim",
        "corrected", "amended", "amendment", "appeal", "appealed",
    },
    "refund": {
        "refund", "refunded", "refunding", "reimbursement",
        "reimbursed", "credit", "credited", "money_back",
    },
    "payment_posting": {
        "payment_posting", "posting", "posted", "applied",
        "misapplied", "era_mismatch", "remittance",
    },
    "provider_enrollment": {
        "credentialing", "enrollment", "enrolled",
        "provider_enrollment", "paneling", "paneled",
    },
}


def build_concept_index():
    """Build reverse lookup: token → canonical concept."""
    index = {}
    for concept, synonyms in DOMAIN_CONCEPTS.items():
        for syn in synonyms:
            index[syn] = concept
    return index


def apply_concept_normalization(tokens: list, concept_index: dict) -> list:
    """Replace synonym tokens with their canonical concept ID."""
    return [concept_index.get(t, t) for t in tokens]
