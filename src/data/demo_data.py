"""
Alma Insights — Demo Data Generator
Produces realistic sample ticket conversations for development/demo.
"""

import random
from datetime import datetime, timedelta


TRC_CODES = [
    ("COB-001", "Coordination of Benefits — Payer Dispute"),
    ("COB-002", "Coordination of Benefits — Client Inquiry"),
    ("CLM-001", "Claim Denied — Missing Info"),
    ("CLM-002", "Claim Denied — Auth Required"),
    ("CLM-003", "Claim Processing Delay"),
    ("BIL-001", "Billing — Incorrect Co-pay"),
    ("BIL-002", "Billing — Payment Not Applied"),
    ("BIL-003", "Billing — Estimated Payment Dispute"),
    ("CRD-001", "Credentialing — Status Inquiry"),
    ("CRD-002", "Credentialing — Documentation Missing"),
    ("PAY-001", "Payment — Direct Deposit Issue"),
    ("PAY-002", "Payment — Remittance Discrepancy"),
    ("SES-001", "Session — Client No-Show Billing"),
    ("SES-002", "Session — Late Cancel Policy"),
    ("PLT-001", "Platform — Technical Error"),
    ("PLT-002", "Platform — Calendar Sync Issue"),
]

CLIENT_MESSAGES = {
    "COB-001": [
        "Hi, I received an EOB showing my client's secondary insurance denied the claim. The primary paid correctly but the COB adjudication seems wrong. Can you look into this?",
        "I've had three clients this week with the same issue — their Aetna secondary is denying everything that BCBS primary already paid. This started happening after January 1st.",
        "The client is asking me about a balance and I don't want to bill them if the insurance should be covering it. Can someone check the COB setup?",
    ],
    "CLM-001": [
        "I submitted a claim two weeks ago and it was denied for missing information. I included all the required codes — what specifically is missing?",
        "Keep getting denials on my claims for diagnosis code. I'm using F41.1 for generalized anxiety. What else do they need?",
    ],
    "BIL-001": [
        "My client was charged $45 for their co-pay but their plan says it should be $25. Can this be corrected?",
        "Multiple clients are reporting incorrect co-pay amounts this month. I think the system updated incorrectly after their plan renewed.",
    ],
    "BIL-003": [
        "The estimated payment shown to my client was $150 but they were actually charged $210. They're upset and I don't know how to explain the difference.",
        "I keep seeing estimated payments that don't match actual charges. My clients are losing trust in the billing process.",
    ],
    "CRD-001": [
        "I submitted my credentialing application 6 weeks ago. Can you give me an update on where it stands?",
        "I need to start seeing clients with UnitedHealthcare but my credentialing still shows pending. It's been 2 months.",
    ],
    "PLT-001": [
        "I'm getting an error when I try to submit my session notes. It says 'internal server error' and I've tried three different browsers.",
        "The platform has been extremely slow today. Pages take 30+ seconds to load and I've lost two session notes that didn't save.",
    ],
}

AGENT_RESPONSES = [
    "Thank you for reaching out about this. Let me look into your account and I'll have an update for you shortly.",
    "I've reviewed the claim details and I can see the issue. Let me escalate this to our claims team for resolution.",
    "I understand how frustrating this must be. I'm going to file an internal ticket to get this corrected as quickly as possible.",
    "Good news — I was able to identify the problem. The {trc_label} issue has been flagged for our billing team and should be resolved within 3-5 business days.",
    "I've updated the records on our end. You should see the correction reflected within 48 hours. Is there anything else I can help with?",
    "I've escalated this to our specialist team. They typically respond within 24 hours. I'll follow up with you once I hear back.",
]

SUBJECTS = {
    "COB": "COB issue — {client} claim denied by secondary",
    "CLM": "Claim denial — need clarification on {code}",
    "BIL": "Billing discrepancy — client charged incorrect amount",
    "CRD": "Credentialing status inquiry — {payer}",
    "PAY": "Payment issue — direct deposit not received",
    "SES": "Session billing question — no-show policy",
    "PLT": "Platform error — {issue}",
}

