"""
Alma Insights — NLP Synthesizer (Layer 3)
Final Gemini call(s) for qualia-rich narrative from NLP scan findings.
"""

import json
import logging
from pathlib import Path
from datetime import datetime

logger = logging.getLogger("alma.nlp_synthesis")

_PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "config" / "prompts"


class NLPSynthesizer:
    """Generates narrative summaries of NLP scan findings via Gemini.

    Takes structured findings from `nlp_findings` and asks Gemini to
    produce an executive-readable summary. Called after NLPMetaAnalyzer
    to layer narrative on top of the statistical aggregations.
    """

    def __init__(self, db, gemini_client):
        self.db = db
        self.gemini = gemini_client

    def synthesize_findings(self, scan_id, max_findings=10):
        """
        Takes top N findings, pulls exemplar tickets for each,
        assembles synthesis prompt, calls Gemini. Stores result
        to report_history table (existing).
        """
        # Get scan info
        scan = self.db.get_scan_status(scan_id)
        if not scan:
            raise ValueError(f"Scan {scan_id} not found")

        # Get top findings
        findings = self.db.get_scan_findings(scan_id, limit=max_findings)
        if not findings:
            return "No findings to synthesize."

        # Pull exemplar tickets
        findings_with_exemplars = []
        for f in findings:
            exemplar_ids = json.loads(f.get("exemplar_ticket_ids") or "[]")
            exemplar_text = self._pull_exemplar_tickets(exemplar_ids)
            findings_with_exemplars.append({
                "title": f["title"],
                "description": f.get("description", ""),
                "ticket_count": f.get("ticket_count", 0),
                "dominant_friction": f.get("dominant_friction_type", ""),
                "temporal_trend": f.get("temporal_trend", ""),
                "statistical_validation": f.get("statistical_validation", ""),
                "exemplar_tickets": exemplar_text,
            })

        # Assemble prompt
        prompt = self._assemble_synthesis_prompt(
            scan, findings_with_exemplars
        )

        # Call Gemini
        logger.info(f"Calling Gemini for synthesis ({len(prompt)} chars)")
        response = self.gemini.generate(prompt)

        # Store as report
        self.db.save_report(
            page="nlp_scanner",
            parameters={
                "scan_id": scan_id,
                "findings_count": len(findings),
                "date_range": f"{scan['date_range_start']} to {scan['date_range_end']}",
            },
            summary=response,
            ticket_count=scan.get("total_tickets", 0),
            report_type="nlp_synthesis",
        )

        return response

    def synthesize_single_finding(self, finding_id, user_question=None):
        """
        Deep-dive on one finding. Used by chat drilldown.
        """
        finding = self.db.conn.execute(
            "SELECT * FROM nlp_findings WHERE finding_id = ?", (finding_id,)
        ).fetchone()
        if not finding:
            raise ValueError(f"Finding {finding_id} not found")
        finding = dict(finding)

        # Pull exemplar tickets
        exemplar_ids = json.loads(finding.get("exemplar_ticket_ids") or "[]")
        exemplar_text = self._pull_exemplar_tickets(exemplar_ids)

        # Assemble drilldown prompt
        prompt = self._assemble_drilldown_prompt(
            finding, exemplar_text, user_question
        )

        # Call Gemini
        logger.info(f"Calling Gemini for drilldown ({len(prompt)} chars)")
        return self.gemini.generate(prompt)

    def _assemble_synthesis_prompt(self, scan, findings_with_exemplars):
        """Load nlp_synthesize.txt, inject findings + tickets."""
        template_path = _PROMPTS_DIR / "nlp_synthesize.txt"
        template = template_path.read_text(encoding="utf-8")

        # Format findings
        findings_text = []
        for i, f in enumerate(findings_with_exemplars, 1):
            block = f"--- Finding {i}: {f['title']} ---\n"
            block += f"Description: {f['description']}\n"
            block += f"Tickets: {f['ticket_count']} | "
            block += f"Friction: {f['dominant_friction']} | "
            block += f"Trend: {f['temporal_trend']}\n"
            if f['statistical_validation']:
                block += f"Statistical validation: {f['statistical_validation']}\n"
            if f['exemplar_tickets']:
                block += f"\nExemplar tickets:\n{f['exemplar_tickets']}\n"
            findings_text.append(block)

        prompt = template.replace("{total_tickets}",
                                  str(scan.get("total_tickets", 0)))
        prompt = prompt.replace("{date_start}",
                                scan.get("date_range_start", ""))
        prompt = prompt.replace("{date_end}",
                                scan.get("date_range_end", ""))
        prompt = prompt.replace("{findings_with_exemplars}",
                                "\n".join(findings_text))

        return prompt

    def _assemble_drilldown_prompt(self, finding, exemplar_text,
                                   user_question=None):
        """Load nlp_drilldown.txt, inject data."""
        template_path = _PROMPTS_DIR / "nlp_drilldown.txt"
        template = template_path.read_text(encoding="utf-8")

        exemplar_ids = json.loads(finding.get("exemplar_ticket_ids") or "[]")

        prompt = template.replace("{finding_title}", finding.get("title", ""))
        prompt = prompt.replace("{finding_description}",
                                finding.get("description", ""))
        prompt = prompt.replace("{statistical_validation}",
                                finding.get("statistical_validation") or "None")
        prompt = prompt.replace("{n}", str(len(exemplar_ids)))
        prompt = prompt.replace("{exemplar_tickets}", exemplar_text or "None")
        prompt = prompt.replace("{user_question}",
                                user_question or "Provide full analysis")

        return prompt

    def _pull_exemplar_tickets(self, ticket_ids):
        """
        Read full comment threads for exemplar tickets.
        Apply PII redaction. Format as JSONL.
        """
        if not ticket_ids:
            return ""

        from src.gemini.gemini_client import GeminiClient
        redactor = GeminiClient(cli_path="", pii_redaction=True)

        lines = []
        for tid in ticket_ids[:10]:
            conv = self.db.get_conversation(tid)
            if not conv:
                continue

            subject = redactor._redact_base(conv.get("subject", ""))
            subject = redactor._redact_aggressive(subject)
            thread = redactor._redact_base(conv.get("full_thread", ""))
            thread = redactor._redact_aggressive(thread)

            lines.append(json.dumps({
                "ticket_id": tid,
                "trc": conv.get("trc_code", ""),
                "created_at": conv.get("created_at", ""),
                "subject": subject,
                "csat": conv.get("csat_score"),
                "thread": thread[:2000],  # limit per-ticket size
            }))

        return "\n".join(lines)
