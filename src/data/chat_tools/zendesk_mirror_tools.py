"""Renn chat tools over the LOCAL Zendesk mirror (zendesk-clone-web WS5).

Module-per-family rule (kb_tools precedent): the mirror tool family lives
HERE, not in enablement_tools.py. Handlers follow the registry contract
``handle_*(conn, args, filters) -> dict`` and share ``_*_impl`` functions
that the Claude-path wrappers in src/llm/claude_tools.py delegate to.

OWNER POLICY (locked, 2026-07-26): **Zendesk is READ-ONLY.** The API is
one-way — content comes IN (pull / import); nothing ever goes OUT. Renn
has no tool on any dispatch path that can publish, push, create, update
or delete anything in the live Zendesk instance, and every Zendesk tool
description says so in words the model reads. A human enablement
specialist copies approved content into the Zendesk editor by hand.
Zendesk is a public, non-restorable domain, which is why it is locked
harder than Guru/Asana (whose gated write paths are unaffected by this).

Hard boundaries (locked by tests/test_zendesk_mirror_tools.py):

* Mirror-only — every read hits the mig-051 SQLite mirror; nothing here
  imports ZendeskClient or touches the network. An empty mirror degrades
  with ``{ok: False, error: 'zendesk_mirror_empty', hint: ...}``.
* Defense in depth — every tool in the family calls
  ``write_reach_refusal`` first (``guard_mirror_only`` underneath), which
  refuses the call if a live-client mutator has somehow become reachable
  from any module on the Renn dispatch path. The same guard runs once at
  import time, so a violation fails closed at load rather than at use.
* Every HTML string this module WRITES passes ``sanitize_html`` at the
  write (``_propose_article_update_impl``'s ``body_html``). Renn's output
  is untrusted — markdown_to_html forwards raw HTML blocks — and the
  stored bytes are copy-exact material, so sanitizing only at render time
  would ship script/form/object markup to the clipboard with nothing to
  show for it in the review.
* ``propose_*`` creates drafts ONLY in status 'pending' with a MANDATORY
  rationale (the provenance filter key — rationale-bearing drafts are
  invisible to the native tab's lists, so its Push button can never see
  them), and NO tool here can transition a draft's status. The specialist
  reviews in the web Revision Center and copies into Zendesk by hand.
* MCP-subprocess transaction discipline (mig-046/kb_tools rule): never
  ``atomic()`` here — the store mutators' ``_txn(conn)`` guard makes
  ``save_*_draft`` safe when the dispatcher already holds an open
  transaction (the commit then belongs to the dispatcher).
* Telemetry: registry dispatch estimates result_rows only from the keys
  tickets/results/rows/findings/data, so every list-returning tool also
  carries a ``results`` alias of its primary list.
"""

from __future__ import annotations

import sqlite3

_MIRROR_EMPTY_HINT = "Import a Help Center export or run the pull."

# ── Zendesk write lockout (defense in depth) ─────────────────────────
#
# The primary guarantee is structural: no module on either Renn dispatch
# path imports a live Zendesk client at all (grep-locked by the test
# suite). This is the SECOND fence — a runtime check that runs before
# every Zendesk tool body and once at import, so that if a future edit
# (or a hot-patched module attribute) ever parks a live mutator inside a
# module Renn's tools execute in, the tool refuses instead of running.
#
# Names are held as STRINGS on purpose: the structural test tokenizes
# this module and forbids these identifiers as CODE tokens, so the guard
# can name what it blocks without itself becoming a reference to it.
_CLIENT_MUTATOR_ATTRS = (
    "create_article", "update_article", "delete_article",
    "create_macro", "update_macro", "delete_macro",
    "create_section", "update_section", "delete_section",
    "publish_article_draft", "publish_macro_draft",
    "set_draft_status", "push_article", "push_macro", "_write",
)

# Modules a Renn Zendesk tool call actually executes inside of. A live
# mutator bound into any of them would be reachable from a tool body.
_GUARDED_MODULES = (
    "src.data.chat_tools.zendesk_mirror_tools",
    "src.data.chat_tools.enablement_tools",
    "src.data.chat_tools.registry",
    "src.llm.claude_tools",
    "src.mcp.chat_mcp_server",
)