PAYERS = ["Aetna", "BCBS", "UnitedHealthcare", "Cigna", "Humana", "Medicaid", "Medicare", "Optum"]
NAMES = ["Sarah M.", "James T.", "Provider #4521", "Dr. Chen", "Maria L.", "Provider #3387",
         "Alex K.", "Provider #5902", "Rebecca N.", "David P.", "Provider #2218", "Lisa W."]
AGENTS = ["Support Agent", "RCM Specialist", "Claims Team", "Billing Support", "Credentialing Team"]


def generate_demo_data(db):
    """Generate ~150 realistic demo conversations."""
    base_date = datetime(2025, 11, 1)
    conversations = []

    for i in range(160):
        trc_code, trc_label = random.choice(TRC_CODES)
        prefix = trc_code.split("-")[0]
        ticket_id = f"TKT-{10000 + i}"
        created = base_date + timedelta(days=random.randint(0, 90), hours=random.randint(8, 18))
        solved = created + timedelta(days=random.randint(1, 14), hours=random.randint(1, 8))
        status = random.choice(["solved", "solved", "solved", "open", "pending"])
        if status != "solved":
            solved = None

        # Build subject
        subject = SUBJECTS.get(prefix, f"Support request — {trc_label}")
        subject = subject.format(
            client=random.choice(NAMES),
            code=f"F{random.randint(30, 99)}.{random.randint(0, 9)}",
            payer=random.choice(PAYERS),
            issue=random.choice(["session notes", "calendar sync", "page load", "login error"]),
        )

        requester = random.choice(NAMES)
        agent = random.choice(AGENTS)
        csat = random.choice([None, None, 1, 2, 3, 4, 4, 5, 5, 5])

        # Store ticket FIRST (before comments, to satisfy FK constraint)
        db.upsert_ticket({
            "ticket_id": ticket_id,
            "subject": subject,
            "trc_code": trc_code,
            "trc_label": trc_label,
            "status": status,
            "priority": random.choice(["low", "normal", "normal", "high", "urgent"]),
            "channel": random.choice(["email", "email", "chat", "phone", "web"]),
            "csat_score": csat,
            "created_at": created.isoformat(),
            "updated_at": (solved or created + timedelta(days=1)).isoformat(),
            "solved_at": solved.isoformat() if solved else None,
            "requester_name": requester,
            "requester_email": f"{requester.lower().replace(' ', '.').replace('#', '')}@provider.com",
            "assignee_name": agent,
            "group_name": "RCM Support",
            "tags": [prefix.lower(), "demo"],
            "custom_fields": {},
        })

        # Build conversation thread and insert comments
        client_msgs = CLIENT_MESSAGES.get(trc_code, CLIENT_MESSAGES.get(
            f"{prefix}-001", ["I need help with a billing issue related to my account."]))

        thread_lines = []
        msg_count = random.randint(3, 8)
        client_count = 0
        agent_count = 0

        for m in range(msg_count):
            msg_time = created + timedelta(hours=m * random.randint(1, 12))
            if m % 2 == 0:  # Customer message
                body = random.choice(client_msgs)
                role = "customer"
                author = requester
                client_count += 1
            else:  # Agent response
                body = random.choice(AGENT_RESPONSES).format(trc_label=trc_label)
                role = "agent"
                author = agent
                agent_count += 1

            thread_lines.append(f"[{msg_time.strftime('%Y-%m-%d %H:%M')}] {role.upper()} ({author}):\n{body}")

            db.upsert_comment({
                "comment_id": f"COM-{10000 + i}-{m}",
                "ticket_id": ticket_id,
                "author_name": author,
                "author_role": role,
                "body": body,
                "is_public": True,
                "created_at": msg_time.isoformat(),
            })

        full_thread = "\n\n---\n\n".join(thread_lines)
        preview = thread_lines[0][:200] + "..." if len(thread_lines[0]) > 200 else thread_lines[0]

        # Store rebuilt conversation
        db.upsert_conversation({
            "ticket_id": ticket_id,
            "subject": subject,
            "trc_code": trc_code,
            "trc_label": trc_label,
            "status": status,
            "csat_score": csat,
            "created_at": created.isoformat(),
            "solved_at": solved.isoformat() if solved else None,
            "message_count": msg_count,
            "client_messages": client_count,
            "agent_messages": agent_count,
            "full_thread": full_thread,
            "thread_preview": preview,
        })

    db.commit()
    db.rebuild_fts_index()
