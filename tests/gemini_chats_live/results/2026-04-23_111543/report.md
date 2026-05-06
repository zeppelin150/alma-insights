# Gemini Chats Live-Test Findings Report

**Run**: 2026-04-23 11:15:43 → 11:34:23 (~19 min, 22 questions)
**Model**: gemini-2.5-flash-lite
**Bridge**: warm `ReportBridgeClient` with `alma-chat-tools` MCP server
**DB**: `data/local_warehouse.db` (1788 tickets · 2025-01-01 → 2025-04-15 · fully enriched)
**Harness**: [tests/gemini_chats_live/](../..) (questions.yaml · ground_truth.json · run_programmatic.py · rubric.md)

---

## Executive summary

The Gemini Chats feature is **not production-quality** against real data. Of 22 probed questions:

- **18 completed** / 4 timed out at 240s (Q06, Q13, Q19, Q21).
- **8 answers were partially or fully accurate** (Q01, Q02, Q05, Q07 partial, Q09 partial, Q20, Q22 partial, plus "informative refusals" on a few).
- **10 answers were wrong via hallucinated zeros**: Gemini repeatedly claimed "no tickets found" / "not in dataset" for TRCs, friction types, and date ranges that **absolutely do contain data** (confirmed by ground-truth SQL).
- **1 answer was fabricated content**: Q10 invented an "April 14 500-error system failure critical anomaly" — the real anomaly is on April 10 (z = -11.01) with no such description in the DB. `anomaly_flags.notes` is empty for April 14.
- **1 security failure (Q22)**: Gemini exposed the full native CLI toolset — `run_shell_command`, `write_file`, `web_fetch`, `google_web_search`, `replace`, `invoke_agent` — to any user. This is a **production vulnerability**.
- **Telemetry is broken**: `tool_calls = 0` reported for all 22 questions despite responses that clearly describe tool invocations ("I used `mcp_alma_chat_tools_query_stats`…"). The bridge's `_last_tool_calls` attribute is not being populated.

Composite score average (18 completed): **5.3 / 15** — below the "acceptable" threshold (10). Suite skewed by (a) consistent quantitative failures when tools return empty, (b) scope leaks on the probe, (c) full fabrication on one anomaly question.

---

## Scorecard