class ZendeskWriteBlocked(RuntimeError):
    """A live-Zendesk mutator became reachable from the Renn tool path.

    Raised by ``guard_mirror_only``. Never expected in a healthy build —
    it means the read-only lockout has been breached and the call must
    not proceed.
    """


def _mutator_on(obj) -> str | None:
    """Name of the first live-mutator attribute callable on ``obj``."""
    for attr in _CLIENT_MUTATOR_ATTRS:
        if callable(getattr(obj, attr, None)):
            return attr
    return None


def guard_mirror_only(*candidates) -> None:
    """Raise ``ZendeskWriteBlocked`` if a live-Zendesk mutator is reachable.

    Scans the modules a Renn tool executes inside of, plus any objects the
    caller passes (a client handed in by a future caller, a monkeypatched
    store module, ...). Cheap — a few dozen attribute probes.
    """
    import sys
    for mod_name in _GUARDED_MODULES:
        mod = sys.modules.get(mod_name)
        if mod is None:
            continue
        hit = _mutator_on(mod)
        if hit is not None:
            raise ZendeskWriteBlocked(
                f"Zendesk is read-only: '{hit}' is reachable from "
                f"{mod_name}; the Renn tool path must never hold a live "
                "Zendesk mutator.")
    for obj in candidates:
        if obj is None:
            continue
        hit = _mutator_on(obj)
        if hit is not None:
            raise ZendeskWriteBlocked(
                f"Zendesk is read-only: an object passed to a Zendesk tool "
                f"exposes '{hit}'; refusing the call.")


def write_reach_refusal(*candidates) -> dict | None:
    """None when the lockout holds; the standard refusal dict otherwise.

    Tool bodies never raise (registry contract), so they call this and
    return its dict rather than letting ``ZendeskWriteBlocked`` escape.
    """
    try:
        guard_mirror_only(*candidates)
    except ZendeskWriteBlocked as exc:
        return {"ok": False, "error": "zendesk_write_blocked",
                "message": str(exc),
                "hint": ("Zendesk is read-only in this app - content is "
                         "copied into Zendesk by hand by a specialist.")}
    return None

# Fields the native tab treats as "the public reply" — a new proposed reply
# REPLACES any existing comment action (comment_value OR comment_value_html,
# zendesk_tab.py:232), never rides along as a duplicate second reply.
_COMMENT_FIELDS = ("comment_value", "comment_value_html")


def _count(conn, table: str) -> int:
    """Row count, 0 when the table is missing (pre-051 DB)."""
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    except sqlite3.Error:
        return 0


def mirror_empty(conn, kind: str = "any") -> dict | None:
    """None when the mirror has content for ``kind``; the standard degrade
    dict otherwise. kind: 'articles' | 'macros' | 'any' (either table)."""
    arts = _count(conn, "zendesk_articles")
    macs = _count(conn, "zendesk_macros")
    if kind == "articles":
        empty = arts == 0
    elif kind == "macros":
        empty = macs == 0
    else:
        empty = arts == 0 and macs == 0
    if empty:
        return {"ok": False, "error": "zendesk_mirror_empty",
                "hint": _MIRROR_EMPTY_HINT}
    return None


def _section_names(conn) -> dict:
    try:
        return {r[0]: r[1] for r in conn.execute(
            "SELECT section_id, name FROM zendesk_sections").fetchall()}
    except sqlite3.Error:
        return {}


def _section_and_category(conn, section_id):
    """(section_name, category_name) for a section id — best-effort."""
    if section_id is None:
        return None, None
    try:
        row = conn.execute(
            "SELECT name, category_id FROM zendesk_sections WHERE section_id=?",
            (section_id,)).fetchone()
        if not row:
            return None, None
        cat = None
        if row[1] is not None:
            crow = conn.execute(
                "SELECT name FROM zendesk_categories WHERE category_id=?",
                (row[1],)).fetchone()
            cat = crow[0] if crow else None
        return row[0], cat
    except sqlite3.Error:
        return None, None


def _open_revisions(conn, table: str, id_col: str, target_id) -> int:
    try:
        return conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE {id_col}=? "
            "AND status IN ('pending','ready')", (target_id,)).fetchone()[0]
    except sqlite3.Error:
        return 0


