# Taxonomy Drift Audit

The classifier writes free text into several columns of `ticket_index`.
Over time those values drift — the prompt changes, the model changes,
edge cases appear. The audit surfaces drift each month so we can keep
the canonical taxonomies (used by the chat tools) in sync with reality.

## What it does

Compares actual distinct values in the production DB against:

| Column | Source of truth |
|---|---|
| `ticket_index.friction_type` | `CANONICAL_FRICTION_TYPES` in [src/data/chat_tools/canonical_taxonomy.py](../src/data/chat_tools/canonical_taxonomy.py) |
| `ticket_index.sentiment_polarity` | `CANONICAL_SENTIMENT_POLARITY` |
| `ticket_index.anomaly_flag` | `CANONICAL_ANOMALY_FLAGS` |
| `ticket_index.trc_code` | previous-month snapshot diff |
| `nlp_ticket_classifications.sub_cluster` | previous-month snapshot diff |

For the constant-canonical columns it produces:

- **Proposed additions** — values present in data but missing from the canon
- **Suspected typos** — observed values that score ≥ 0.85 similarity (stdlib `difflib`) to a canonical value
- **Proposed retirements** — canonical values absent from the last 30 days of data

For the snapshot-diff columns it produces:

- **New arrivals** — values not in last month's snapshot
- **Departed** — values from last month's snapshot no longer observed

Output:

- `tools/audit_reports/YYYY-MM-DD.md` — human report (review this)
- `tools/audit_reports/YYYY-MM-DD.json` — machine manifest (kept indefinitely)
- `tools/audit_reports/last_snapshot.json` — overwrites monthly; baseline for next run

## Running

```bash
# Standalone CLI (default cadence, default DB)
python tools/audit_taxonomies.py

# Customize
python tools/audit_taxonomies.py --days 30 --db data/local_warehouse.db

# Opt-in pytest wrapper (used in scheduled tasks / CI)
python -m pytest tests/test_taxonomy_drift.py --run-audit
ALMA_RUN_AUDIT=1 python -m pytest tests/test_taxonomy_drift.py
```

The pytest wrapper is **skipped by default** so it doesn't add noise to
normal regression runs. It only runs with `--run-audit` or
`ALMA_RUN_AUDIT=1`. Behavior:

- ✅ pass — audit completes; suspected typos under tolerance
- ❌ fail — audit error or typos ≥ 5 (likely classifier regression)

## Cadence

Every **30 days**. Three layers reinforce the schedule (any one is enough):

1. Operator playbook (this document)
2. Optional startup-check warning if no report file is dated within 35 days
3. Optional scheduled-task entry that runs the CLI on the 1st of the month

## Review / promotion workflow

When you receive a new audit report:

### Per row

| Finding type | What to do |
|---|---|
| Proposed addition | Decide: real new category, or classifier drift? If real → promote. If drift → file an NLP-prompt bug. |
| Suspected typo | File a bug against [config/prompts/nlp_classify.txt](../config/prompts/nlp_classify.txt) — the classifier is producing a misspelling. |
| Proposed retirement | Confirm the category really is retired (not just absent for a window). Only remove if confident. |
| New arrival (TRC / sub_cluster) | Verify the new value is intentional. Most are normal — TRCs and sub-clusters are open by design. |
| Departed (TRC / sub_cluster) | Usually noise from the 30-day window. No action unless persistent. |

### Promote an addition

1. Edit [src/data/chat_tools/canonical_taxonomy.py](../src/data/chat_tools/canonical_taxonomy.py)
2. Add the value to the relevant tuple
3. Update the constant's `Last reviewed` comment to today's date
4. Run the audit again — the value should no longer appear in *Proposed additions*
5. Commit. Next month's audit uses the updated canon.

### Retire a value

1. Confirm absent for ≥ 60 days (two audits) before removing
2. Remove from the tuple in `canonical_taxonomy.py`
3. Update the `Last reviewed` date
4. Audit existing chat sessions / prompt templates that referenced the value (text search the codebase)

## Why the canon exists at all

The classifier is open — it can write any string. So why bother with a
Python-side list?

1. **Partial gating in the chat tools.** When a user query passes a
   filter value (e.g. `friction_type='Feature Broken'`), the handler
   case-coerces if possible, otherwise warns via `gate_warning` in the
   response. Without a canon, every typo silently returns 0 rows.
2. **Documentation in tool descriptions.** The MCP tool schema lists
   the 12 known friction types in its description, so Gemini self-corrects
   on its next call.
3. **Drift detection.** This audit. Without a canon the classifier
   could quietly add 3 new friction types per month and we'd never
   notice.

The canon is **soft** — open enum at runtime, advisory at the audit
layer. New values pass through; we just want to know they showed up.

## Library policy

Stdlib only. Typo detection uses `difflib.get_close_matches` (Ratcliff–
Obershelp similarity), which catches the same intuitive typo cases as
Levenshtein for short strings. The audit's worst-case workload is
~580k pair comparisons in ~15s, and runs monthly — not worth a
`python-Levenshtein` dependency. See the bug-bash 2026-04-23 thread
for the tradeoff analysis.
