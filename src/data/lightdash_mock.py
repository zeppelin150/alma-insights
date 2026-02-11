"""
Alma Insights — Lightdash Mock Client
Spoofs all Lightdash API interactions for test/debug mode.
Generates realistic fake rows matching the real Lightdash column schema.
"""

import json
import random
import time
from datetime import datetime, timedelta
from typing import Tuple, Optional

from src.data.run_logger import RunLogger


# ═══════════════════════════════════
#  MOCK DATA GENERATORS
# ═══════════════════════════════════

_TRC_CODES = [
    "COB", "CLM-001", "CLM-002", "BIL-001", "BIL-002",
    "CRD-001", "PAY-001", "PAY-002", "SES-001", "PLT-001",
]

_STATUSES = ["solved", "open", "pending", "closed"]

_CUSTOMER_MESSAGES = [
    "Hi, I received an EOB showing my client's secondary insurance denied the claim.",
    "I need help with a billing discrepancy on my account.",
    "My credentialing application has been pending for two months now.",
    "The payment I received doesn't match the expected amount.",
    "I'm seeing incorrect co-pay amounts for multiple clients this month.",
    "Can someone check the COB setup for this patient?",
    "The platform keeps timing out when I try to submit claims.",
    "I need to update my practice information in the system.",
    "We're getting denials on claims that were previously approved.",
    "The estimated payment shown was different from what was charged.",
]

_AGENT_MESSAGES = [
    "I'll look into this right away. Can you provide the claim number?",
    "I've escalated this to our billing team for review.",
    "The issue has been resolved. The corrected amount will be posted within 3-5 business days.",
    "I can see the discrepancy in our system. Let me walk you through the correction.",
    "Your credentialing application has been approved. You should see the update by tomorrow.",
    "I've submitted a ticket to our platform team regarding the timeout issue.",
    "Thank you for your patience. The COB setup has been corrected.",
    "I've applied the adjustment. Please verify on your next statement.",
]

_BOT_MESSAGES = [
    "This ticket has been automatically categorized as a billing inquiry.",
    "A satisfaction survey has been sent to the requester.",
    "This ticket was merged with ticket #12345.",
    "Reminder: This ticket has been open for 5 business days.",
]

_NAMES = [
    "Sarah M.", "James K.", "Provider #2218", "Maria L.", "Dr. Chen",
    "RCM Specialist", "Billing Team", "Lisa R.", "David P.", "Support Bot",
]


def _random_timestamp(base: datetime, offset_hours: int) -> str:
    """Generate a timestamp string offset from base."""
    ts = base + timedelta(hours=offset_hours, minutes=random.randint(0, 59))
    return ts.strftime("%Y-%m-%d %H:%M:%S")