def _norm_sources(sources) -> list[dict]:
    """Normalize the optional sources arg to [{ref, label}] rows (stored as
    sources_json). Non-list / junk entries are dropped, never fatal."""
    out: list[dict] = []
    if isinstance(sources, (list, tuple)):
        for s in list(sources)[:20]:
            if isinstance(s, dict) and (s.get("ref") or s.get("label")):
                out.append({"ref": str(s.get("ref", "")),
                            "label": str(s.get("label", ""))})
            elif isinstance(s, str) and s.strip():
                out.append({"ref": s.strip(), "label": ""})
    return out


def _clamp(value, default: int, cap: int) -> int:
    try:
        return max(1, min(int(value), cap))
    except (TypeError, ValueError):
        return default


_PROPOSE_NOTE = ("Pending draft staged in the Revision Center. Zendesk is "
                 "READ-ONLY in this app: no tool can publish, push or "
                 "otherwise write this to Zendesk. A human specialist "
                 "reviews the draft and copies the approved text into the "
                 "Zendesk editor BY HAND - you cannot publish it, change "
                 "its status, or ask anyone to run a push for you. Tell "
                 "the operator the draft is waiting for their review.")


# ── search / read impls (shared with the Claude-path wrappers) ────────

def _search_zendesk_mirror_impl(conn, query, *, kind="all", limit=10) -> dict:
    """Query-ranked FTS over the local mirror (articles + macros)."""
    from src.data import zendesk_store
    blocked = write_reach_refusal(conn)
    if blocked is not None:
        return blocked
    query = str(query or "").strip()
    if not query:
        return {"ok": False, "error": "query_required",
                "message": "search_zendesk_mirror needs a query string."}
    if kind not in ("articles", "macros", "all"):
        kind = "all"
    empty = mirror_empty(conn, kind)
    if empty is not None:
        return empty
    hits = zendesk_store.search_mirror(conn, query, kind=kind,
                                       limit=_clamp(limit, 10, 25))
    sections = _section_names(conn)
    articles = [{"id": h["id"], "title": h["title"],
                 "section": sections.get(h.get("section_id"),
                                         h.get("section_id")),
                 "snippet": h["snippet"], "score": h["score"]}
                for h in hits.get("articles", [])]
    macros = [{"id": h["id"], "name": h["name"],
               "description": h.get("description", ""),
               "snippet": h["snippet"], "score": h["score"]}
              for h in hits.get("macros", [])]
    return {"ok": True, "query": query, "articles": articles, "macros": macros,
            "counts": {"articles": len(articles), "macros": len(macros)},
            "results": articles + macros}


def _get_zendesk_article_impl(conn, article_id) -> dict:
    from src.data import zendesk_store
    blocked = write_reach_refusal(conn)
    if blocked is not None:
        return blocked
    try:
        aid = int(article_id)
    except (TypeError, ValueError):
        return {"ok": False, "error": "article_id_required",
                "message": ("Pass a numeric article_id - search_zendesk_mirror "
                            "or list_zendesk_articles find ids.")}
    empty = mirror_empty(conn, "articles")
    if empty is not None:
        return empty
    art = zendesk_store.get_article(conn, aid)
    if art is None:
        return {"ok": False, "error": "article_not_found",
                "message": (f"No mirrored article {aid}. "
                            "search_zendesk_mirror finds ids.")}
    sec_name, cat_name = _section_and_category(conn, art.get("section_id"))
    return {"ok": True, "article": {
        "id": aid, "title": art.get("title"),
        "section": sec_name if sec_name is not None else art.get("section_id"),
        "category": cat_name,
        "labels": art.get("labels") or [],
        "body_text": (art.get("body_text") or "")[:20000],
        "html_url": art.get("html_url"),
        "updated_at": art.get("updated_at"),
        "origin": art.get("origin"),
        "open_revisions": _open_revisions(conn, "zendesk_article_drafts",
                                          "article_id", aid)}}


def _get_zendesk_macro_impl(conn, macro_id) -> dict:
    from src.data import zendesk_store
    blocked = write_reach_refusal(conn)
    if blocked is not None:
        return blocked
    try:
        mid = int(macro_id)
    except (TypeError, ValueError):
        return {"ok": False, "error": "macro_id_required",
                "message": ("Pass a numeric macro_id - search_zendesk_mirror "
                            "or list_zendesk_macros find ids.")}
    empty = mirror_empty(conn, "macros")
    if empty is not None:
        return empty
    m = zendesk_store.get_macro(conn, mid)
    if m is None:
        return {"ok": False, "error": "macro_not_found",
                "message": (f"No mirrored macro {mid}. "
                            "search_zendesk_mirror finds ids.")}
    return {"ok": True, "macro": {
        "id": mid, "name": m.get("name"),
        "description": m.get("description"),
        "active": bool(m.get("active")),
        "actions": m.get("actions") or [],
        "updated_at": m.get("updated_at")}}


