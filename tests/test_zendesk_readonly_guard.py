"""Permanent enforcement net for the locked Zendesk READ-ONLY policy.

OWNER POLICY (locked, product-level — not a style preference):

    The Zendesk API is ONE-WAY.  The app may only ever GET from Zendesk
    (import macros and readable content into the local mirror).  Nothing in
    this codebase may POST, PUT, PATCH or DELETE to Zendesk, and Renn (the
    AI) must have no tool that can reach a Zendesk write.  Approved content
    reaches Zendesk by an enablement specialist copying it from the local
    mirror and pasting it into the Zendesk editor by hand.

    Rationale: Guru and Asana are slim attack surfaces — production-locked
    behind domain + Zscaler restrictions, and deleted content there is
    restorable.  A Zendesk Guide instance is a PUBLIC domain and its content
    is NOT restorable the same way.  Guru and Asana write paths are
    deliberately unaffected by this policy.

This module is the structural guard.  It reads the source of every module
under ``src/`` and fails loudly if anyone ever re-adds a Zendesk write —
including via a fresh helper with a new name.  It is intentionally a
whole-tree scan rather than a test of one module: the failure mode it exists
to prevent is a *new* file, not a regression in an old one.

If a test here fails, the fix is to remove the write.  Do not relax the
guard, and do not add an allowlist entry, without an explicit new decision
from the product owner.

Read-only lanes that MUST keep working and are unaffected:
  * ticket ingestion (``fetch_incremental`` / ``fetch_view_tickets`` /
    ``fetch_ticket_fields``, product-mode Source Monitor) — GET only
  * the Help Center / macro mirror pull — GET only
  * every Guru and Asana write path — out of scope, untouched
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_SRC = _PROJECT_ROOT / "src"

_POLICY = (
    "OWNER POLICY (locked): the Zendesk API is ONE-WAY / read-only. No code "
    "may POST/PUT/PATCH/DELETE to Zendesk and Renn must have no tool that "
    "can. Content reaches Zendesk only by a human copying it into the "
    "Zendesk editor. Remove the write — do not relax this guard. "
    "(Guru/Asana writes are out of scope and unaffected.)"
)

# The Zendesk REST write methods the app used to carry, plus the ``_write``
# transport helper that carried their bodies.  These are matched as CODE
# identifiers only (see ``_code_identifiers``), never as text: the runtime
# mutator fence in ``zendesk_mirror_tools.py`` deliberately holds these names
# as string literals so it can name what it blocks, and this file names them
# in prose.  Neither is a reference.
#
# The purely local draft helpers (``update_article_draft``,
# ``update_macro_draft``, ``save_article_draft``) are different identifiers
# and are unaffected — they write to the local mirror, not to Zendesk.
_WRITE_METHODS = ("create_article", "update_article",
                  "create_macro", "update_macro")
_WRITE_TRANSPORT = ("_write",)
_FORBIDDEN_IDENTIFIERS = _WRITE_METHODS + _WRITE_TRANSPORT

# Method names that would make a client capable of changing remote state.
_WRITE_VERB_RE = re.compile(
    r"^(create|update|delete|destroy|remove|post|put|patch|push|publish"
    r"|upload|archive|restore|write|send|insert|modify|edit|set|save|sync"
    r"|import|merge|apply)_",
    re.IGNORECASE,
)

# The only write-verb-shaped names ZendeskClient may keep: they write to the
# LOCAL encrypted credential store, never to Zendesk.
_LOCAL_ONLY_CLIENT_METHODS = frozenset({
    "save_cursor", "save_credentials", "save_trc_field",
})


# ══════════════════════════════════════════════════════════════════════
#  helpers
# ══════════════════════════════════════════════════════════════════════

def _src_files() -> list[Path]:
    files = sorted(p for p in _SRC.rglob("*.py") if "__pycache__" not in p.parts)
    assert files, f"no source files found under {_SRC} — the guard scanned nothing"
    return files


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _rel(path: Path) -> str:
    return str(path.relative_to(_PROJECT_ROOT)).replace("\\", "/")


def _parse(path: Path, text: str) -> ast.Module | None:
    try:
        return ast.parse(text, filename=str(path))
    except SyntaxError:      # pragma: no cover - a broken module
        return None


def _code_identifiers(tree: ast.Module) -> dict[str, int]:
    """Every identifier used as CODE, mapped to its first line.

    Built from the AST, so identifiers that appear only inside string
    literals, docstrings or comments are correctly NOT counted as
    references.  That distinction is load-bearing: the runtime mutator fence
    names the blocked methods as strings on purpose.
    """
    found: dict[str, int] = {}

    def note(name, lineno):
        if name and name not in found:
            found[name] = lineno

    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            note(node.id, node.lineno)
        elif isinstance(node, ast.Attribute):
            note(node.attr, node.lineno)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                               ast.ClassDef)):
            note(node.name, node.lineno)
        elif isinstance(node, ast.arg):
            note(node.arg, node.lineno)
        elif isinstance(node, ast.keyword):
            note(node.arg, getattr(node.value, "lineno", 0))
        elif isinstance(node, ast.alias):
            note((node.asname or node.name or "").split(".")[-1],
                 getattr(node, "lineno", 0))
    return found


def _zendesk_modules() -> list[tuple[Path, str, ast.Module]]:
    """The modules that could plausibly talk to Zendesk over HTTP.

    In scope when the module is a Zendesk module by filename, builds a
    ``zendesk.com`` URL, or uses ``ZendeskClient`` as code.  A passing
    mention in a Guru or Asana docstring is not enough — those clients keep
    their writes, which is exactly the point of the owner's decision.
    """
    out = []
    for path in _src_files():
        text = _read(path)
        tree = _parse(path, text)
        if tree is None:
            continue
        in_scope = (
            "zendesk" in path.name.lower()
            or "zendesk.com" in text.lower()
            or "ZendeskClient" in _code_identifiers(tree)
        )
        if in_scope:
            out.append((path, text, tree))
    assert out, "no Zendesk module found — the guard is scanning the wrong tree"
    return out


def _dotted(node: ast.AST) -> str:
    """Render ``a.b.c`` from an attribute/name chain, else ''."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    elif parts:
        parts.append("<expr>")
    return ".".join(reversed(parts))


