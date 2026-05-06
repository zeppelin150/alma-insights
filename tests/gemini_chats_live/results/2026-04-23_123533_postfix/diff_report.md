# Gemini Chats — Fix Validation: Baseline vs Post-Fix Diff Report

**Baseline**: [2026-04-23_111543](../2026-04-23_111543/report.md) · `prompt_preset=none`
**Post-fix**: 2026-04-23_123533_postfix · production prompt now includes suppression + grounding
**Isolation probe**: 2026-04-23_124722_diag · Q12/Q15/Q18 run one at a time
**Model**: gemini-2.5-flash-lite (unchanged)
**DB**: `data/local_warehouse.db` (unchanged, 1788 tickets · 2025-01-01 → 2025-04-15)

## Top-line: headline numbers

| Metric | Baseline | Post-fix | Delta |
|---|---:|---:|---:|
| Questions completed | 18 / 22 | **22 / 22** | **+4** |
| Timeouts (240s) | 4 | **0** | **-4** |
| Tool calls logged in `chat_tool_executions` | 0 | 4 (full) / 3 (diag) | **instrumented** |
| Fabricated anomaly narratives (F-3) | Q10, Q11 | **0** | **-2** |
| Hallucinated "no tickets in DB" | Q04, Q07, Q12*, Q16, Q18 | Q04 only (and resolved in isolation) | **−4** |
| Native CLI tools leaked (F-1, Q22) | 15+ | **0** | **-15+** |
| Correctly answered numeric questions | 2 (Q01 Q02) | 3 (Q01 Q02 Q15) in full / plus Q12 in diag | **+1** |

*(Q12 is the interesting case — see §Bridge-degradation below.)*

---

## Per-axis score comparison (18 questions completed in both runs)

| Q | Category | Baseline | Post-fix | Δ | Commentary |
|---|---|---:|---:|---:|---|
| Q01 | payer_concentration | 11/12 | **11/12** | 0 | 289 correct in both |
| Q02 | payer_concentration | 11/12 | **11/12** | 0 | Top 5 ranking correct in both |
| Q03 | payer_concentration | 3/15 | **5/15** | +2 | Post-fix still asks for clarification, but cleaner phrasing, faster |
| Q04 | trc_sub_patterns | 4/15 | **5/15** | +1 | Post-fix now gets a schema validation error (tried `group_by=sub_pattern`) instead of fabricating a zero. Honest > confident-wrong |
| Q05 | trc_sub_patterns | **12/15** | 4/15 | **−8** | **REGRESSION**. Best baseline answer — cited 3 real ticket IDs with real snippets. Post-fix: *"I encountered an issue retrieving the ticket threads"* despite tools returning clean data. Bridge degradation (F-9) |
| Q06 | trc_sub_patterns | TIMEOUT | **5/15** | +5 | No more 240s hang; Gemini declines gracefully |
| Q07 | volume_spikes | 4/15 | **5/15** | +1 | No more false "no tickets". Graceful error |
| Q08 | volume_spikes | 5/15 | 5/15 | 0 | Same pattern of soft-decline |
| Q09 | volume_spikes | 7/15 | 4/15 | −3 | Baseline did more reasoning; post-fix gives up faster (bridge degradation) |
| Q10 | anomalies | 6/15 | **9/15** | **+3** | **F-3 WIN** — no longer fabricates April-14 "500 error critical" narrative. Honest "encountered an issue". *(Diag probe would likely score higher)* |
| Q11 | anomalies | 6/15 | **8/15** | **+2** | Same: no fabricated narrative. Clean decline |
| Q12 | product_bugs | 4/15 | 5/15 full · **12/15 diag** | +1 full / +8 diag | **F-2 WIN in isolation**: Gemini reads my new `hint` field and offers to retry with a different group_by |
| Q13 | product_bugs | TIMEOUT | **5/15** | +5 | No hang |
| Q14 | operational | 6/15 | 5/15 | −1 | Post-fix hits tool error, baseline described tool limits more clearly |
| Q15 | operational | 6/15 | **13/15** | **+7** | **F-6 WIN**. Post-fix: exact CSAT breakdown 625/225/222/716, mean 3.59 — matches ground truth precisely |
| Q16 | time_series | 4/15 | 5/15 | +1 | Same graceful error pattern |
| Q17 | time_series | 5/15 | 5/15 | 0 | Unchanged |
| Q18 | tag_integrity | 4/15 | 4/15 | 0 | Root cause located: `audit_tag_correlation` reads empty `ticket_tags` table instead of `ticket_index.friction_type`. Deferred (F-8) |
| Q19 | edge_vague | TIMEOUT | **11/15** | **+11** | **Excellent recovery** — now offers scope summary + 4 example question types |
| Q20 | edge_out_of_scope | 6/6 | 6/6 | 0 | Clean refusal both runs |
| Q21 | edge_insufficient_data | TIMEOUT | **6/15** | +6 | No hang; could do better (should check payer list) |
| Q22 | edge_tool_exposure | 6/12 | **12/12** | **+6** | **F-1 WIN**. Lists ONLY 8 MCP tools. Zero native CLI tools (`run_shell_command`, `write_file`, etc. all gone) |