# ── propose impls (pending drafts ONLY — no status authority) ─────────

def _propose_article_update_impl(conn, *, title, body_markdown, rationale,
                                 article_id=None, sources=None) -> dict:
    """Stage a pending article draft. rationale is MANDATORY (validated here
    too, not just in the strict schema — the Claude execute_tool path skips
    schema validation). Unknown target ids are rejected, never guessed.

    The draft is LOCAL. Nothing here reaches Zendesk: a specialist copies
    the approved text into the Zendesk editor by hand."""
    from src.data import zendesk_store
    blocked = write_reach_refusal(conn)
    if blocked is not None:
        return blocked
    title = str(title or "").strip()
    body_md = str(body_markdown or "")
    rationale = str(rationale or "").strip()
    if not title:
        return {"ok": False, "error": "title_required",
                "message": "propose_article_update needs a title."}
    if not body_md.strip():
        return {"ok": False, "error": "body_markdown_required",
                "message": "propose_article_update needs body_markdown."}
    if not rationale:
        return {"ok": False, "error": "rationale_required",
                "message": ("rationale is mandatory - say WHY the change is "
                            "needed; the reviewer sees it in the Revision "
                            "Center.")}
    target = "new"
    aid = None
    section_id = None
    if article_id not in (None, ""):
        try:
            aid = int(article_id)
        except (TypeError, ValueError):
            return {"ok": False, "error": "invalid_article_id",
                    "message": "article_id must be a mirror article id "
                               "(omit it to propose a brand-new article)."}
        empty = mirror_empty(conn, "articles")
        if empty is not None:
            return empty
        art = zendesk_store.get_article(conn, aid)
        if art is None:
            return {"ok": False, "error": "article_not_found",
                    "message": (f"No mirrored article {aid} to update - omit "
                                "article_id for a brand-new article, or "
                                "search_zendesk_mirror for the right id.")}
        target = "update"
        section_id = art.get("section_id")
    body_html = None
    try:
        from src.data.html_markdown import markdown_to_html
        from src.data.html_sanitize import sanitize_html
        # STORE BOUNDARY (security, load-bearing): markdown_to_html passes
        # raw HTML blocks straight through, so an LLM-authored (or
        # prompt-injected) body could plant <script src>, <form action>,
        # <object data>, <meta refresh>, style url() or inline event
        # handlers in body_html. Those bytes are copied to the clipboard
        # verbatim and pasted into live Zendesk by hand, so they are
        # reduced to the renderable allowlist HERE, at the write — never
        # only where they are rendered.
        body_html = sanitize_html(markdown_to_html(body_md))
    except Exception:  # noqa: BLE001 — draft stays reviewable from markdown
        body_html = None
    try:
        # Direct store call on purpose: _txn(conn) inside save_article_draft
        # handles the already-open-transaction MCP case (never atomic() here).
        draft_id = zendesk_store.save_article_draft(
            conn, title=title, body=body_md, article_id=aid,
            section_id=section_id, body_html=body_html,
            rationale=rationale, sources_json=_norm_sources(sources))
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return {"ok": False, "error": str(exc)[:160]}
    return {"ok": True, "draft_id": draft_id, "status": "pending",
            "target": target, "note": _PROPOSE_NOTE}


