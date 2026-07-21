"""Drive search evaluation harness (offline half).

``scorer`` is a pure, deterministic ranking-metrics module — no network, no
LLM, no clock. It is importable from tests and from
``scripts/run_drive_eval.py`` alike.

The corpus is authored by the OWNER on a real Google Drive, not by this repo;
see docs/DRIVE_SEARCH_EVAL.md for what makes a good evaluation corpus and what
ground-truth format to hand back.
"""