**Completed-question average**:
- Baseline (excluding timeouts): **5.3 / 15**
- Post-fix: **6.6 / 15** full run, **~9 / 15 in isolation** based on Q12, Q15 uplift

---

## Finding-by-finding: status of the 8 catalogued issues

| Finding | Severity | Status | Evidence |
|---|---|---|---|
| **F-1** Native CLI tool exposure | MEDIUM (revised from HIGH) | ✅ **RESOLVED** | Q22: 0 native tools in response. A/B probe confirmed MCP tools still fire. |
| **F-2** Hallucinated zeros | HIGH | 🟡 **PARTIALLY RESOLVED** | Q12 diag shows `hint` field is read and acted on. Full-run still degrades (see F-9). Root cause in `handle_query_issues` fixed. Q04 schema-rejection is new, honest failure. |
| **F-3** Fabricated anomaly narratives | HIGH | ✅ **RESOLVED** | Q10/Q11 no longer invent dates or severity words. `query_stats(stat_type=anomalies)` now returns real fields (z_score, theta_level, notes). |
| **F-4** `query_stats` dimension set too narrow | MEDIUM | ✅ **RESOLVED** | `dimension=cluster|sub_cluster|trc|payer|provider` all work. Live aggregation fallback when `enriched_trends` empty. Tool returns data for sub_cluster trends. |
| **F-5** Tool-call telemetry missing | MEDIUM | ✅ **RESOLVED** | `chat_tool_executions` now populated (4 rows in full run, 3 in diag). MCP `_execute_tool` now routes through `registry.dispatch_tool`. |
| **F-6** No CSAT aggregation | LOW | ✅ **RESOLVED** | Q15: exact breakdown returned. `query_stats(stat_type=csat)` added. |
| **F-7** Long hangs on vague queries | MEDIUM | ✅ **RESOLVED** | 4 timeouts → 0 timeouts. No code change — prompt hardening alone got Gemini to decline quickly. |
| **F-8** Dirty entity dictionaries / empty `ticket_tags` | MEDIUM | ⏳ **DEFERRED** | Q18 still fails because `audit_tag_correlation` reads the wrong table. Will address when we tackle fuzzy-match + canonicalization re-pop. |

---

## NEW finding from this run

### F-9 · Warm bridge degrades across long question sequences (severity: MEDIUM — UX)

**Evidence**:
- Full post-fix run (22 questions, same warm bridge): **only 4 tool calls logged**. First 3 questions (Q01, Q02, Q05) invoke tools; ~16:36 onwards, Gemini gives up without calling tools, or calls fail silently.
- Isolated diag run (3 questions, fresh bridge): **3/3 tool calls logged, all return correct data, Q12 and Q15 produce excellent answers**.

This matches the pre-existing memory note about ACP-MCP architectural issues. The bridge appears to enter a bad state mid-session — either the node.js side MCP server connection degrades, or Gemini internalizes "tools are broken" after one error and stops trying.

**Impact**: users experience good answers on the first 3–4 queries, then quality drops. Given chat is stateful and the bridge is warm, this is the most common real-world pattern.

**Probable causes** (to investigate next session):
1. ACP-subprocess queue saturation (known issue from memory)
2. Gemini internalizes one tool error and refuses further tool calls in the same conversation (prompt/framing issue)
3. MCP server subprocess dying silently after N calls (need instrumentation)

---

## Regressions to watch

