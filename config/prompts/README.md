# config/prompts/ — LLM Prompt Templates

> 21 text templates used by the LLM pipeline. Variable placeholders replaced at runtime via Python string formatting.

## Template Index

### NLP Classification

| Template | Consumer | Purpose |
|----------|---------|---------|
| `nlp_classify.txt` | `WorkerAgent` | Ticket classification system prompt |
| `nlp_synthesize.txt` | `nlp_meta_analyzer` | Post-scan cross-TRC NLP synthesis |
| `nlp_drilldown.txt` | NLP drilldown UI | Deep-dive analysis on a specific finding |

### VOC Analysis (Voice of Customer)

| Template | Consumer | Purpose |
|----------|---------|---------|
| `voc_analysis.txt` | `VOCBuilder` | Single-TRC VOC analysis |
| `voc_analysis_batch.txt` | `VOCBuilder` | Multi-TRC batched VOC (Phase 1) |
| `voc_accumulator.txt` | `VOCBuilder` | Evidence ledger rounds (Phase 2a) |
| `voc_pattern_detector.txt` | `VOCBuilder` | Pattern detection specialist (Phase 2b) |
| `voc_novelty_scanner.txt` | `VOCBuilder` | Novelty validation specialist (Phase 2b) |
| `voc_friction_scorer.txt` | `VOCBuilder` | Friction scoring specialist (Phase 2b) |
| `voc_convergence.txt` | `VOCBuilder` | Cross-TRC convergence (Phase 3) |
| `voc_synthesis.txt` | `VOCBuilder` | Final executive synthesis |

### Reports

| Template | Consumer | Purpose |
|----------|---------|---------|
| `executive_summary.txt` | `ai_report_pipeline` | Executive report generation |
| `general_trend.txt` | `trending_engine` | Trend narrative synthesis |
| `ab_comparison.txt` | `ab_report_pipeline` | A/B dataset comparison |
| `incident_summary.txt` | `incident_engine` | Incident analysis narrative |
| `hypothesis.txt` | `prompts.py` | Hypothesis testing prompt |
| `synthesis.txt` | `prompts.py` | General Gemini synthesis |
| `sentiment_dive.txt` | `trending_topics` page | Sentiment deep-dive analysis |

### Utility

| Template | Consumer | Purpose |
|----------|---------|---------|
| `drilldown.txt` | UI drilldown panel | Ticket drilldown prompt |
| `rcm_themes.txt` | Theme extraction | RCM theme identification |
| `csv_reformat_schema.txt` | `CSVReformatter` | CSV column mapping prompt |

## See Also

- `docs/LLM_INTEGRATION.md` — Full LLM integration guide with prompt details
- `docs/AGENTS.md` — How prompts are used in the classification pipeline