def generate_mock_rows(date_start: str, date_end: str, max_rows: int = 500) -> list:
    """
    Generate mock Lightdash API rows matching the real column schema.
    Each row represents one comment/event on a ticket.
    """
    ds = datetime.strptime(date_start, "%Y-%m-%d")
    de = datetime.strptime(date_end, "%Y-%m-%d")
    total_days = max(1, (de - ds).days)

    rows = []
    # Generate ~50-100 tickets with 3-8 comments each
    num_tickets = min(max_rows // 5, random.randint(40, 120))

    for t in range(num_tickets):
        ticket_id = str(random.randint(100000, 999999))
        trc = random.choice(_TRC_CODES)
        status = random.choice(_STATUSES)
        csat = random.choice([None, None, 1, 2, 3, 4, 5])  # Many no-response
        created = ds + timedelta(days=random.randint(0, total_days))
        subject = f"Ticket #{ticket_id}: {trc} issue"
        assignee = random.choice(_NAMES[:8])
        requester = random.choice(_NAMES[:5])

        # Resolution times
        assign_to_res = round(random.uniform(0.5, 120), 2) if status == "solved" else None
        total_res = round(assign_to_res * random.uniform(1.0, 2.5), 2) if assign_to_res else None
        first_reply = round(random.uniform(0.1, 24), 2)

        num_comments = random.randint(2, 8)

        for ci in range(num_comments):
            # Role assignment
            if ci % 3 == 0:
                role = "end-user"
                body = random.choice(_CUSTOMER_MESSAGES)
                author = requester
            elif ci % 3 == 1:
                role = "agent"
                body = random.choice(_AGENT_MESSAGES)
                author = assignee
            else:
                # Mix: sometimes bot, sometimes another customer/agent msg
                if random.random() < 0.2:
                    role = ""  # NULL = bot
                    body = random.choice(_BOT_MESSAGES)
                    author = "Support Bot"
                else:
                    role = random.choice(["end-user", "agent"])
                    body = random.choice(
                        _CUSTOMER_MESSAGES if role == "end-user" else _AGENT_MESSAGES
                    )
                    author = requester if role == "end-user" else assignee

            # Event timestamp — sometimes NULL (especially first comment)
            if ci == 0 and random.random() < 0.3:
                event_ts = None  # NULL timestamp
            elif random.random() < 0.05:
                event_ts = None  # Occasional NULL
            else:
                event_ts = _random_timestamp(created, ci * random.randint(1, 8))

            # Build row matching Lightdash flat format
            row = {
                "tickets_ticket_id": {"value": ticket_id},
                "tickets_subject": {"value": subject},
                "tickets_trc_code": {"value": trc},
                "tickets_status": {"value": status},
                "tickets_csat_score": {"value": csat},
                "tickets_created_at_day": {"value": created.strftime("%Y-%m-%d")},
                "comments_body": {"value": body},
                "comments_author_role": {"value": role},
                "comments_author_name": {"value": author},
                "ticket_update_details_created_est_raw": {"value": event_ts},
                "tickets_assignment_to_resolution_hours": {"value": assign_to_res},
                "tickets_total_resolution_hours": {"value": total_res},
                "tickets_first_reply_hours": {"value": first_reply},
            }
            rows.append(row)

            if len(rows) >= max_rows:
                return rows

    return rows


# ═══════════════════════════════════
#  MOCK HTTP CLIENT
# ═══════════════════════════════════

class MockHTTPClient:
    """
    Drop-in replacement for _DefaultHTTPClient.
    Simulates Lightdash API responses with realistic delays and data.
    Supports configurable failure modes for testing error handling.
    """

    def __init__(
        self,
        simulate_latency: bool = True,
        latency_ms: int = 200,
        fail_mode: str = "none",    # none | 429 | 500 | timeout | auth
        fail_after_pages: int = 0,   # 0 = never fail during pagination
    ):
        self.simulate_latency = simulate_latency
        self.latency_ms = latency_ms
        self.fail_mode = fail_mode
        self.fail_after_pages = fail_after_pages
        self._page_count = 0
        self._generated_data: dict = {}  # Cache per date range

    def get(self, path: str, timeout: int = 30) -> Tuple[int, any]:
        self._maybe_delay()
        self._maybe_fail()

        # /api/v1/org — org check
        if "/api/v1/org" in path:
            return 200, {"status": "ok", "results": {"organizationName": "Alma (Test)"}}

        return 200, {"status": "ok"}

    def post(self, path: str, body: dict = None, timeout: int = 30) -> Tuple[int, any]:
        self._maybe_delay()
        body = body or {}

        # /api/v1/saved/<uuid>/results — chart execution
        if "/saved/" in path and "/results" in path:
            self._page_count += 1
            if self.fail_after_pages and self._page_count > self.fail_after_pages:
                self._maybe_fail(force=True)

            return self._mock_chart_results(body)

        return 200, {"status": "ok"}

    def _mock_chart_results(self, body: dict) -> Tuple[int, dict]:
        """Generate mock chart results based on request parameters."""
        limit = body.get("limit", 500)
        page = body.get("page", 1)

        # Extract date range from filters
        date_start = "2025-12-01"
        date_end = "2026-02-10"
        filters = body.get("filters", {})
        for dim_filter in filters.get("dimensions", []):
            values = dim_filter.get("values", [])
            if len(values) >= 2:
                date_start = values[0]
                date_end = values[1]

        # Generate or retrieve cached data for this date range
        cache_key = f"{date_start}_{date_end}"
        if cache_key not in self._generated_data:
            # Generate a reasonable number of rows based on date range
            ds = datetime.strptime(date_start, "%Y-%m-%d")
            de = datetime.strptime(date_end, "%Y-%m-%d")
            days = max(1, (de - ds).days)
            total_rows = min(days * random.randint(15, 40), 5000)
            self._generated_data[cache_key] = generate_mock_rows(
                date_start, date_end, max_rows=total_rows
            )

        all_rows = self._generated_data[cache_key]

        # If limit=1 this is a probe — return totalResults
        if limit == 1:
            return 200, {
                "status": "ok",
                "results": {
                    "rows": all_rows[:1],
                    "metricQuery": {"totalResults": len(all_rows)},
                }
            }

        # Paginate
        start_idx = (page - 1) * limit
        end_idx = start_idx + limit
        page_rows = all_rows[start_idx:end_idx]

        return 200, {
            "status": "ok",
            "results": {
                "rows": page_rows,
                "metricQuery": {"totalResults": len(all_rows)},
            }
        }

    def _maybe_delay(self):
        if self.simulate_latency:
            delay = self.latency_ms / 1000 * random.uniform(0.5, 1.5)
            time.sleep(delay)

    def _maybe_fail(self, force=False):
        if self.fail_mode == "none" and not force:
            return
        if self.fail_mode == "429" or (force and self.fail_mode == "429"):
            raise _MockHTTPError(429, '{"error": "Rate limited"}')
        elif self.fail_mode == "500":
            raise _MockHTTPError(500, '{"error": "Internal server error"}')
        elif self.fail_mode == "timeout":
            raise TimeoutError("Mock: connection timed out")
        elif self.fail_mode == "auth":
            raise _MockHTTPError(401, '{"error": "Unauthorized"}')


class _MockHTTPError(Exception):
    def __init__(self, code, body):
        self.code = code
        self.body = body
        super().__init__(f"Mock HTTP {code}")


# ═══════════════════════════════════
#  MOCK PREFLIGHT (for test mode)
# ═══════════════════════════════════

def run_mock_preflight(logger: Optional[RunLogger] = None):
    """
    Run a spoofed preflight that simulates all checks with delays.
    Returns the same PreflightResult list structure as the real one.
    """
    from src.data.lightdash_client import PreflightResult

    log = logger or RunLogger(run_type="preflight")
    log.start(detail="Starting mock preflight (test mode)")
    results = []

    steps = [
        ("Token present", True, "PAT: ldpat_…TEST"),
        ("URL valid", True, "project=test-proj… chart=test-chart…"),
        ("Trusted host", True, "Host matches: alma.lightdash.cloud (mock)"),
        ("Server reachable", True, "200 OK in 45ms (mock)"),
        ("Chart access", True, "200 OK in 120ms, got 1 sample row (mock)"),
    ]

    for step_name, passed, detail in steps:
        time.sleep(0.3)  # Simulate network delay
        results.append(PreflightResult(step_name, passed, detail, duration_ms=random.randint(30, 200)))
        log.preflight(step_name.lower().replace(" ", "_"),
                      status="OK" if passed else "FAIL", detail=detail)

    log.end(status="OK", detail="Mock preflight passed: 5/5 checks")
    return results


# ═══════════════════════════════════
#  MOCK INGESTION (for test mode)
# ═══════════════════════════════════

def run_mock_ingestion(
    db, date_start: str = "2025-12-01", date_end: str = "2026-02-10",
    logger: Optional[RunLogger] = None,
    progress_callback=None,
) -> dict:
    """
    Run a spoofed ingestion using MockHTTPClient.
    Follows the exact same code path as real ingestion.
    """
    from src.data.lightdash_client import run_ingestion

    mock_client = MockHTTPClient(simulate_latency=True, latency_ms=100)

    return run_ingestion(
        pat="ldpat_mock_test_token_for_demo",
        chart_url="https://alma.lightdash.cloud/projects/00000000-0000-0000-0000-000000000000/saved/11111111-1111-1111-1111-111111111111/view",
        date_start=date_start,
        date_end=date_end,
        db=db,
        trusted_base="https://alma.lightdash.cloud",
        logger=logger,
        http_client=mock_client,
        progress_callback=progress_callback,
    )