def _kwarg(call: ast.Call, name: str):
    for kw in call.keywords:
        if kw.arg == name:
            return kw.value
    return None


def _literal_str(node) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _non_get_request_sites(path: Path, tree: ast.Module) -> list[str]:
    """Every HTTP call site in ``tree`` that is not provably a GET.

    Covers ``urllib.request.Request`` (a ``data=`` body or a non-GET
    ``method=`` makes it a write), bare ``urlopen(url, data=...)`` and the
    ``requests`` library's verb helpers.
    """
    bad: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _dotted(node.func)
        where = f"{_rel(path)}:{node.lineno}"

        # ── urllib Request(...) ──
        if name == "Request" or name.endswith("request.Request"):
            method = _kwarg(node, "method")
            literal = _literal_str(method)
            if method is not None and (literal is None or literal.upper() != "GET"):
                bad.append(f"{where}  {name}(method={ast.dump(method)[:60]})")
            data = _kwarg(node, "data")
            if data is not None and not (isinstance(data, ast.Constant)
                                         and data.value is None):
                bad.append(f"{where}  {name}(data=...) — a body means a write")
            if len(node.args) > 1:
                bad.append(f"{where}  {name}(url, <positional body>)")

        # ── urlopen(url, data) ──
        elif name == "urlopen" or name.endswith("request.urlopen"):
            data = _kwarg(node, "data")
            if data is not None and not (isinstance(data, ast.Constant)
                                         and data.value is None):
                bad.append(f"{where}  urlopen(data=...) — a body means a write")
            if len(node.args) > 1:
                bad.append(f"{where}  urlopen(url, <positional body>)")

        # ── requests.<verb>(...) ──
        elif name.startswith("requests."):
            verb = name.split(".", 1)[1]
            if verb in ("post", "put", "patch", "delete"):
                bad.append(f"{where}  requests.{verb}(...)")
            elif verb == "request":
                method = _kwarg(node, "method")
                if method is None and node.args:
                    method = node.args[0]
                literal = _literal_str(method)
                if literal is None or literal.upper() != "GET":
                    bad.append(f"{where}  requests.request(<non-GET>)")
        elif name.endswith(".Session"):
            continue

    return bad


# ══════════════════════════════════════════════════════════════════════
#  1. no module may reference a Zendesk write method or transport helper
# ══════════════════════════════════════════════════════════════════════

def test_no_module_references_a_zendesk_write_method_or_transport():
    """No module anywhere in ``src/`` may USE a Zendesk write identifier.

    Scanned across the whole tree, not just the Zendesk modules: the failure
    this exists to catch is a brand-new file, not a regression in an old one.
    """
    offenders = []
    for path in _src_files():
        text = _read(path)
        tree = _parse(path, text)
        if tree is None:
            continue
        names = _code_identifiers(tree)
        for forbidden in _FORBIDDEN_IDENTIFIERS:
            if forbidden in names:
                offenders.append(
                    f"{_rel(path)}:{names[forbidden]}  {forbidden}")

    assert not offenders, (
        "Zendesk write identifier(s) reappeared as CODE in the source tree:\n  "
        + "\n  ".join(offenders)
        + "\n\n" + _POLICY
        + "\nThe local draft helpers (update_article_draft / "
          "update_macro_draft / save_article_draft) are different identifiers "
          "and stay allowed — they write the local mirror, not Zendesk."
    )


