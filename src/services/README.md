# src/services

Service layer for Alma Insights. Contains persistence hooks, context injection,
chat session management, and scheduling logic.

These modules are consumed by the UI layer and pipeline agents but do not
depend on PySide6 — they are pure Python + SQLite.
