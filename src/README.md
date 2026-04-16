# src/ — Application Source Tree

> All application logic for Alma Insights. No tests, no scripts, no config — just the app.

## Directory Map

| Directory | Files | LOC | Purpose | API Reference |
|-----------|-------|-----|---------|---------------|
| `agents/` | 14 | 9.7K | Agentic NLP classification pipeline: orchestrator, workers, bridge, rate limiting, batch packing | `agents/INDEX.md` |
| `data/` | 85 | 29K | Data layer: DB, analysis engines, integrations, reports, configuration | `data/INDEX.md` |
| `data/chat_tools/` | 9 | 1.5K | Chat tool dispatch, execution, logging | `data/chat_tools/INDEX.md` |
| `data/embedding/` | 7 | 614 | Local embedding model (Gemma/MiniLM), semantic search | `data/embedding/INDEX.md` |
| `data/filter_engine/` | 6 | 607 | SQL filter building for warehouse queries | `data/filter_engine/INDEX.md` |
| `data/post_nlp/` | 3 | 325 | Post-NLP trend enrichment and theme tagging | — |
| `gemini/` | 4 | 700 | Gemini CLI wrapper, prompt assembly, client factory | `gemini/INDEX.md` |
| `llm/` | 4 | 980 | Claude API client, tool definitions, model registry | `llm/INDEX.md` |
| `mcp/` | 4 | 700 | MCP servers for classification and chat tools | — |
| `services/` | 8 | 1.5K | Chat engine, session management, persistence hooks | `services/INDEX.md` |
| `ui/` | 75 | 40K | PySide6 UI: pages, widgets, dialogs, theme | `ui/INDEX.md` |
| `updater/` | 4 | 630 | Auto-update: version check, download, stage-and-apply, schema migration | `updater/INDEX.md` |
| `export/` | 2 | 116 | Google Drive report export | `export/INDEX.md` |
| `tools/` | 2 | 478 | Custom query tools | — |

## Entry Point

```python
# main.py (project root) boots the app:
from src.ui.main_window import MainWindow
```

## Key Cross-Cutting Modules

- **Connection discipline**: `data/connection_factory.py` — all DB access goes through here
- **Settings**: `data/settings_manager.py` — centralized settings API
- **LLM routing**: `gemini/client_factory.py` — task-routed provider dispatch
- **PII redaction**: `data/redaction_engine.py` — mandatory before all LLM calls

## See Also

- `CLAUDE.md` — Project-wide quick reference
- `docs/ARCHITECTURE.md` — System architecture layers
