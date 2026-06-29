"""Embedded web Agent chat — a QWebEngineView UI bridged to Python via
QWebChannel (in-process; NO web server, no localhost). The Python backend
(ChatEngine, the claude/aws CLIs, MCP, redaction) is unchanged — the web layer
is a pure renderer talking to ``ChatBridge``.
"""