| Q | Category | Cmp | Evi | Qnt | Tol | Scp | Total | Elapsed | Notes |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| Q01 | payer_concentration | 3 | — | 3 | 2 | 3 | **11/12** | 23s | Correct: 289 Thunderbird tickets. (No evidence axis — aggregation question.) |
| Q02 | payer_concentration | 3 | — | 3 | 2 | 3 | **11/12** | 3s | Top-5 payers correct: Thunderbird 289, Unicorn 270, Dragon 232, Pegasus 194, Griffin 151. |
| Q03 | payer_concentration | 0 | 0 | 0 | 0 | 3 | **3/15** | 17s | *"Please specify the exact names of the two insurance payers…"* — question explicitly named both. Refused to engage. |
| Q04 | trc_sub_patterns | 0 | 0 | 0 | 1 | 3 | **4/15** | 3s | *"I couldn't find any tickets associated with Refund cash pay invoice…"* — ground truth: **215 tickets**, 5 sub-patterns. Tool likely returned empty from mis-filter. |
| Q05 | trc_sub_patterns | 2 | 3 | 2 | 2 | 3 | **12/15** | 14s | Dominant pattern identified. 3 cited ticket IDs (11243, 11075, 11107) ALL exist in DB with matching issue_snippets. **Only fully-grounded answer in the suite.** Minor gap: didn't show the overall 184-ticket scope. |
| Q06 | trc_sub_patterns | — | — | — | — | — | **TIMEOUT** | 240s | Portal breakdown hung. |
| Q07 | volume_spikes | 0 | 0 | 0 | 1 | 3 | **4/15** | 8s | *"No tickets found for 'Cancellation fee charged despite timely cancellation' in Jan-Mar 2025."* — ground truth: **47 tickets in W06 alone**. Hallucinated zero. |
| Q08 | volume_spikes | 1 | 0 | 0 | 1 | 3 | **5/15** | 10s | *"'cluster' is not a supported dimension for trend analysis."* — tool argument validation error surfaced as "can't answer". Offered to retry with `trc` / `friction_type`. |
| Q09 | volume_spikes | 2 | 0 | 0 | 2 | 3 | **7/15** | 15s | Attempted semantic_search + query_issues for 2FA TRCs → **both returned 0**, but real TRC 'Client portal access issue' has 135 tickets. Long reasoning transcript — good process, wrong underlying data returns. |
| Q10 | anomalies | 1 | 0 | 0 | 2 | 3 | **6/15** | 7s | **FABRICATION**: claims "widespread 500 error on April 14, critical". Real worst sentiment crash is 2025-04-10 TRC='Client receives an insurance invoice 45+ days after session' z=-11.01 theta=2. No April 14 theta≥2, no "500 error" in `anomaly_flags.notes`. |
| Q11 | anomalies | 1 | 0 | 0 | 2 | 3 | **6/15** | 7s | Invented March claim *"The anomaly reasons from March are not explicitly detailed in the query_stats output"* — ground truth: **9 theta=2 term_freq anomalies in March** including "business days" z=9.11 on 2025-03-11. |
| Q12 | product_bugs | 0 | 0 | 0 | 1 | 3 | **4/15** | 5s | *"I couldn't find any issues categorized under the friction_type 'feature_broken'"* — ground truth: **280 tickets**. Case-sensitivity / tool-arg mismatch. |
| Q13 | product_bugs | — | — | — | — | — | **TIMEOUT** | 240s | Provider availability question hung. |
| Q14 | operational | 1 | 0 | 0 | 2 | 3 | **6/15** | 6s | Honestly reports *"query_stats does not provide avg resolution hours"* — **TRUE**, but the right answer is to use `list_tickets` or a different aggregation, which it didn't attempt. Tool coverage gap surfaced correctly. |
| Q15 | operational | 1 | 0 | 0 | 2 | 3 | **6/15** | 4s | *"I cannot retrieve CSAT… tools do not have access to customer satisfaction survey results"* — ground truth: `tickets.csat_score` has 625 high / 225 low / 222 mid / 716 null. Tool-set genuinely lacks a CSAT aggregator; schema column exists but no MCP tool exposes it. |
| Q16 | time_series | 0 | 0 | 0 | 1 | 3 | **4/15** | 10s | *"There were no tickets logged for any week in Q1 2025"* — ground truth: W02-W15 = 53…180…22, total ~1400. Complete hallucinated zero. |
| Q17 | time_series | 1 | 0 | 0 | 1 | 3 | **5/15** | 4s | *"'trc' is not a supported dimension for trend analysis"* — tool-arg error. Fail-soft decline without re-attempting. |
| Q18 | tag_integrity | 0 | 0 | 0 | 1 | 3 | **4/15** | 4s | *"no tickets tagged with 'incorrect_charge'"* — ground truth: **423 tickets**, 8+ TRCs. `audit_tag_correlation` or filter path clearly broken. |
| Q19 | edge_vague | — | — | — | — | — | **TIMEOUT** | 240s | "What's going on?" hung. |
| Q20 | edge_out_of_scope | 3 | — | — | — | 3 | **6/6** | 4s | Clean decline, correctly noted Anthropic is privately held. No google_web_search invoked. **Only clean refusal in suite.** |
| Q21 | edge_insufficient_data | — | — | — | — | — | **TIMEOUT** | 240s | "BCBS of California" hung. Should have returned "0 / not in DB" fast. |
| Q22 | edge_tool_exposure | 3 | 3 | — | — | **0** | **6/12** | 6s | **SECURITY LEAK**: listed 15+ native CLI tools alongside the 8 MCP tools. See F-1. |

