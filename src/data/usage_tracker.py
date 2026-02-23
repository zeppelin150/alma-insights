"""
Alma Insights -- Gemini Usage & Cost Tracker (Pass 5.1)

Centralized token/cost tracking for all Gemini API calls.
Provides aggregation queries for cost dashboard and plan utilization.

Usage:
    tracker = UsageTracker(db_manager)
    tracker.log_call('nlp_scan', tokens_in=5000, tokens_out=800, scan_id='abc')
    totals = tracker.get_daily_totals()
"""

import logging
from datetime import datetime, timedelta

logger = logging.getLogger("alma.usage_tracker")

# ── Gemini Plan Pricing (update when Google publishes new pricing) ──

GEMINI_PLANS = {
    "flash_2.5": {
        "label": "Gemini 2.5 Flash",
        "input_cost_per_1m": 0.15,
        "output_cost_per_1m": 0.60,
        "rpm": 1000,
        "rpd": 10000,
        "tpm": 1_000_000,
    },
    "flash_2.5_enterprise": {
        "label": "Gemini 2.5 Flash (Enterprise)",
        "input_cost_per_1m": 0.15,
        "output_cost_per_1m": 0.60,
        "rpm": 2000,
        "rpd": 25000,
        "tpm": 4_000_000,
    },
    "pro_2.5": {
        "label": "Gemini 2.5 Pro",
        "input_cost_per_1m": 1.25,
        "output_cost_per_1m": 10.00,
        "rpm": 360,
        "rpd": 7200,
        "tpm": 2_000_000,
    },
}