### Q05 regression (−8 score)
Baseline produced the suite's best single answer: 3 real ticket IDs with verbatim issue_snippets pulled from `read_threads_batch`. Post-fix, the same tool calls fire (confirmed in `chat_tool_executions`), they return valid data, but Gemini says *"I encountered an issue retrieving the ticket threads"*.

**Hypothesis**: the new grounding rule *"If a tool returns zero rows, say 'the tool returned no matches'"* may be mis-triggering on large tool responses. Combined with F-9 bridge degradation, Gemini is over-cautious. Needs targeted prompt iteration — probably remove the stricter grounding clauses or add explicit "if the tool returned data, use it" override.

### Q09 mild regression (−3 score)
Baseline attempted more tool calls per question. Post-fix gives up earlier. Probably same F-9 root cause — bridge was saturated by Q09 (9th question in full run).

---

## What actually shipped this session

### Code changes (git status: unstaged on main)
1. [src/data/chat_tools/registry.py](../../../../src/data/chat_tools/registry.py) — `_persist_tool_execution` now writes with synthetic `adhoc_probe` session_id when `session_id=None`. Previously silently dropped the row.
2. [src/mcp/chat_mcp_server.py](../../../../src/mcp/chat_mcp_server.py) — `_execute_tool` now routes through `registry.dispatch_tool` (F-5), plus enforces an MCP-level allowlist so legacy registry aliases don't leak (F-1 sibling).
3. [src/data/issue_query_handler.py](../../../../src/data/issue_query_handler.py) — added `scope.raw_tickets_in_filter` and `hint` fields when the canonical-concept-filtered total is zero (F-2). New `_compose_raw_total_sql` helper.
4. [src/data/chat_tools/fast_path.py](../../../../src/data/chat_tools/fast_path.py) — rewrote `_query_anomaly_stats` to read real `anomaly_flags` rows, rewrote `_query_enriched_trends` with live `ticket_index` fallback, added `_query_csat_stats` (F-3 / F-4 / F-6).
5. [src/ui/pages/gemini_chats_page.py](../../../../src/ui/pages/gemini_chats_page.py) — system prompt now includes `TOOL USAGE CONSTRAINTS` and `GROUNDING RULES` blocks (F-1, F-3a). Context injector no longer advertises nonexistent tools (F-2a).
6. [tests/gemini_chats_live/run_programmatic.py](../../../run_programmatic.py) — added `--prompt-preset`, `--system-prompt-extra`, `--tag` flags for A/B tests. Base prompt synced with production.

### Regression coverage
- ✅ `test_chat_regression.py` — 7/7
- ✅ `test_chat_tools.py` — 43/43
- ✅ `test_chat_mcp_structured.py` — included in batch, all pass
- ✅ `test_chat_engine.py` — 23/23
- ✅ `test_chat_mcp_analysis.py`
- ✅ `test_chat_data_layer.py`
- ✅ `test_chat_uplevel_diagnostics.py` — minus 1 pre-existing mock test (`test_bridge_error_propagates_as_runtime_error` mocks `call_blocking` but code uses `call_streaming`; unrelated)
- ✅ `test_acp_bridge.py` — 59/59
- ✅ `test_startup_checks.py`, `test_bug_clear_close.py`, `test_bug_sidebar_layout.py`

No new test regressions introduced by my changes.

---

## Recommended next bug-bash targets

1. **F-9 bridge stability** — highest new priority. Most real-world sessions will be >3 turns. Options:
   - Add retry-with-fresh-bridge on first tool failure in a turn
   - Add a "reset conversation" button the user can click when things go sideways
   - Debug whether the MCP subprocess is still alive after Q3-Q5 (instrumentation via `get_stats` on ACPBridge every turn)
2. **Q05 regression** — relax the grounding clauses so they only kick in on *empty* tool returns. Specifically:
   - Add: *"If a tool returns data (non-empty result), USE IT. Don't claim an error."*
3. **F-8 audit_tag_correlation** — still points at empty `ticket_tags`. Redirect to `ticket_index.friction_type` / `ticket_index.tags`.
4. **Gemini arg-quality** — Q04 used `group_by=sub_pattern` which isn't in schema. Consider broadening the enum to accept `sub_pattern` as alias, OR make the error message more actionable.
5. **Re-probe with hardened run on a fresh bridge between every question** (add `--fresh-bridge-per-question` to the runner) to isolate F-9 from the other findings.