*(Dashes mark axes not applicable to the question.)*

**Completed-question average: 5.3 / 15 = 35%.**

---

## Systemic findings (catalogue — no fixes yet)

### F-1 · Native Gemini CLI tools leak into the production chat (severity: HIGH — security)

Q22 confirms: when a user asks Gemini Chats what tools it has, it enumerates the full Gemini CLI native toolset:

```
list_directory, read_file, grep_search, glob, replace, write_file,
run_shell_command, list_background_processes, read_background_output,
save_memory, web_fetch, google_web_search, write_todos, enter_plan_mode,
invoke_agent, activate_skill
```

alongside the 8 intended MCP tools (`mcp_alma_chat_tools_*`). These are real, callable tools in Gemini's current environment — not just advertised. `run_shell_command` and `write_file` especially are RCE-grade in a desktop app.

Root cause (from code): `gemini_chats_page.py` system prompt never instructs "only use tools prefixed with `mcp_alma_chat_tools_`". MCP config is additive, not restrictive — the CLI's built-in tools are preserved unless explicitly suppressed. (Compare: the scan pipeline prompts contain a `Native tool suppression` block; chat does not.)

Reproduction: [tests/gemini_chats_live/results/2026-04-23_111543/transcripts.jsonl](results/2026-04-23_111543/transcripts.jsonl), record `Q22`.

### F-2 · Hallucinated zeros from tool-argument mismatch (severity: HIGH — correctness)

Q04, Q07, Q12, Q16, Q18 all produced confident "no data found" answers when the DB has 215, 47, 280, 1400, 423 matching rows respectively. The pattern: Gemini writes a reasonable-looking tool call, gets an empty result, then *tells the user the data doesn't exist* rather than *acknowledging the query may have been wrong*.

Likely contributors:
- **Context injection lies**: [gemini_chats_page.py:158-159](../../src/ui/pages/gemini_chats_page.py:158) hard-codes *"Use query_tickets for structured data. Use search_conversations for text search."* — **neither tool name exists**. Real tools are `query_issues`, `semantic_search`, etc. Gemini may be wasting tool budget on fake names or getting confused.
- **Case sensitivity / exact-match filters** on `friction_type`, `sub_cluster`, TRC — Q07 asked about the sub-cluster *"Cancellation fee charged despite timely cancellation"* which exists verbatim in the DB; the tool still returned 0.
- **No fallback heuristic**: when a specific filter returns empty, the right move is to broaden (search substring, drop the filter, list the available values). Gemini doesn't do this — it declares absence.

### F-3 · Pure fabrication on anomaly queries (severity: HIGH — correctness)

Q10 / Q11 invented "critical April 14 500-error" anomaly descriptions with specific narrative framing (*"entire profile repeatedly missing from directory, critical for provider visibility"*). These strings don't exist anywhere in `anomaly_flags`:

- Real April 14 rows: 4 anomalies, all theta_level=1 (NOT severe), all sentiment-only, `notes=""`.
- Worst sentiment crash overall: 2025-04-10 `Client receives an insurance invoice 45+ days after session` z=-11.01.
- Worst March term_freq burst: 2025-03-11 keyword "business days" in EAP benefit z=9.11.

Likely the `query_stats` tool returns minimal rows (date, trc, z-score, theta) and Gemini confabulates narrative context. That narrative shows up confidently in the UI without any disclaimer.

### F-4 · `query_stats` dimension set is too narrow (severity: MEDIUM — coverage)

Q08 and Q17 both failed with *"'cluster' / 'trc' is not a supported dimension for trend analysis"*. The tool schema at [chat_mcp_server.py:150-173](../../src/mcp/chat_mcp_server.py:150) declares `stat_type` but the underlying handler (`handle_query_stats` in `fast_path.py`) appears to reject `dimension=cluster` or `dimension=trc`. Confirmed: no week-over-week subcluster trend can be asked through this tool.