# ══════════════════════════════════════════════════════════════════════
#  2. no non-GET HTTP request may be constructed in a Zendesk module
# ══════════════════════════════════════════════════════════════════════

def test_no_zendesk_module_constructs_a_non_get_http_request():
    """Every HTTP call site in a Zendesk-capable module must be a GET.

    A request is a write if it carries a body (``data=``) or declares a
    method other than ``GET``.  Scope is deliberately the modules that could
    reach Zendesk (Zendesk filename, a ``zendesk.com`` URL, or use of
    ``ZendeskClient``) — Guru and Asana keep their writes, which is the whole
    point of the owner's decision.
    """
    offenders = []
    for path, _text, tree in _zendesk_modules():
        offenders.extend(_non_get_request_sites(path, tree))

    assert not offenders, (
        "non-GET HTTP request(s) constructed in Zendesk-capable module(s):\n  "
        + "\n  ".join(offenders)
        + "\n\n" + _POLICY
    )


def test_the_zendesk_url_builder_is_in_the_scanned_scope():
    """The scope selector must actually cover the module that builds the URL.

    Without this, a future refactor could move the base URL somewhere the
    selector does not look and the HTTP scan would pass by scanning nothing
    that matters.
    """
    scanned = {_rel(p) for p, _t, _tree in _zendesk_modules()}
    assert "src/data/zendesk_client.py" in scanned, (
        "src/data/zendesk_client.py is no longer in the Zendesk HTTP scan "
        "scope — the read-only transport guard is scanning the wrong set.\n\n"
        + _POLICY
    )
    url_builders = {_rel(p) for p in _src_files()
                    if "zendesk.com" in _read(p).lower()}
    assert url_builders <= scanned, (
        "module(s) build a zendesk.com URL but fall outside the scanned "
        "scope: " + ", ".join(sorted(url_builders - scanned)) + "\n\n" + _POLICY
    )


# ══════════════════════════════════════════════════════════════════════
#  3. ZendeskClient exposes no write-shaped public method
# ══════════════════════════════════════════════════════════════════════

def test_the_zendesk_client_exposes_no_write_shaped_public_method():
    """Introspect the live class: nothing on it may change remote state."""
    from src.data.zendesk_client import ZendeskClient

    offenders = []
    for name in dir(ZendeskClient):
        if name.startswith("_"):
            continue
        if name in _LOCAL_ONLY_CLIENT_METHODS:
            continue
        if _WRITE_VERB_RE.match(name):
            offenders.append(name)

    assert not offenders, (
        "ZendeskClient grew (a) write-shaped public method(s): "
        + ", ".join(sorted(offenders))
        + "\n\n" + _POLICY
        + "\nIf the new method only writes to the LOCAL credential store, add "
          "it to _LOCAL_ONLY_CLIENT_METHODS in this file with a comment "
          "explaining why it never reaches Zendesk."
    )


def test_the_zendesk_client_has_no_write_transport_attribute():
    """``ZendeskClient._write`` must not exist, public or private."""
    from src.data.zendesk_client import ZendeskClient

    assert not hasattr(ZendeskClient, "_write"), (
        "ZendeskClient._write is back — the POST/PUT transport helper.\n\n"
        + _POLICY
    )
    for name in _WRITE_METHODS:
        assert not hasattr(ZendeskClient, name), (
            f"ZendeskClient.{name} is back.\n\n" + _POLICY
        )


def test_the_zendesk_transport_choke_point_refuses_a_non_get():
    """The runtime backstop: asking the transport for a non-GET must raise.

    Structural scans catch code that is *written*; this catches code that
    computes a method at runtime.  ``ZendeskClient`` must funnel every
    outbound request through one builder that refuses anything but GET.
    """
    from src.data import zendesk_client as zc

    assert hasattr(zc, "ZendeskWriteBlocked"), (
        "the ZendeskWriteBlocked guard exception is gone from "
        "src/data/zendesk_client.py.\n\n" + _POLICY
    )
    client = zc.ZendeskClient("acme", "user@example.com", "token")
    builder = getattr(client, "_build_request", None)
    assert builder is not None, (
        "src/data/zendesk_client.py no longer funnels requests through a "
        "single ``_build_request`` choke point, so the runtime method guard "
        "is unenforced.\n\n" + _POLICY
    )

    # The GET path still works — read-only must not mean broken.
    req = builder("/tickets.json", method="GET")
    assert req.get_method() == "GET"
    assert req.data is None

    for method in ("POST", "PUT", "PATCH", "DELETE", "post"):
        with pytest.raises(zc.ZendeskWriteBlocked):
            builder("/help_center/en-us/articles.json", method=method)


