# AI Reports — Architecture & Flow (R1–R5 rebuild, 2026-05-06)

This is the canonical reference for the AI Reports page. It supersedes
the earlier "raw markdown blob" UX and ships the structured-output
contract, multi-bridge pipeline, grounding harness, and conversational
prompt builder targeted by the 3.24.26 build plan
([screenshots](../Build%20History%20Docs/3.24.26%20build%20plan%20ui%20screenshots/)).

## Why this rebuild

The pre-rebuild Page (Build 11.0) generated reports as a single Gemini
call returning free-form markdown. The Evidence Panel was decorative
(only ticket-link clicks routed to it), the prompt manager was a flat
editor, and there was no audit of report claims against the underlying
data. Three observable defects:

1. The pipeline emitted raw text, not structured findings — the
   evidence panel could not bind to anything.
2. Report History showed no pipeline metadata ("3-bridge pipeline · 3
   specialists" was a screenshot artefact only).
3. There was no way to know whether a report's claims (counts, entities,
   dates) actually matched the warehouse.

R1–R5 replace the pipeline boundaries to fix all three at once.

## Performance targets (per user spec)

| Metric                            | Target          | Lever                         |
|-----------------------------------|-----------------|-------------------------------|
| End-to-end report generation      | 15–30 min @ 1700 tickets | Multi-bridge specialists + convergence |
| Accuracy on underlying data       | ≥ 90 %          | `report_grounding` harness     |
| UI: empty → first finding visible | < 1 s after parse | Structured emit; canvas render lazy |

## Contract — JSON-fenced AlmaReport schema

The LLM emits **exactly one fenced JSON block** matching the
`AlmaReport` schema declared in
[`src/data/report_schema.py`](../src/data/report_schema.py):

```json
{
  "title": "Executive Summary — Q1",
  "executive_summary": "2-4 sentences for leadership.",
  "findings": [
    {
      "finding_id": "f-exec-01",
      "title": "Billing & charge discrepancies",
      "summary": "1-2 sentence finding.",
      "severity": "high",
      "confidence": 0.85,
      "supporting_ticket_ids": ["12345"],
      "evidence_chips": [
        {"label": "Tickets",   "value": "275",    "kind": "metric"},
        {"label": "Trend",     "value": "+18% WoW","kind": "trend"},
        {"label": "Top payers","value": "Cigna",  "kind": "cohort"}
      ],
      "trcs_touched": ["Billing"],
      "cohort": "Cigna, Humana",
      "body_md": "Multi-paragraph drilldown."
    }
  ]
}
```

The parser (`src/data/report_parser.py::parse_report`) extracts the
fenced block, validates with `jsonschema` when available (else a
permissive local check), and hydrates the dataclasses. **Failure modes
never raise** — they degrade to a "legacy_unparseable" fallback Report
whose `raw_markdown` carries the LLM output and whose `findings` list is
empty. The UI then renders the legacy markdown, the evidence panel goes
to its metadata view, and the grounding harness tags the report.

## Pipeline

```
AIReportPipeline.run()
  ├── Phase 1   build_data_block(db, dates, trc_filter)
  ├── Phase 1b  analyst_md + tech_summary_md (best-effort)
  ├── Phase 2   single_pass OR multi_bridge dispatch
  │     · single_pass:   one Gemini call → text
  │     · multi_bridge:  SpecialistPipeline.run()
  │                       (friction / sentiment / anomaly → convergence)
  ├── Phase 2b  parse_report(text) → Report
  ├── Phase 2c  GroundingHarness.score(report) (warn-only)
  └── Phase 3   compose markdown (legacy callers + chat context)
```

Settings (`data/settings.yaml > ai.report_pipeline`):

| Key                           | Default       | Notes                          |
|-------------------------------|---------------|--------------------------------|
| `kind`                        | `multi_bridge`| Universal default per user spec |
| `bridges`                     | `4`           | Bridge pool size               |
| `model`                       | `''` → `ai.active_model` | Specialist model      |
| `convergence_model`           | `''` → reuse model | Convergence editor model  |

## Multi-bridge specialist pipeline

`src/data/specialist_pipeline.py` fans out three parallel specialists
through the existing `ReportOrchestrator` (8.5/9.0 priority queue,
canary probes, retry budgets, stall-restart) then converges with a
single editor call.

| Specialist | Prompt                                       | Scope                                   |
|------------|----------------------------------------------|-----------------------------------------|
| friction   | `config/prompts/specialist_friction.txt`     | Friction types + cohorts                |
| sentiment  | `config/prompts/specialist_sentiment.txt`    | Per-cohort sentiment + n-gram drivers   |
| anomaly    | `config/prompts/specialist_anomaly.txt`      | Poisson/CUSUM/z-score signals           |
| converge   | `config/prompts/convergence.txt`             | Dedup → rank → drop weak claims         |

Failure modes (none raise):
- One specialist returns `[Error: …]` → its findings drop, others run.
- All specialists fail → fallback Report with `specialist_failed_all` flag.
- Convergence fails → falls back to merged-partials Report.

## Grounding harness

`src/data/report_grounding.py::GroundingHarness` audits the parsed
Report against `ticket_index` along three dimensions:

| Dimension | What it checks                                                          |
|-----------|-------------------------------------------------------------------------|
| count     | Metric chips with integer value re-run against `ticket_index` (±10 %).  |
| entity    | Cohort / source chips + `cohort` field exist in payer / TRC / provider. |
| time      | Date-like chip values fall inside `scope.date_range`.                   |

Composite per-finding score = passed_dims / dims_attempted.
Composite report score = mean(finding_scores). Below 0.75 the report
gains a `low_accuracy` flag; reports never block (warn-only per user spec).
Each failed dim adds `<finding_id>:<dim>:<detail>` to the flag list so
the evidence panel can surface the audit trail.