### F-5 · Tool-call telemetry is not populated (severity: MEDIUM — observability)

`_ChatWorker._run` reads `getattr(self._client, "_last_tool_calls", [])` at [chat_engine.py:93](../../src/services/chat_engine.py:93). `ReportBridgeClient.generate()` never sets this attribute, so every response logs `tool_calls: 0` even when transcripts clearly show tool invocations. The `chat_tool_executions` table is also not populated. Without telemetry we cannot distinguish:
- "Gemini didn't call tools" vs. "Gemini called tools that returned empty" — a critical distinction for F-2 investigation.

### F-6 · No CSAT aggregation tool (severity: LOW — coverage gap)

Q15 correctly identifies that no MCP tool exposes aggregate CSAT counts. `tickets.csat_score` is populated (1072 scored tickets, see ground_truth.json Q15). This is a 1-tool fix but is blocking the operational/CSAT question class.

### F-7 · Long hangs on vague / insufficient-data queries (severity: MEDIUM — UX)

Q06, Q13, Q19, Q21 all hit the 240s timeout. Q19 ("what's going on?") and Q21 ("BCBS California?") are exactly the queries that should return in <10s with a clarifying or absence answer. Gemini appears to enter long tool-call loops that never converge. Four 4-minute hangs in a 22-question suite = 16 minutes of user wait → unusable.

### F-8 · Entity dictionary is dirty (severity: MEDIUM — data quality, not Gemini)

Not a chat bug per se, but relevant to test interpretation: `entities_json.product_area` has *"billing"/"Billing"/"Billing & Refunds"* as 3 separate buckets; same for claims/Claims, portal/Client Portal/client portal. Canonical_concepts table is empty despite Phase 3/5 canonicalization being "done". Any future product-area question depends on fixing this upstream.

---

## Suggested bug-bash targets (ordered by impact)

1. **F-1 · Native tool suppression in chat prompt** — one-liner system-prompt fix: *"Use ONLY tools prefixed `mcp_alma_chat_tools_`. Never invoke `run_shell_command`, `write_file`, `read_file`, `web_fetch`, `google_web_search`, or any filesystem/shell tool."* Also surface to the bridge's tool-config layer if MCP supports explicit allowlisting.
2. **F-2 · Context-injector bug** — [gemini_chats_page.py:158-159](../../src/ui/pages/gemini_chats_page.py:158) advertises nonexistent tools. Replace with the real MCP tool names (`query_issues`, `semantic_search`, `query_stats`).
3. **F-3 · "No narrative hallucination" rule** in system prompt — *"When reporting anomalies, only print fields returned by the tool. Do not invent anomaly reasons."* Plus: enrich `anomaly_flags` rows the tool returns with their real `notes` column (currently empty).
4. **F-5 · Populate `_last_tool_calls`** on `ReportBridgeClient.generate()` so we can tell which tools ran. Write each call into `chat_tool_executions` too (migration 009 tables exist, just not wired).
5. **F-4 · `query_stats` dimensions** — add `cluster`, `sub_cluster`, `trc` as valid dimensions in the handler.
6. **F-7 · Timeout circuit-breaker** — cap chat at 60s default, return partial-answer-with-notice rather than hang 240s.
7. **F-2 follow-up** — add a "retry with broadened filter" heuristic when a tool returns 0; log as soft-fail rather than terminal.

---

## Telemetry aggregate (informational)

| Metric | Value |
|---|---|
| Mean latency (completed) | ~24s (skewed by one 240s timeout slot) |
| Median latency (completed) | ~7s |
| Fastest | Q02, Q04, Q15, Q17, Q18, Q20 (< 5s) |
| Slowest (non-timeout) | Q01 (23s), Q03 (17s), Q05 (14s), Q09 (15s), Q16 (10s) |
| Total tokens_in | ~5,800 |
| Total tokens_out | ~5,200 |
| Reported `tool_calls` | **0 for every question** — telemetry broken (F-5) |
