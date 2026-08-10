"""Architecture guardrails for the web SPA (web/src) — enforced in CI.

These lock the security posture of every Chromium surface:

1. **No raw-DOM HTML injection.** The SPA renders untrusted text only as
   escaped React children; ``dangerouslySetInnerHTML`` / ``innerHTML`` /
   ``document.write`` / ``eval`` would reopen the XSS→bridge hole.
2. **Origin-inert.** The page must stay incapable of network or persistent
   storage (no fetch/XHR/WebSocket/beacon/*Storage/indexedDB) — the Python
   side owns ALL traffic (pure-renderer rule; also what keeps file:// safe).
3. **Sandboxed previews only.** Any ``<iframe>`` (the untrusted-HTML preview
   surface, M3+) must carry ``sandbox`` and must NOT grant ``allow-scripts``.

Scope: first-party sources in web/src. web/public/qwebchannel.js is Qt's own
transport shim (it references WebSocket for a mode we never use) and dist/ is
generated — both excluded.
"""

import re
from pathlib import Path

import pytest

WEB_SRC = Path(__file__).resolve().parent.parent / "web" / "src"

BANNED = (
    "dangerouslySetInnerHTML",
    ".innerHTML",
    "document.write",
    "eval(",
    "new Function",
    "fetch(",
    "XMLHttpRequest",
    "WebSocket",
    "sendBeacon",
    "localStorage",
    "sessionStorage",
    "indexedDB",
)


def _sources():
    files = [p for p in WEB_SRC.rglob("*")
             if p.suffix in (".js", ".jsx") and ".test." not in p.name]
    assert files, f"no SPA sources found under {WEB_SRC}"
    return files


def test_spa_sources_exist():
    names = {p.name for p in _sources()}
    assert "App.jsx" in names and "ChatApp.jsx" in names


@pytest.mark.parametrize("token", BANNED)
def test_no_banned_tokens_in_spa(token):
    offenders = []
    for path in _sources():
        text = path.read_text(encoding="utf-8")
        if token in text:
            offenders.append(str(path.relative_to(WEB_SRC)))
    assert not offenders, (
        f"{token!r} found in {offenders} — the SPA is a pure renderer: "
        "no raw-DOM HTML, no network, no storage. Route the need through "
        "the Python bridge instead.")


_IFRAME = re.compile(r"<iframe\b[^>]*>", re.IGNORECASE | re.DOTALL)


def test_every_iframe_is_sandboxed_without_scripts():
    for path in _sources():
        text = path.read_text(encoding="utf-8")
        for tag in _IFRAME.findall(text):
            rel = path.relative_to(WEB_SRC)
            assert "sandbox" in tag, (
                f"{rel}: <iframe> without a sandbox attribute — untrusted "
                "HTML previews must be sandboxed")
            assert "allow-scripts" not in tag, (
                f"{rel}: <iframe sandbox> grants allow-scripts — scripts in "
                "preview content defeat the whole isolation layer")


# ── side-effectful bridge slots must be gated (registry grows with M1+) ──
# Every bridge slot that can cause a write/publish/external call is listed
# here with the test file that proves its request-row/single-winner gating.
# The guardrail fails when a new bridge module appears without registration.
GATED_SLOT_TESTS = {
    # M1 (read-only bridge): forged-id no-op + scope validation proven there;
    # the first side-effectful slot (M2 requestReschedule) extends that file.
    "src/ui/web/calendar_bridge.py": "tests/test_calendar_bridge.py",
    # M3 (read-only): forged-id gating + the sanitize-every-preview invariant;
    # M4's publish/import slots extend that file with native-confirm proofs.
    "src/ui/web/workbench_bridge.py": "tests/test_workbench_bridge.py",
    # Home port: requestModeSwitch is the one authority-bearing slot (it
    # persists app.last_mode and rebuilds the sidebar), proven there with the
    # allowlist / single-winner / native-confirm / deferred-dispatch shape.
    "src/ui/web/home_bridge.py": "tests/test_home_bridge.py",
    # Zendesk mirror tab: delete/purge run the full destructive gate
    # (single-winner claim before the native confirm, re-verify, fail
    # closed), pull/import claim before the nested-event-loop pickers, and
    # the no-Zendesk-write guarantee is asserted structurally there.
    "src/ui/web/zendesk_bridge.py": "tests/test_zendesk_bridge.py",
    # WS-D-WEB task mirror: writes relay to the host's CAS-guarded
    # asana_writeback lanes behind current-id validation + a single-winner
    # inflight claim; openUrl/openAttachment are gated on last-pushed-vm
    # registries. Proven there, plus the AST no-Asana-write-verb fence in
    # tests/test_task_web_controller.py.
    "src/ui/web/task_bridge.py": "tests/test_task_bridge.py",
}

_KNOWN_BRIDGES = {"chat_bridge.py"}   # pre-pivot; gating reviewed 2026-06/07


def test_new_bridge_modules_are_registered():
    web_dir = Path(__file__).resolve().parent.parent / "src" / "ui" / "web"
    bridges = {p.name for p in web_dir.glob("*_bridge.py")}
    unregistered = bridges - _KNOWN_BRIDGES - {
        Path(k).name for k in GATED_SLOT_TESTS}
    assert not unregistered, (
        f"bridge module(s) {sorted(unregistered)} lack a GATED_SLOT_TESTS "
        "registration — add the request-row/single-winner test first")


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