## UI

```
AIReportsPage (src/ui/pages/ai_reports.py — minimally edited)
  └── ReportCanvas (src/ui/widgets/report_canvas.py)
        ├── Header (title, scope chips, accuracy badge)
        ├── Executive summary box
        └── FindingCard list (src/ui/widgets/finding_card.py)
                ├── Title + SeverityBadge + confidence
                ├── Summary
                ├── EvidenceChip row
                └── Lazy body markdown drilldown

  └── EvidencePanel (src/ui/widgets/evidence_panel.py — unchanged)
        ├── show_ticket(ticket_id)   ← MarkdownViewer link click
        ├── show_finding(finding_dict) ← FindingCard click (NEW)
        └── show_metadata(meta)       ← canvas emits on report load (NEW)
```

The canvas exposes `set_markdown()` / `clear()` / `toPlainText()` for
backward compatibility with the previous `MarkdownViewer` so all
existing call sites in `ai_reports.py` (chat context, save .md, save
.html, copy) keep working unchanged.

## Persistence

Migration `migrations/026_report_findings.sql` adds 5 columns to
`analysis_reports`:

- `findings_json`     — `Report.to_json()` payload (rehydrated via `Report.from_json()`)
- `pipeline_kind`     — `single_pass` | `multi_bridge`
- `specialist_count`  — count of specialists used (multi_bridge only)
- `accuracy_score`    — composite 0.0–1.0 from grounding harness
- `cost_usd`          — total bridge spend on this run

`db.save_report()` accepts these as kwargs; legacy callers (Smart
Reports, A/B Compare, pre-026 DBs) pass nothing and the column defaults
keep them working.

`Report History` reads back via `db.get_full_report(report_id)`. The
`full_results` JSON carries `report_struct` which round-trips through
`Report.from_dict()`.

## Conversational prompt builder

`src/ui/widgets/prompt_wizard.py` is the 2-pane wizard from the 3.24.26
reference. Backed by `src/data/prompt_authoring.py::AuthoringSession`,
which is a deterministic state machine (`SCOPE → QUESTIONS → OUTPUT
→ PREVIEW → DONE`). An optional `llm_callable` hook enriches each step
with Claude (via task-routed `build_client_for_task("prompt_authoring")`);
crashes in the hook fall back to canned replies and never raise.

The final `prompt_text` embeds the AlmaReport JSON-Schema verbatim so
authored prompts work out of the box with the parser.

## Testing

Default test sweep (no env vars):

```
pytest tests/test_report_schema.py tests/test_report_parser.py \
       tests/test_report_grounding.py tests/test_specialist_pipeline.py \
       tests/test_prompt_authoring.py tests/test_ai_reports_e2e.py
```

Live-bridge mode (opt-in, slow):

```
ALMA_E2E_LIVE=1 pytest tests/test_ai_reports_e2e.py -v
```

The stub-bridge fixture in `tests/conftest_stub_bridge.py` emits a
canned JSON-fenced Report so the entire pipeline (Phase 1 → 2c →
persistence) runs offline in <1 s.

## File map (new)

| Path                                            | LOC  | Purpose |
|-------------------------------------------------|------|---------|
| `migrations/026_report_findings.sql`            | ~25  | analysis_reports schema extension |
| `src/data/report_schema.py`                     | 356  | Report/Finding dataclasses + JSON_SCHEMA |
| `src/data/report_parser.py`                     | 271  | JSON-fence extraction → Report |
| `src/data/report_grounding.py`                  | 407  | 3-dimension accuracy harness |
| `src/data/specialist_pipeline.py`               | 405  | Multi-bridge fan-out + convergence |
| `src/data/prompt_authoring.py`                  | 375  | Wizard state machine |
| `src/data/ai_report_pipeline.py`                | 412  | Rebuilt pipeline (replaces 218 LOC) |
| `src/ui/widgets/severity_badge.py`              | 94   | Colored chip |
| `src/ui/widgets/finding_card.py`                | 295  | Collapsible card |
| `src/ui/widgets/report_canvas.py`               | 390  | Structured canvas + legacy fallback |
| `src/ui/widgets/prompt_wizard.py`               | 388  | 2-pane authoring widget |
| `src/ui/pages/ai_reports_prompts_tab.py`        | 50   | Shim hosting PromptWizard (was 672 LOC) |
| `config/prompts/{specialist_*,convergence}.txt` | —    | 4 new prompt templates |
| `tests/conftest_stub_bridge.py`                 | ~210 | Stub bridge fixture |
| `tests/test_ai_reports_e2e.py`                  | ~230 | E2E with `--live` toggle |
| `tests/test_report_*` (4 files)                 | ~700 | Unit coverage |

All 11 source files are under the 800-LOC cap; the 5 canned prompt
templates were rewritten in-place for JSON output.

## Drift audit

A post-facto audit checklist lives at
`C:/Users/Chris/.claude/projects/C--alma-insights/memory/reports_drift_audit.md`.
Run on the next session ≥ 24 h after this build; it greps for the
file inventory above, runs each test sweep in groups of 3-4, and
checks the 5 wiring points in `ai_report_pipeline.py` /
`ai_reports.py` / settings.

## Known limitations

- `cost_usd` defaults to `0.0` on multi-bridge runs (we don't currently
  pull per-call cost from the orchestrator's `gemini_usage` table).
  Tracked as a follow-up — see drift audit.
- Live-mode E2E requires a real Gemini CLI install + valid OAuth; no
  mocking of the bundled bridge.
- `ai_reports.py` is still ~1660 LOC even after this rebuild (pre-existing
  bloat unrelated to R1-R5). A future refactor should split it into
  page shell + canvas-tab + voc-tab modules.
