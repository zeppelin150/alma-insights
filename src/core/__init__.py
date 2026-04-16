"""
Alma Insights — Core cross-cutting utilities.

Modules:
    crash_handler — Global sys.excepthook that writes redacted crash
                    reports to data/crash_reports/. Local only; no
                    telemetry upload.
"""