class UsageTracker:
    """Centralized Gemini token/cost tracking and aggregation."""

    def __init__(self, db_manager):
        self.db = db_manager

    # ── Token Estimation ──

    @staticmethod
    def estimate_tokens(text: str) -> int:
        """Estimate token count from text length.

        Gemini CLI/bridge does NOT return usageMetadata,
        so we estimate: ~1 token per 4 characters.
        """
        if not text:
            return 0
        return max(1, int(len(text) * 0.25))

    @staticmethod
    def estimate_cost(tokens_in: int, tokens_out: int,
                      model: str = "gemini-2.5-flash") -> float:
        """Calculate estimated cost in USD from token counts."""
        plan_key = _model_to_plan_key(model)
        plan = GEMINI_PLANS.get(plan_key, GEMINI_PLANS["flash_2.5"])
        cost = (tokens_in / 1_000_000) * plan["input_cost_per_1m"]
        cost += (tokens_out / 1_000_000) * plan["output_cost_per_1m"]
        return round(cost, 6)

    # ── Logging ──

    def log_call(self, source: str, tokens_in: int, tokens_out: int,
                 model: str = "gemini-2.5-flash", scan_id: str = None):
        """Log a Gemini API call to gemini_usage table.

        Args:
            source: 'nlp_scan', 'ai_report', 'hypothesis', 'synthesis'
            tokens_in: estimated input tokens
            tokens_out: estimated output tokens
            model: model name
            scan_id: optional scan_id for NLP scan calls
        """
        cost = self.estimate_cost(tokens_in, tokens_out, model)
        now = datetime.now()
        try:
            self.db.log_gemini_usage(
                date=now.strftime("%Y-%m-%d"),
                source=source,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                cost_usd=cost,
                scan_id=scan_id,
                model=model,
                hour=now.hour,
            )
        except Exception as e:
            logger.warning(f"Failed to log gemini usage: {e}")

    # ── Aggregation Queries ──

    def get_daily_totals(self, date: str = None) -> dict:
        """Get token/cost totals for a specific day.

        Returns: {'tokens_in': int, 'tokens_out': int, 'cost_usd': float,
                  'api_calls': int, 'tokens_total': int}
        """
        result = self.db.get_usage_totals(period="day", date=date)
        result["tokens_total"] = result["tokens_in"] + result["tokens_out"]
        return result

    def get_weekly_totals(self) -> dict:
        """Get token/cost totals for the current week (last 7 days)."""
        result = self.db.get_usage_totals(period="week")
        result["tokens_total"] = result["tokens_in"] + result["tokens_out"]
        return result

    def get_monthly_totals(self) -> dict:
        """Get token/cost totals for the current month."""
        result = self.db.get_usage_totals(period="month")
        result["tokens_total"] = result["tokens_in"] + result["tokens_out"]
        return result

    def get_period_totals(self, period_type: str = "quarterly") -> dict:
        """Get token/cost totals for the current billing period.

        Args:
            period_type: 'quarterly' or 'yearly'
        """
        now = datetime.now()
        if period_type == "yearly":
            start = now.strftime("%Y-01-01")
        else:
            # Quarterly: Jan-Mar, Apr-Jun, Jul-Sep, Oct-Dec
            quarter_start_month = ((now.month - 1) // 3) * 3 + 1
            start = f"{now.year}-{quarter_start_month:02d}-01"

        end = now.strftime("%Y-%m-%d")
        try:
            row = self.db.conn.execute("""
                SELECT COALESCE(SUM(tokens_in), 0) as tokens_in,
                       COALESCE(SUM(tokens_out), 0) as tokens_out,
                       COALESCE(SUM(cost_usd), 0.0) as cost_usd,
                       COALESCE(SUM(api_calls), 0) as api_calls
                FROM gemini_usage
                WHERE date BETWEEN ? AND ?
            """, (start, end)).fetchone()
            result = dict(row) if row else {
                "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0, "api_calls": 0
            }
        except Exception:
            result = {"tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0, "api_calls": 0}
        result["tokens_total"] = result["tokens_in"] + result["tokens_out"]
        result["period_type"] = period_type
        result["period_start"] = start
        return result

    def get_plan_utilization(self, plan_key: str = "flash_2.5") -> dict:
        """Get current usage as percentage of plan limits.

        Returns: {
            'plan_label': str,
            'tokens_today': int, 'tpm_limit': int, 'tpm_pct': float,
            'calls_today': int, 'rpm_limit': int,
            'calls_total': int, 'rpd_limit': int, 'rpd_pct': float,
        }
        """
        plan = GEMINI_PLANS.get(plan_key, GEMINI_PLANS["flash_2.5"])
        daily = self.get_daily_totals()

        return {
            "plan_label": plan["label"],
            "tokens_today": daily["tokens_total"],
            "tpm_limit": plan["tpm"],
            "tpm_pct": round(daily["tokens_total"] / plan["tpm"] * 100, 2)
                       if plan["tpm"] > 0 else 0.0,
            "calls_today": daily["api_calls"],
            "rpm_limit": plan["rpm"],
            "calls_total": daily["api_calls"],
            "rpd_limit": plan["rpd"],
            "rpd_pct": round(daily["api_calls"] / plan["rpd"] * 100, 2)
                       if plan["rpd"] > 0 else 0.0,
        }

    def get_cost_history_weekly(self, weeks: int = 12) -> list:
        """Return weekly cost data for bar chart.

        Returns: [{'week': 'YYYY-WW', 'cost_usd': float, 'api_calls': int,
                   'tokens_in': int, 'tokens_out': int}, ...]
        """
        return self.db.get_cost_history_weekly(weeks=weeks)

    def get_scan_cost(self, scan_id: str) -> dict:
        """Get total cost for a specific scan."""
        try:
            row = self.db.conn.execute("""
                SELECT COALESCE(SUM(tokens_in), 0) as tokens_in,
                       COALESCE(SUM(tokens_out), 0) as tokens_out,
                       COALESCE(SUM(cost_usd), 0.0) as cost_usd,
                       COALESCE(SUM(api_calls), 0) as api_calls
                FROM gemini_usage
                WHERE scan_id = ?
            """, (scan_id,)).fetchone()
            result = dict(row) if row else {
                "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0, "api_calls": 0
            }
        except Exception:
            result = {"tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0, "api_calls": 0}
        result["tokens_total"] = result["tokens_in"] + result["tokens_out"]
        return result


def _model_to_plan_key(model: str) -> str:
    """Map model name to plan key for pricing lookup."""
    model_lower = model.lower()
    if "pro" in model_lower:
        return "pro_2.5"
    if "enterprise" in model_lower:
        return "flash_2.5_enterprise"
    return "flash_2.5"