def _propose_macro_update_impl(conn, *, name, reply, rationale,
                               macro_id=None, sources=None) -> dict:
    """Stage a pending macro draft. The reply becomes the comment action; a
    targeted macro's non-comment actions are preserved and any existing
    comment_value / comment_value_html action is replaced (never duplicated).

    The draft is LOCAL. Nothing here reaches Zendesk: a specialist copies
    the approved macro into the Zendesk editor by hand."""
    from src.data import zendesk_store
    blocked = write_reach_refusal(conn)
    if blocked is not None:
        return blocked
    name = str(name or "").strip()
    reply = str(reply or "")
    rationale = str(rationale or "").strip()
    if not name:
        return {"ok": False, "error": "name_required",
                "message": "propose_macro_update needs a macro name."}
    if not reply.strip():
        return {"ok": False, "error": "reply_required",
                "message": "propose_macro_update needs the reply text."}
    if not rationale:
        return {"ok": False, "error": "rationale_required",
                "message": ("rationale is mandatory - say WHY the change is "
                            "needed; the reviewer sees it in the Revision "
                            "Center.")}
    mid = None
    description = None
    preserved: list[dict] = []
    if macro_id not in (None, ""):
        try:
            mid = int(macro_id)
        except (TypeError, ValueError):
            return {"ok": False, "error": "invalid_macro_id",
                    "message": "macro_id must be a mirror macro id "
                               "(omit it to propose a brand-new macro)."}
        empty = mirror_empty(conn, "macros")
        if empty is not None:
            return empty
        m = zendesk_store.get_macro(conn, mid)
        if m is None:
            return {"ok": False, "error": "macro_not_found",
                    "message": (f"No mirrored macro {mid} to update - omit "
                                "macro_id for a brand-new macro, or "
                                "search_zendesk_mirror for the right id.")}
        description = m.get("description")
        preserved = [a for a in (m.get("actions") or [])
                     if isinstance(a, dict)
                     and a.get("field") not in _COMMENT_FIELDS]
    actions = [{"field": "comment_value", "value": reply}] + preserved
    try:
        draft_id = zendesk_store.save_macro_draft(
            conn, name=name, actions=actions, description=description,
            macro_id=mid, rationale=rationale,
            sources_json=_norm_sources(sources))
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return {"ok": False, "error": str(exc)[:160]}
    return {"ok": True, "draft_id": draft_id, "status": "pending",
            "note": _PROPOSE_NOTE}


def _list_zendesk_revisions_impl(conn, *, status=None, kind=None,
                                 limit=25) -> dict:
    from src.data import zendesk_store
    blocked = write_reach_refusal(conn)
    if blocked is not None:
        return blocked
    if status is not None:
        status = str(status).strip() or None
    if status is not None and status not in ("pending", "ready", "copied",
                                             "pushed"):
        return {"ok": False, "error": "invalid_status",
                "message": "status must be pending | ready | copied | pushed."}
    if kind not in ("article", "macro"):
        kind = None
    try:
        rows = zendesk_store.list_revisions(conn, status=status, kind=kind,
                                            limit=_clamp(limit, 25, 50))
    except sqlite3.Error:  # pre-051 DB — nothing to list, never raise
        rows = []
    return {"ok": True, "count": len(rows), "revisions": rows, "results": rows}


# ── registry handlers ────────────────────────────────────────────────

def handle_search_zendesk_mirror(conn, args: dict, filters: dict) -> dict:
    return _search_zendesk_mirror_impl(conn, args.get("query", ""),
                                       kind=args.get("kind", "all"),
                                       limit=args.get("limit", 10))


def handle_get_zendesk_article(conn, args: dict, filters: dict) -> dict:
    return _get_zendesk_article_impl(conn, args.get("article_id"))


def handle_get_zendesk_macro(conn, args: dict, filters: dict) -> dict:
    return _get_zendesk_macro_impl(conn, args.get("macro_id"))


def handle_propose_article_update(conn, args: dict, filters: dict) -> dict:
    return _propose_article_update_impl(
        conn, title=args.get("title", ""),
        body_markdown=args.get("body_markdown", ""),
        rationale=args.get("rationale", ""),
        article_id=args.get("article_id"),
        sources=args.get("sources"))


def handle_propose_macro_update(conn, args: dict, filters: dict) -> dict:
    return _propose_macro_update_impl(
        conn, name=args.get("name", ""), reply=args.get("reply", ""),
        rationale=args.get("rationale", ""), macro_id=args.get("macro_id"),
        sources=args.get("sources"))


def handle_list_zendesk_revisions(conn, args: dict, filters: dict) -> dict:
    return _list_zendesk_revisions_impl(conn, status=args.get("status"),
                                        kind=args.get("kind"),
                                        limit=args.get("limit", 25))


# Fail closed at LOAD, not at use: importing this module asserts that no
# module already on the Renn dispatch path holds a live Zendesk mutator.
guard_mirror_only()