# ══════════════════════════════════════════════════════════════════════
#  4. Renn's Zendesk tool surface exposes no write
# ══════════════════════════════════════════════════════════════════════

def _zendesk_tools() -> dict:
    """Registered chat tools that are part of the Zendesk surface."""
    from src.data.chat_tools.registry import get_tool_registry

    registry = get_tool_registry()
    picked = {}
    for name, spec in registry.items():
        handler = spec.get("handler") if isinstance(spec, dict) else None
        module = getattr(handler, "__module__", "") or ""
        if "zendesk" in name.lower() or "zendesk_mirror_tools" in module:
            picked[name] = spec
    assert picked, (
        "no Zendesk chat tools were found — this guard would silently pass. "
        "Fix the selector before trusting it."
    )
    return picked


def test_no_registered_zendesk_chat_tool_is_write_shaped():
    """Renn's Zendesk tools are read + propose only.

    ``propose_*`` stages a PENDING local draft in the Revision Center; it is
    not a write to Zendesk and is expressly permitted.
    """
    offenders = [name for name in _zendesk_tools()
                 if _WRITE_VERB_RE.match(name)]

    assert not offenders, (
        "Renn gained (a) write-shaped Zendesk tool(s): "
        + ", ".join(sorted(offenders))
        + "\n\n" + _POLICY
        + "\nRenn's Zendesk surface is read + propose only: it may search and "
          "read the local mirror and stage PENDING drafts, nothing else."
    )


def test_the_zendesk_chat_tool_modules_contain_no_zendesk_write():
    """The modules backing Renn's Zendesk tools must hold no write at all."""
    import importlib

    offenders = []
    modules = {getattr(spec.get("handler"), "__module__", "")
               for spec in _zendesk_tools().values()}
    assert modules, "Renn's Zendesk tools resolved to no module"
    for mod_name in sorted(m for m in modules if m):
        module = importlib.import_module(mod_name)
        source_file = getattr(module, "__file__", None)
        if not source_file:
            continue
        path = Path(source_file)
        tree = _parse(path, _read(path))
        assert tree is not None, f"{mod_name} does not parse"
        names = _code_identifiers(tree)
        for forbidden in _FORBIDDEN_IDENTIFIERS:
            if forbidden in names:
                offenders.append(f"{mod_name}:{names[forbidden]}  {forbidden}")

    assert not offenders, (
        "a module backing Renn's Zendesk tools can write to Zendesk:\n  "
        + "\n  ".join(offenders) + "\n\n" + _POLICY
        + "\nRenn must have no reachable path to a Zendesk write — not a tool, "
          "not a helper in a module a tool executes inside of."
    )


def test_the_declared_claude_tool_specs_expose_no_zendesk_write():
    """The Claude-side tool declarations must not advertise a Zendesk write.

    The wire schema is what the model actually sees, so it is guarded
    separately from the Python registry.
    """
    text = _read(_SRC / "llm" / "claude_tools.py")
    names = re.findall(r'"name":\s*"([a-z0-9_]+)"', text)
    assert names, "no tool names parsed out of claude_tools.py"

    offenders = [n for n in names
                 if "zendesk" in n.lower() and _WRITE_VERB_RE.match(n)]

    assert not offenders, (
        "a declared Claude tool advertises a Zendesk write: "
        + ", ".join(sorted(offenders)) + "\n\n" + _POLICY
    )


# ══════════════════════════════════════════════════════════════════════
#  5. the read-only lanes that must keep working
# ══════════════════════════════════════════════════════════════════════

def test_the_read_only_lanes_survive_the_guard():
    """Read-only is not the same as removed.

    Ticket ingestion and the mirror pull are GET-only and must keep their
    public entry points — a guard that quietly deleted them would break
    product-mode Source Monitor and the mirror.
    """
    from src.data.zendesk_client import ZendeskClient

    for name in ("fetch_incremental", "fetch_view_tickets", "fetch_ticket",
                 "fetch_ticket_fields", "test_connection",
                 "get_articles", "get_articles_paged", "get_article",
                 "get_sections", "get_categories",
                 "list_macros", "list_macros_paged", "get_macro"):
        assert callable(getattr(ZendeskClient, name, None)), (
            f"ZendeskClient.{name} disappeared. The policy is read-ONLY, not "
            "read-nothing: ticket ingestion and the mirror pull are GET-only "
            "lanes that must keep working."
        )
