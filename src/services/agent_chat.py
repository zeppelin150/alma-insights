"""AgentChatController — builds and owns a fully-wired enablement ``ChatEngine``
for the standalone Agent page (``src/ui/web``).

It mirrors the enablement Workbench's engine setup so the embedded web chat talks
to the SAME live backend: the ``alma-chat-tools`` MCP server, provider routing
(claude/gemini via ``build_client_for_task``), a persisted chat session, and the
adaptive bridge-recycle. Kept self-contained so the Agent page doesn't depend on
the Workbench page; the shared setup is a candidate to factor out later.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, Signal, Slot

logger = logging.getLogger("alma.agent_chat")


# ── review-honesty helpers (the in-chat approval panel) ──────────────
#
# The panel diffs TEXT, but publish ships ``enablement_store.publish_body``
# (HTML). When a draft carries ``content_html`` the reviewed text is a
# down-conversion of those bytes (``enablement_store.review_text``) and the
# down-conversion is LOSSY: ``html_to_markdown`` drops script/style bodies and
# every attribute except href. Before 2026-07-26 the panel diffed the markdown
# COLUMN instead, so an imported card's hostile ``content_html`` (a <script>
# block, an onerror handler pointing at evil.example) was invisible on the only
# surface gating the push, yet went out over the wire verbatim.
#
# The rule this encodes: the reviewed representation must be complete with
# respect to what is sent, or the incompleteness must be NAMED on screen. These
# helpers compute that name.
#
# 2026-07-27 — THE SECOND FIX, and the more important one. The first version of
# this scanner DROPPED a finding whose every word already appeared in the
# reviewed text ("it was accounted for"). The draft author writes the prose AND
# the payload, so seeding the prose with the payload's own tokens
# (``<p>fetch https evil.example/x document.cookie</p>``) emptied the finding
# list and the notice went silent while the <script> still shipped. A heuristic
# that decides FOR the human what they do not need to see, over inputs the
# adversary controls, is not a control at all.
#
# The predicate that replaced it is PROVABLE and author-independent:
# ``is_exact_preimage`` re-runs ``publish_body`` over the reviewed text and
# compares bytes. Either the shipped bytes ARE a pure function of what was on
# screen (nothing can hide) or they are not (say so — always). The gap list
# below is now DETAIL ONLY: it enriches a notice whose presence it can never
# decide, and no listing rule may suppress anything.

_OPAQUE_TAGS = frozenset({
    "script", "style", "noscript", "template", "head",
    "iframe", "frame", "frameset", "object", "embed", "applet",
    "svg", "math", "form", "meta", "link", "base",
})

# Attributes whose value is a payload (code / a fetched URL) rather than prose.
# ``html_to_markdown`` keeps NONE of these except ``href``.
_PAYLOAD_ATTRS = frozenset({
    "src", "srcdoc", "srcset", "data", "action", "formaction", "poster",
    "background", "xlink:href", "href", "style", "content", "value",
})

# Attributes that carry prose a human is meant to read. The projection drops
# them like any other attribute, so hidden text can ride in one. Machine
# identifiers (class / id / data-*) are deliberately NOT here: expand_blocks
# generates them by the dozen and naming them would bury the real findings.
_TEXT_ATTRS = frozenset({"title", "alt", "aria-label", "placeholder", "summary"})

_WORD_RE = None      # lazily compiled (module import stays import-light)
_ACTIVE_CSS = None   # ditto — CSS that can fetch or execute, not just style
_GAP_LIMIT = 6       # how many distinct gaps we name before "+N more"
_SNIPPET = 90        # per-gap payload budget, characters


def _words(text: str) -> set:
    """Lowercased word-ish tokens (>=3 chars).

    Used ONLY to ADD a finding (visible text of the body that never reached the
    reviewed text). It must never be used to remove one — that was the forgeable
    heuristic this module was rebuilt to delete.
    """
    global _WORD_RE
    if _WORD_RE is None:
        import re
        _WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._'/-]{2,}")
    return {t.lower().strip("._'-/") for t in _WORD_RE.findall(text or "")}


def _is_active_css(value: str) -> bool:
    """True for inline CSS that can fetch or execute — not merely decorate."""
    global _ACTIVE_CSS
    if _ACTIVE_CSS is None:
        import re
        _ACTIVE_CSS = re.compile(
            r"url\s*\(|expression\s*\(|javascript\s*:|@import|behavior\s*:", re.I)
    return bool(_ACTIVE_CSS.search(value or ""))


def _clip(value: str, limit: int = _SNIPPET) -> str:
    """One-line, length-capped rendering of a payload for the notice."""
    flat = " ".join((value or "").split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


class _PublishScan:
    """Collects the parts of a publish body a markdown projection cannot show.

    Not a sanitizer and not a security control — it never edits the bytes. It
    only answers "what is in here that the reviewer will not see", so the panel
    can say so out loud.
    """

    def __init__(self):
        self.findings: list[tuple[str, str]] = []   # (label, payload) — material
        self.styling: list[tuple[str, str]] = []    # presentational-only drops
        self.text_words: set = set()                # visible (non-opaque) text
        self._opaque: list[str] = []
        self._opaque_text: list[str] = []

    def scan(self, html: str) -> None:
        from html.parser import HTMLParser

        outer = self

        class _P(HTMLParser):
            def handle_starttag(self, tag, attrs):
                outer._starttag(tag, attrs)

            def handle_startendtag(self, tag, attrs):
                outer._starttag(tag, attrs, self_closing=True)

            def handle_endtag(self, tag):
                outer._endtag(tag)

            def handle_data(self, data):
                outer._data(data)

        p = _P(convert_charrefs=True)
        p.feed(html or "")
        p.close()
        outer._flush_opaque()

    # ── parser hooks ────────────────────────────────────────────────

    def _starttag(self, tag, attrs, self_closing: bool = False) -> None:
        tag = (tag or "").lower()
        for raw_name, raw_value in attrs or []:
            name = (raw_name or "").lower()
            value = raw_value or ""
            if name.startswith("on"):
                self.findings.append((f"<{tag} {name}> handler", value))
            elif name == "style" and value.strip():
                # Inline CSS is presentational UNLESS it can fetch or execute.
                bucket = (self.findings if _is_active_css(value)
                          else self.styling)
                bucket.append((f"<{tag} style>", value))
            elif name in _PAYLOAD_ATTRS or name in _TEXT_ATTRS:
                if value.strip():
                    self.findings.append((f"<{tag} {name}>", value))
        if tag in _OPAQUE_TAGS:
            if self_closing or tag in ("meta", "link", "base"):
                self.findings.append((f"<{tag}> element", ""))
            else:
                self._flush_opaque()
                self._opaque.append(tag)

    def _endtag(self, tag) -> None:
        tag = (tag or "").lower()
        if self._opaque and self._opaque[-1] == tag:
            self._flush_opaque()

    def _data(self, data) -> None:
        if self._opaque:
            self._opaque_text.append(data or "")
        else:
            self.text_words |= _words(data)

    def _flush_opaque(self) -> None:
        if not self._opaque:
            return
        tag = self._opaque.pop()
        body = "".join(self._opaque_text)
        self._opaque_text = []
        self.findings.append((f"<{tag}> content", body))


def is_exact_preimage(reviewed: str, body: str) -> bool:
    """True when ``body`` is exactly what publishing ``reviewed`` would produce.

    Verification, not a flag: when the shipped bytes are a pure function of the
    text on screen, the operator has seen everything that can ship — nothing can
    hide in markup the composition generates deterministically from their own
    words. Only when this is False does the projection have room to drop things.
    """
    try:
        from src.data.enablement_store import publish_body
        return publish_body({"content": reviewed or ""}) == (body or "")
    except Exception:  # noqa: BLE001 — cannot prove it → treat as lossy
        return False


def is_projection_lossy(reviewed: str, body: str) -> bool:
    """THE predicate the notice fires on. Provable, and author-independent.

    True when there are bytes to send and they are NOT provably reconstructible
    from the text the operator reviewed. Its only inputs are the two byte
    strings and ``publish_body`` itself; nothing a draft author can write into
    the prose changes the answer, which is exactly what the deleted word-token
    heuristic could not say for itself.
    """
    if not (body or "").strip():
        return False
    return not is_exact_preimage(reviewed, body)


def publish_gaps(reviewed: str, body: str) -> tuple[list[str], list[str]]:
    """What ``body`` (the shipped bytes) carries that ``reviewed`` omits.

    Returns ``(material, presentational)`` phrase lists — material is anything
    that fetches, executes, or is readable content the reviewer never saw;
    presentational is inline styling that only changes how the card looks.

    DETAIL ONLY. This list makes a notice specific; it does NOT decide whether
    one appears (see :func:`is_projection_lossy`), and it drops nothing. The
    previous version skipped a finding whose words already appeared in the
    reviewed text, which let an author who controls both the prose and the
    payload empty this list on demand. An extra ``<a href>`` line is noise; a
    missing ``<script>`` line was an exploit.
    """
    if not is_projection_lossy(reviewed, body):
        return [], []
    scan = _PublishScan()
    try:
        scan.scan(body)
    except Exception:  # noqa: BLE001 — an unparsable body is itself unreviewed
        return (["the HTML that will be sent could not be parsed for review, "
                 "so the text above may not represent it"], [])
    material: list[str] = []
    for label, payload in scan.findings:
        flat = " ".join((payload or "").split())
        material.append(f"{label} {_clip(flat)}" if flat else label)
    # Additive only: visible text of the shipped body that never reached the
    # reviewed text. Seeding the prose can empty THIS line and nothing else.
    missing = sorted(scan.text_words - _words(reviewed))
    if missing:
        material.append("text not shown above: " + _clip(", ".join(missing)))
    styling = [f"{label} {_clip(payload)}" for label, payload in scan.styling]
    return _dedupe(material), _dedupe(styling)


def _dedupe(items: list[str]) -> list[str]:
    """Order-preserving de-duplication."""
    out, seen = [], set()
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _listed(items: list[str]) -> str:
    """Up to ``_GAP_LIMIT`` phrases, with an honest '+N more' tail."""
    shown = items[:_GAP_LIMIT]
    more = len(items) - len(shown)
    return "; ".join(shown) + (f" (+{more} more)" if more > 0 else "")


LOSSY_NOTICE = (
    "The text above is a DOWN-CONVERSION of the HTML that will be sent, not "
    "the bytes themselves, and it is not provably complete: republishing the "
    "text above would not reproduce what ships. Open \"Exact bytes that will "
    "be sent\" below and read them before approving.")


def review_notice(reviewed: str, body: str, *, card_id: str = "",
                  has_baseline: bool = True) -> str:
    """The panel's honesty line — '' only when the diff above needs no caveat.

    The FIRST caveat is unconditional and carries the whole guarantee: whenever
    ``is_projection_lossy`` holds, ``LOSSY_NOTICE`` is emitted, full stop. No
    scanner result, no wording in the draft, and no author-supplied prose can
    suppress it — the only thing that silences this notice is
    ``is_exact_preimage`` being PROVED, i.e. the bytes really are a pure
    function of the reviewed text. (Before 2026-07-27 the notice's existence
    depended on the gap list being non-empty, and the gap list was forgeable.)

    The rest are refinements that make the caveat specific:

    * what the projection dropped — script bodies, handlers, attribute
      payloads, unseen text (the loud list; this is what hid a <script> and an
      evil.example fetch);
    * styling that ships and the text cannot show (quiet, but not silent);
    * the draft replaces a live card but no local copy of that card exists, so
      the diff renders a replacement as pure additions. (The missing baseline
      is a known, owner-DEFERRED audit finding — not fixed here, but not
      allowed to look fixed either.)
    """
    parts = []
    if is_projection_lossy(reviewed, body):
        parts.append(LOSSY_NOTICE)
    material, styling = publish_gaps(reviewed, body)
    if material:
        parts.append(
            "NOT SHOWN ABOVE but WILL be sent to Guru — " + _listed(material)
            + ". The review text is a markdown down-conversion of the HTML "
              "that ships; anything listed here has no representation in it.")
    if styling:
        parts.append("Inline styling also ships and the text cannot show it: "
                     + _listed(styling) + ".")
    if card_id and not has_baseline:
        parts.append(
            f"No local copy of live card {card_id} is available, so this diff "
            "shows additions only — it is not a true before/after of the "
            "card this publish will REPLACE.")
    return " ".join(parts)


# ── the approval binding (TIME-OF-CHECK / TIME-OF-USE) ───────────────
#
# 2026-07-27 — THE THIRD FIX, and the one the previous two made visible.
#
# Rounds one and two made the panel tell the truth about the bytes it was
# rendering. Both were verified. Neither was load-bearing, because THE
# APPROVAL WAS BOUND TO A DRAFT ID, NOT TO THE BYTES. Traced end to end with
# a spy Guru client: the panel rendered
# ``<p>Q3 billing runbook. Escalate to the RCM lead.</p>`` with an empty
# notice and one diff row; ``enablement_store.update_draft_content`` — exactly
# what the ``revise_draft`` chat tool calls — then mutated the row with no
# lock, no gate and no status change even though the draft had a pending
# push; the operator's click published the NEW bytes. They approved what the
# panel showed and something else shipped.
#
# It is REACHABLE by the model: ``revise_draft`` is a registered chat tool, so
# a prompt-injected Renn (source docs, ticket exports, Asana comments and Guru
# cards are all in the threat model) can revise a draft after the human has
# read the panel.
#
# The design is the Zendesk lane's, not a new one: ``zendesk_web``'s
# ``_record_review`` / ``_review_covers`` bind a clipboard release to a
# recompute-from-row content hash and refuse when the row moved under the
# operator. Here the same shape guards a live WRITE:
#
#   * ``pending_drafts`` renders a draft  -> record the fingerprint of exactly
#     what it put on screen;
#   * ``approve_draft`` -> RECOMPUTE the fingerprint FROM THE ROW and refuse
#     unless it still matches.
#
# The recompute is the whole point. A client-supplied hash would only move the
# forgery to the QWebChannel, where any page script can call any slot.
#
# Fail closed on every branch: no recorded review, a publish body that cannot
# be produced, a vanished row, or any mismatch means NOTHING is published AND
# no sign-off is recorded — the refusal happens BEFORE
# ``enablement_store.approve_draft``, because recording a sign-off and then
# refusing would leave ``approved_at`` set and the very next
# ``_push_guru_draft_impl`` from the model would sail through the M5 gate.

STALE_REVIEW_REFUSAL = (
    "This draft changed after you reviewed it — nothing was published. "
    "Re-open the review panel and read the new content before approving.")

NO_REVIEW_REFUSAL = (
    "This draft has not been reviewed on this panel — nothing was published. "
    "Open the review panel and read what will be sent before approving.")

UNREADABLE_BODY_REFUSAL = (
    "This draft's publish body could not be read, so what would be sent "
    "cannot be verified — nothing was published.")

MISSING_DRAFT_REFUSAL = (
    "This draft no longer exists — nothing was published.")

# ── the native confirm, and the one-successful-publish rule ──────────
#
# 2026-07-27 — THE FOURTH AND FIFTH FIXES.
#
# (4) THERE WAS NO NATIVE CONFIRM ON THIS PATH AT ALL. ``refreshDrafts()`` and
#     ``approveDraft()`` are both page-callable QWebChannel slots, and the
#     QWebChannel is the trust boundary: a probe published hostile bytes to the
#     LIVE Guru instance from those two calls alone, with every signal
#     discarded. The Zendesk lane documented this exact defect class and closed
#     it with a native dialog that displays the exact bytes; that lane only
#     writes to the CLIPBOARD. This one performs the real remote write, and it
#     had nothing. A Qt dialog is the only channel Python can prove a human
#     perceived, so the publish now takes one — showing the bytes UNCOLLAPSED
#     and the resolved TARGET, defaulting to Cancel — and FAILS CLOSED when no
#     dialog host was injected.
#
# (5) A FAILED PUBLISH LEFT A PERMANENT PRE-AUTHORIZATION. ``approved_at`` was
#     written before the push and never rolled back, and ``clear_push_request``
#     ran unconditionally afterwards, so a transient 401 left the gate open AND
#     removed the draft from the review panel. The sign-off is now scoped and
#     single-use (``enablement_store.record_approval`` /
#     ``approval_open`` / ``revoke_approval``) and is spent ONLY by a publish
#     that actually succeeded; anything else re-arms the review request with the
#     failure reason attached.

NO_CONFIRM_HOST_REFUSAL = (
    "This build cannot show the native publish confirmation, so nothing was "
    "published. Publishing to Guru requires the confirmation dialog — use the "
    "Workbench, or restart the app.")

DECLINED_REFUSAL = (
    "You cancelled the publish confirmation — nothing was published.")

PUBLISH_FAILED_REFUSAL = (
    "The publish did not succeed, so your sign-off was NOT used and nothing "
    "reached Guru. The draft is back in this panel awaiting approval. Reason: "
    "{reason}")


def _target_label(card_id, target) -> str:
    """One human sentence naming WHERE this publish lands.

    On the panel and in the native confirm, because ``publish_draft``'s branch
    is decided entirely by these three ids and none of them were previously
    visible on any approval surface: a non-empty ``card_id`` OVERWRITES that
    live card, an empty one CREATES a card in that collection (and folder, if
    one is set).
    """
    t = target or {}
    collection = str(t.get("collection_id") or "").strip()
    folder = str(t.get("folder_id") or "").strip()
    if str(card_id or "").strip():
        return f"OVERWRITE the live Guru card {str(card_id).strip()}"
    where = f"collection {collection}" if collection else "the default collection"
    if folder:
        where += f", folder {folder}"
    return f"CREATE a new Guru card in {where}"


def approval_fingerprint(title, card_id, body, target=None) -> str:
    """Identity of everything ONE approval commits to — bytes AND destination.

    Thin re-export of :func:`src.data.enablement_store.approval_fingerprint`.
    The math lives in the data layer because the M5 gate
    (``_push_guru_draft_impl``) has to verify the same identity and must not
    import a service; a second definition here is exactly the drift that let
    the publish body and the previewed body disagree for months. Imported
    lazily so this module stays import-light.
    """
    from src.data.enablement_store import approval_fingerprint as _fp
    return _fp(title, card_id, body, target)


def draft_fingerprint(draft, *, collection_id=None, folder_id=None) -> str | None:
    """``approval_fingerprint`` RECOMPUTED FROM A DRAFT ROW (re-export).

    ``None`` when the publish body cannot be produced — an unreadable body is
    never approvable, and ``None`` never compares equal to a recorded
    fingerprint, so every caller fails closed without a special case.
    """
    from src.data.enablement_store import draft_fingerprint as _fp
    return _fp(draft, collection_id=collection_id, folder_id=folder_id)


class DriveListWorker(QThread):
    """Off-thread Drive folder lister for the M3 picker (the GoogleOAuthWorker
    pattern). DriveReader's HTTP calls block, so they MUST NOT run inside the
    QWebChannel slot (that freezes the Qt event loop per tree expand — invariant
    6). This worker runs ``list_picker_roots()`` for the roots (empty/``root``
    parent) — auth-aware: Shared Drives PLUS shared-with-me folders, the only
    roots a service account actually has — or ``list_folders(parent_id)`` for a
    node's children, then emits id+name+driveId(+shared) rows on ``finished``
    (queued to the main thread). No file bodies ever cross — only folder rows
    for lazy-tree navigation. Shared by the in-chat React picker AND the native
    ``DriveFolderPickerDialog`` (the model-independent entry point), so both
    trees always agree.
    """

    finished = Signal(str, str, list)   # (request_id, parent_id, folders)
    failed = Signal(str, str)           # (request_id, message — redacted/no PHI)

    def __init__(self, request_id: str, parent_id: str, parent=None):
        super().__init__(parent)
        self._request_id = request_id or ""
        self._parent_id = parent_id or ""

    def run(self):
        try:
            from src.data.drive_reader import DriveReader
            reader = DriveReader.from_settings()
            pid = (self._parent_id or "").strip()
            if pid and pid.lower() != "root":
                rows = reader.list_folders(pid)
            else:
                rows = reader.list_picker_roots()
            folders = [{"id": r.get("id"), "name": r.get("name"),
                        "driveId": r.get("drive_id") or r.get("driveId"),
                        "shared": bool(r.get("shared"))}
                       for r in (rows or [])]
            self.finished.emit(self._request_id, self._parent_id, folders)
        except Exception as exc:  # noqa: BLE001 — surface a short, non-PHI message
            self.failed.emit(self._request_id, str(exc)[:160])


class AsanaListWorker(QThread):
    """Off-thread Asana project lister for the M4 picker (mirrors DriveListWorker).

    AsanaClient is stdlib ``urllib`` so it's perfectly safe off-thread, but the
    HTTP still blocks, so it MUST NOT run inside the QWebChannel slot (that freezes
    the Qt event loop while the picker opens — invariant 6). This worker calls the
    live ``list_asana_projects`` impl (shared PAT from the keyring) and emits
    ``[{gid, name}]`` on ``finished`` (queued to the main thread). If no PAT is
    configured it emits ``asana_not_connected`` so the picker can offer a Settings
    hint. No task/board internals ever cross — only gid+name for selection.
    """

    finished = Signal(str, list)        # (request_id, projects [{gid,name}])
    notConnected = Signal(str)          # (request_id) — no shared PAT
    failed = Signal(str, str)           # (request_id, message — short/no PHI)

    def __init__(self, request_id: str, parent=None):
        super().__init__(parent)
        self._request_id = request_id or ""

    def run(self):
        try:
            from src.data.chat_tools.enablement_tools import _list_asana_projects_impl
            res = _list_asana_projects_impl(None) or {}
            if not res.get("ok"):
                if res.get("error") == "asana_not_connected":
                    self.notConnected.emit(self._request_id)
                else:
                    self.failed.emit(self._request_id,
                                     str(res.get("error") or "asana_list_failed")[:160])
                return
            projects = [{"gid": p.get("gid"), "name": p.get("name")}
                        for p in (res.get("projects") or [])]
            self.finished.emit(self._request_id, projects)
        except Exception as exc:  # noqa: BLE001 — surface a short, non-PHI message
            self.failed.emit(self._request_id, str(exc)[:160])


class GuruListWorker(QThread):
    """Off-thread Guru target lister for the M5 publish-target picker (mirrors
    AsanaListWorker).

    Two-level: an EMPTY ``collection_id`` lists the operator's Guru COLLECTIONS;
    a non-empty ``collection_id`` lists THAT collection's FOLDERS. GuruClient does
    blocking HTTP, so it MUST NOT run inside the QWebChannel slot (that freezes the
    Qt event loop while the picker opens — invariant 6). This worker calls the live
    ``_list_guru_collections_impl`` / ``_list_guru_folders_impl`` (REUSED — not
    rebuilt) and emits ``[{id, name}]`` on ``finished`` (queued to the main thread).
    Folder rows flatten ``title`` → ``name`` so the picker has one row shape. If
    Guru creds are missing it emits ``notConnected`` so the picker can offer a
    Settings hint. Guru collection/folder names are OPERATIONAL KB metadata (the
    knowledge base's own structure), NOT patient PHI — same source-aware
    classification as the Asana boards (invariant 13).
    """

    finished = Signal(str, str, str, list)  # (request_id, level, collection_id, items [{id,name}])
    notConnected = Signal(str)              # (request_id) — Guru creds missing
    failed = Signal(str, str)              # (request_id, message — short/no PHI)

    def __init__(self, request_id: str, collection_id: str = "", parent=None):
        super().__init__(parent)
        self._request_id = request_id or ""
        self._collection_id = collection_id or ""

    def run(self):
        try:
            from src.data.chat_tools.enablement_tools import (
                _list_guru_collections_impl, _list_guru_folders_impl)
            cid = (self._collection_id or "").strip()
            if cid:
                res = _list_guru_folders_impl(None, cid) or {}
                level = "folders"
            else:
                res = _list_guru_collections_impl(None) or {}
                level = "collections"
            if not res.get("ok"):
                if res.get("error") == "guru_not_connected":
                    self.notConnected.emit(self._request_id)
                else:
                    self.failed.emit(self._request_id,
                                     str(res.get("error") or "guru_list_failed")[:160])
                return
            if level == "folders":
                # Folder rows expose ``title`` — flatten to ``name`` for one shape.
                items = [{"id": f.get("id"), "name": f.get("title") or f.get("name") or ""}
                         for f in (res.get("folders") or [])]
            else:
                # Collections carry read_only (M8) so the picker can badge
                # Guru-managed collections that can't take a published folder.
                items = [{"id": c.get("id"), "name": c.get("name") or "",
                          "read_only": bool(c.get("read_only"))}
                         for c in (res.get("collections") or [])]
            self.finished.emit(self._request_id, level, cid, items)
        except Exception as exc:  # noqa: BLE001 — surface a short, non-PHI message
            self.failed.emit(self._request_id, str(exc)[:160])


# ═══════════════════════════════════════════════════════════════════════
#  Gated-write op registry (WS1-M6 refactor of the M7b if/elif dispatch).
#
#  Each op is a module-level handler ``fn(params: dict, ctx: dict) -> dict``
#  run inside the WriteWorker thread (``ctx`` currently carries ``db_path``
#  for handlers that touch the local mirror). New ops REGISTER here instead of
#  growing an if/elif (WS3-M7's upload_artifact_to_drive slots straight in).
#
#  ``PRE_DISPATCH_CHECKS`` maps op → main-thread check run by execute_write
#  BEFORE mark_resolved: returning a dict BLOCKS the dispatch *without burning
#  the confirm row* (the card stays open) — the needs_google_connect pattern.
#  Checks must be cheap READS only; the resolve-first ordering protects the
#  write itself, not reads.
# ═══════════════════════════════════════════════════════════════════════

def _write_guru_folder(params: dict, ctx: dict, *, rename: bool) -> dict:
    from src.data.guru_client import GuruClient
    email, token = GuruClient.load_credentials()
    if not (email and token):
        return {"ok": False, "error": "guru_not_connected"}
    client = GuruClient(email, token)
    if rename:
        return client.rename_folder(params.get("folder_id"), params.get("new_title"))
    return client.create_folder(
        params.get("collection_id"), params.get("title"),
        parent_folder_id=params.get("parent_folder_id") or None)


def _write_create_asana_task(params: dict, ctx: dict) -> dict:
    from src.data.asana_client import AsanaClient
    client = AsanaClient.from_store()
    if not client.api_key:
        return {"ok": False, "error": "asana_not_connected"}
    return client.create_task(
        params.get("project_gid"), params.get("name"),
        notes=params.get("notes") or None, due_on=params.get("due_on") or None)


def _write_asana_task_update(params: dict, ctx: dict) -> dict:
    """WS1-M6: execute one gated task mutation via asana_writeback.

    Drift protection (pre-mortem-corrected): CAS applies ONLY to the
    destructive verbs — complete/reopen compare live modified_at against the
    propose-time snapshot; set_due compares the DUE FIELD itself (likes and
    comments bump modified_at without touching due_on, and whole-task CAS
    would make the most common confirms spuriously fail). comment/add_subtask
    are append-only and execute unconditionally. After a drift check passes,
    the anchor is re-stamped to the live value so asana_writeback's own
    internal CAS agrees rather than double-jeopardizing the write.
    """
    from src.data import asana_writeback as awb
    from src.data import enablement_tasks as et
    from src.data.asana_client import AsanaClient
    from src.data.connection_factory import get_connection

    client = AsanaClient.from_store()
    if not client.api_key:
        return {"ok": False, "error": "asana_not_connected"}
    db_path = ctx.get("db_path") or ""
    if not db_path:
        return {"ok": False, "error": "no_db"}
    conn = get_connection(db_path)
    try:
        task_id = str(params.get("task_id") or "")
        action = params.get("action") or ""
        value = params.get("value") or ""
        row = et.get_task(conn, task_id)
        if not row:
            return {"ok": False, "error": "task_not_found"}
        gid = row.get("source_ref") or ""

        if action in ("complete", "reopen", "set_due"):
            try:
                live = client.get_task(gid, opt_fields="modified_at,due_on") or {}
            except Exception as exc:  # noqa: BLE001 — CAS read is protection, not a gate
                logger.debug("gated-write CAS read failed for %s: %s", gid, exc)
                live = {}
            if live:
                if action == "set_due":
                    expected_due = params.get("expected_due") or ""
                    if (live.get("due_on") or "") != expected_due:
                        return {"ok": False, "conflict": True, "op_action": action,
                                "error": "the due date changed in Asana since I "
                                         "proposed this — please re-check the task"}
                else:
                    expected = params.get("expected_modified_at") or ""
                    if expected and (live.get("modified_at") or "") != expected:
                        return {"ok": False, "conflict": True, "op_action": action,
                                "error": "the task changed in Asana since I "
                                         "proposed this — please re-check it"}
                if live.get("modified_at"):
                    et.update_task(conn, task_id,
                                   remote_modified_at=live["modified_at"])

        if action in ("complete", "reopen"):
            res = awb.set_completed_in_asana(conn, task_id, action == "complete",
                                             client=client)
        elif action == "set_due":
            res = awb.update_due_in_asana(conn, task_id, value or None,
                                          client=client)
        elif action == "comment":
            res = awb.post_comment_to_asana(conn, task_id, value, client=client)
        elif action == "add_subtask":
            res = awb.create_subtask_in_asana(conn, task_id, value,
                                              client=client, created_by="agent")
        else:
            return {"ok": False, "error": f"unknown_action: {action}"}
        res = dict(res or {})
        res["op_action"] = action
        res["task_title"] = (row.get("title") or "")[:80]
        return res
    finally:
        conn.close()


def _write_upload_artifact(params: dict, ctx: dict) -> dict:
    """WS3-M7: upload a rendered artifact file into the EC Drive folder via
    the WS2-M1 THROTTLED exporter surface (never a raw create). Runs in the
    WriteWorker thread of the MAIN process — legal for oauth_user."""
    import mimetypes
    from pathlib import Path
    from src.data import artifact_store
    from src.data.connection_factory import get_connection
    from src.export.gdrive_export import GoogleDriveExporter

    db_path = ctx.get("db_path") or ""
    if not db_path:
        return {"ok": False, "error": "no_db"}
    conn = get_connection(db_path)
    try:
        artifact = artifact_store.get_artifact(conn, str(params.get("artifact_id") or ""))
        if not artifact:
            return {"ok": False, "error": "artifact_not_found"}
        path = Path(artifact.get("file_path") or "")
        if not path.is_file():
            return {"ok": False, "error": "artifact_file_missing"}
        folder_id = str(params.get("folder_id") or "")
        if not folder_id:
            return {"ok": False, "error": "no_target_folder"}
        exporter = GoogleDriveExporter.from_settings(folder_id)
        # Explicit map first — Windows' mimetypes reads the registry and is
        # not deterministic across machines for Office types.
        known = {
            ".pptx": "application/vnd.openxmlformats-officedocument."
                     "presentationml.presentation",
            ".svg": "image/svg+xml", ".png": "image/png", ".md": "text/markdown",
            ".pdf": "application/pdf",
        }
        mime = (known.get(path.suffix.lower())
                or mimetypes.guess_type(path.name)[0]
                or "application/octet-stream")
        res = exporter.upload_file(path.name, path.read_bytes(), mime, folder_id)
        file_id = res.get("id") or ""
        import json as _json
        try:
            prov = _json.loads(artifact.get("provenance_json") or "{}")
        except (ValueError, TypeError):
            prov = {}
        prov["drive_file_id"] = file_id
        artifact_store.update_artifact(conn, artifact["artifact_id"],
                                       status="published", provenance_json=prov)
        return {"ok": True, "op_action": "upload_artifact",
                "artifact_id": artifact["artifact_id"],
                "drive_file_id": file_id, "name": path.name}
    finally:
        conn.close()


def _precheck_upload_artifact(controller, params: dict) -> dict | None:
    """WS3-M7 pre-dispatch check (main thread, cheap READ): OAuth is
    disable-on-launch, so a fresh session's Confirm would otherwise burn the
    row on a doomed upload — instead the card stays open, the operator
    reconnects Google, and clicks Confirm again."""
    try:
        from src.data.settings_manager import get_section
        drive_cfg = (get_section("enablement", {}) or {}).get("drive") or {}
        if drive_cfg.get("auth_type", "service_account") != "oauth_user":
            return None
        from src.data import google_oauth
        if google_oauth.is_active():
            return None
    except Exception:  # noqa: BLE001 — a broken check never blocks
        return None
    try:
        controller.start_google_connect()
    except Exception:  # noqa: BLE001 — connect kick-off is best-effort
        pass
    return {"error": "needs_google_connect",
            "message": "Google isn't connected this session — connect (or "
                       "Reconnect in Settings), then click Confirm again."}


WRITE_HANDLERS = {
    "create_guru_folder": lambda p, ctx: _write_guru_folder(p, ctx, rename=False),
    "rename_guru_folder": lambda p, ctx: _write_guru_folder(p, ctx, rename=True),
    "create_asana_task": _write_create_asana_task,
    "asana_task_update": _write_asana_task_update,
    "upload_artifact_to_drive": _write_upload_artifact,
}

# op → main-thread pre-dispatch check (cheap read; dict result blocks WITHOUT
# burning the confirm row — the card stays open for a retry).
PRE_DISPATCH_CHECKS: dict = {
    "upload_artifact_to_drive": _precheck_upload_artifact,
}


class WriteWorker(QThread):
    """Off-thread executor for a GATED, non-idempotent live write (M7b — the
    Confirm card's Confirm button). Mirrors ``AsanaListWorker``: the GuruClient /
    AsanaClient HTTP blocks, so it MUST run off the Qt thread, NEVER inside the
    QWebChannel slot.

    Dispatches on ``op`` via the module-level ``WRITE_HANDLERS`` registry and
    emits ``finished(request_id, ok, result_json)`` or
    ``failed(request_id, message)`` (queued → main thread). The single write runs
    EXACTLY ONCE per worker; the controller has already mark_resolved'd the row
    (single-winner) BEFORE spawning this, so one operator click = one live write.
    """

    finished = Signal(str, bool, str)   # (request_id, ok, result_json)
    failed = Signal(str, str)           # (request_id, message — short/no PHI)

    def __init__(self, request_id: str, op: str, params: dict, parent=None,
                 ctx: dict | None = None):
        super().__init__(parent)
        self._request_id = request_id or ""
        self._op = op or ""
        self._params = dict(params or {})
        self._ctx = dict(ctx or {})

    def run(self):
        import json
        try:
            result = self._dispatch()
            self.finished.emit(self._request_id, bool(result.get("ok")),
                               json.dumps(result, default=str))
        except Exception as exc:  # noqa: BLE001 — surface a short, non-PHI message
            self.failed.emit(self._request_id, str(exc)[:200])

    def _dispatch(self) -> dict:
        handler = WRITE_HANDLERS.get(self._op)
        if handler is None:
            return {"ok": False, "error": f"unknown_op: {self._op}"}
        return handler(self._params, self._ctx)


class AgentChatController(QObject):
    """Owns the Agent's ChatEngine + warm client. Expose ``engine`` to the
    bridge and route sends through ``send`` (lazily wires the provider)."""

    # Connect-Google lifecycle state for the in-chat card (M2). The bridge
    # re-emits this to React: 'connecting' | 'connected' | 'failed'. Payload is a
    # JSON string; STATUS ONLY — never a token, email, or account name (inv. 13).
    googleAuthState = Signal(str)

    # Drive folder picker round-trip (M3). The bridge re-emits both to React.
    #   driveFoldersListed: JSON {request_id, parent_id?, folders:[{id,name,driveId}]}
    #     or {request_id, needs_connect:True} when Google isn't active this session.
    #   actionResolved: JSON {request_id} once an action is resolved → React closes
    #     the picker.
    # driveFoldersListed carries folder NAMES across the bridge for the React
    # confirmation ONLY (in-process, never to the LLM — invariant 6/13).
    driveFoldersListed = Signal(str)
    actionResolved = Signal(str)

    # Asana board picker round-trip (M4). The bridge re-emits this to React.
    #   asanaProjectsListed: JSON {request_id, projects:[{gid,name}]}
    #     or {request_id, asana_not_connected:True} when no shared PAT is set.
    # Board NAMES are OPERATIONAL metadata (project trackers/roadmaps), NOT
    # patient PHI like Drive folder names — so unlike driveFoldersListed they can
    # also flow into the resolve notify (see resolve_asana_board). They still load
    # in-process into React for the picker confirmation.
    asanaProjectsListed = Signal(str)

    # Guru publish-target picker round-trip (M5). The bridge re-emits this to React.
    #   guruTargetsListed: JSON {request_id, level:'collections'|'folders',
    #     collection_id?, items:[{id,name}]} | {request_id, guru_not_connected:True}.
    # Guru collection/folder names are OPERATIONAL KB metadata (the knowledge base's
    # own structure), NOT patient PHI like Drive folder names — so like the Asana
    # boards they can also flow into the resolve notify (see resolve_guru_target).
    # They still load in-process into React for the picker confirmation.
    guruTargetsListed = Signal(str)

    # Busy-queue drain announcement (stop/queue review wave, FIX 4). Emitted
    # with the EXACT text the drain just dispatched, so the web surfaces
    # un-badge precisely the bubble that ran. The bridge re-emits it as
    # ``queuedDispatched``. Never inferred from busy alone: the queue also
    # carries [SYSTEM] triggers and other-surface sends with no JS bubble.
    queued_dispatched = Signal(str)

    def __init__(self, db=None, demo: bool = False, parent=None,
                 confirm_host=None):
        super().__init__(parent)
        self.db = db
        self.demo = demo
        # THE NATIVE PUBLISH GATE. Duck-typed: an object with
        # ``confirm_publish(payload) -> bool`` that opens a real Qt dialog
        # showing the exact bytes and the resolved target (see
        # ``src.ui.web.chat_bridge.PublishConfirmHost``). Injected from the UI
        # layer because src/services must not import src/ui — and ABSENT MEANS
        # NO PUBLISH: approve_draft refuses rather than writing to Guru through
        # a channel no human was proved to have seen.
        self._confirm_host = confirm_host
        self._engine = None
        self._warm_bridge = None
        self._claude_client = None
        self._session_id = None
        self._voice = None
        # M2 connect-Google single-flight guard. One worker per real click; the
        # QWebChannel is the trust boundary (any webview script can call
        # connectGoogle), so this controller-side latch is the enforcement that a
        # double/forged invoke spins up exactly ONE OAuth flow (invariant 16).
        self._google_connect_pending = False
        self._google_worker = None   # stored on self — a GC'd QThread mid-run crashes (inv. 9)
        # M3 Drive-list worker, stored on self for the same no-GC reason (inv. 6/9).
        # list_drive_folders runs the Drive HTTP off-thread; the worker lives here
        # until its finished slot fires on the main thread.
        self._drive_list_worker = None
        # M4 Asana-list worker, stored on self for the same no-GC reason (inv. 6/9).
        # list_asana_projects_for_picker runs the Asana HTTP off-thread; the worker
        # lives here until its finished slot fires on the main thread.
        self._asana_list_worker = None
        # M5 Guru-list worker, stored on self for the same no-GC reason (inv. 6/9).
        # list_guru_targets runs the Guru HTTP off-thread; the worker lives here
        # until its finished slot fires on the main thread.
        self._guru_list_worker = None
        # M7b gated-write workers, stored on self for the same no-GC reason
        # (inv. 6/9). WS1-M6: a SET, not a single attribute — a second Confirm
        # while a slow write runs must not drop the only reference to a running
        # QThread (the documented mid-run-GC crash). execute_write spawns AFTER
        # mark_resolved (single-winner) so one Confirm click = one live write.
        self._write_workers = set()
        # M0 busy-queue: triggers that must run as a normal Renn turn but arrived
        # while the engine was mid-turn. ``enqueue_trigger`` sends immediately when
        # idle, else appends here; one is drained on each ``busy_changed(False)``.
        # (Infra for the M3 picker→resolve path; landed + unit-tested now.)
        self._pending_triggers: list[str] = []
        # THE APPROVAL LEDGER — draft_id -> the ``approval_fingerprint`` of
        # exactly what ``pending_drafts`` last put on screen for it. Written
        # ONLY by pending_drafts (the render), read ONLY by approve_draft (the
        # click), and consumed there so one review authorizes one publish. A
        # draft id is not an authorization; this is.
        self._draft_reviews: dict[int, str] = {}
        # M5 one-shot "here's your day" greeting, ported to the Agent surface
        # (the surface the operator actually chats on).
        self._greeting_sent = False
        self._greeting_block = None
        self._setup_engine()
        self._setup_voice()
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, self._open_greeting)

    @property
    def engine(self):
        return self._engine

    def set_confirm_host(self, host) -> None:
        """Inject the native publish-confirmation host (UI layer, main thread).

        Called once from ``MainWindow`` after the page exists. Until then — and
        in any build where it never happens — ``approve_draft`` fails closed."""
        self._confirm_host = host

    @property
    def voice(self):
        return self._voice

    def _setup_voice(self):
        try:
            from src.services.voice import VoiceController
            self._voice = VoiceController(parent=self)
        except Exception as exc:  # noqa: BLE001 — dictation is optional; chat still works
            logger.debug("voice controller unavailable: %s", exc)
            self._voice = None

    def send(self, text: str):
        """Ensure a session + provider, then send. The ACP bridge boots lazily
        on the first send, so the provider is wired here, not at construction."""
        if self._engine is None:
            return
        self._ensure_session()
        self._persist_message("user", text or "")   # so history has the transcript + a title
        self._prepare_provider()
        self._engine.send(text or "")

    def _on_run_stopped(self) -> None:
        """Persist the stop marker as an assistant row (FIX 2c).

        Mirrors what ``_on_worker_error`` already stamped into the engine's
        in-memory history, so a reloaded transcript replays coherently.
        One row per stopped turn by construction: ``run_stopped`` fires
        exactly once per stop (the engine consumes ``_stop_requested`` before
        emitting) and the telemetry-callback persistence path
        (``_persist_turn``) runs only on ``_on_worker_finished`` — never on
        the stop path — so this is the marker row's ONLY writer."""
        from src.services.chat_engine import RUN_STOPPED_MARKER
        self._persist_message("assistant", RUN_STOPPED_MARKER)

    def _persist_turn(self, role, content, telemetry):
        """Telemetry callback (the assistant turn) → persist with model/tokens."""
        tel = telemetry if isinstance(telemetry, dict) else {}
        self._persist_message(
            role, content or "",
            model_used=tel.get("model_used"),
            tokens_in=tel.get("tokens_in"), tokens_out=tel.get("tokens_out"),
            cost_usd=tel.get("cost_usd"), latency_ms=tel.get("latency_ms"))

    def _persist_message(self, role, content, **kw):
        """Append one message row to chat_messages in the session's DB (best-effort)."""
        if not self._session_id:
            return
        conn = self._open_conn(readonly=False)
        if conn is None:
            return
        try:
            from src.services.chat_session import append_message
            append_message(self._session_id, role, content, conn=conn, **kw)
        except Exception as exc:  # noqa: BLE001 — telemetry only; the chat still works
            logger.debug("agent message persist failed: %s", exc)
        finally:
            conn.close()

    def shutdown(self):
        self._teardown_warm_bridge()
        self._teardown_claude_client()

    def recent_tool_calls(self, since_id: int = 0) -> list[dict]:
        """Newly-finished tool executions for this session (the tool-call
        timeline). Reads the same DB the MCP server writes to (``db_path``), so
        it sees tools as the subprocess records them. Best-effort."""
        db_path = self._db_path()
        if not self._session_id or not db_path:
            return []
        try:
            from src.data.connection_factory import get_connection
            conn = get_connection(db_path, readonly=True)
            try:
                rows = conn.execute(
                    "SELECT rowid, tool_name, result_rows, elapsed_ms, error "
                    "FROM chat_tool_executions WHERE session_id=? AND rowid>? "
                    "ORDER BY rowid", (self._session_id, int(since_id))).fetchall()
            finally:
                conn.close()
            return [{"id": r[0], "name": r[1], "rows": r[2],
                     "ms": round(r[3] or 0), "ok": not r[4], "error": r[4]}
                    for r in rows]
        except Exception:  # noqa: BLE001 — the timeline is best-effort, never fatal
            return []

    def recent_jobs(self) -> list[dict]:
        """Current jobs for this session (the sidebar tracker, M4). Reads the
        same DB the MCP server writes jobs to — best-effort, session-scoped."""
        if not self._session_id:
            return []
        conn = self._open_conn(readonly=True)
        if conn is None:
            return []
        try:
            from src.data.agent_jobs import list_jobs
            return list_jobs(conn, session_id=self._session_id, limit=50)
        except Exception:  # noqa: BLE001 — the tracker is best-effort, never fatal
            return []
        finally:
            conn.close()

    # ── action channel (M0) ─────────────────────────────────────────

    def poll_action_requests(self, session_id: str | None = None) -> list[dict]:
        """The ``action_api``: atomically claim this session's unconsumed action
        requests (picker/connect asks Renn minted in the MCP subprocess) and
        return them. Opens its OWN ``get_connection`` (read-write, since the claim
        UPDATEs) on ``_db_path`` — the same DB the resolver tools write to — so it
        sees rows across the subprocess boundary. Best-effort, session-scoped.

        Defaults to the active session so the bridge's free-running timer can call
        it with no args; an explicit ``session_id`` is accepted for tests.
        """
        sid = session_id or self._session_id
        if not sid:
            return []
        conn = self._open_conn(readonly=False)
        if conn is None:
            return []
        try:
            from src.data.chat_action_requests import claim_pending_actions
            return claim_pending_actions(conn, sid)
        except Exception:  # noqa: BLE001 — the action channel is best-effort
            return []
        finally:
            conn.close()

    def enqueue_trigger(self, text: str) -> None:
        """Run ``text`` as a normal Renn turn — now if idle, else queued.

        The picker→resolve path (M3+) calls this to push a ``[SYSTEM: operator
        selected …]`` follow-up after persisting the pick. ``ChatEngine.send``
        silently early-returns while busy, so a naive send during an in-flight
        turn would dead-end the chat; we queue instead and drain one on the next
        ``busy_changed(False)`` (invariant 7).
        """
        if self._engine is None:
            return
        if self._engine.is_busy:
            self._pending_triggers.append(text or "")
            return
        self.send(text or "")

    def queue_user_message(self, text: str) -> str:
        """The composer's send while Renn may be busy: dispatch now when idle
        ('sent'), else park it on the busy-queue to run as its own turn when
        the engine frees up ('queued'). Same queue as ``enqueue_trigger``, so
        drain order interleaves fairly with system follow-ups."""
        if self._engine is None:
            return "error"
        if self._engine.is_busy:
            self._pending_triggers.append(text or "")
            return "queued"
        self.send(text or "")
        return "sent"

    def stop_run(self) -> bool:
        """Abort Renn's in-flight turn (the Stop control). Delegates to the
        engine; False when idle or when the active client can't abort."""
        if self._engine is None or not hasattr(self._engine, "stop"):
            return False
        try:
            return bool(self._engine.stop())
        except Exception:  # noqa: BLE001 — a failed stop must never crash the chat
            return False

    def _on_busy_changed(self, busy: bool) -> None:
        """Drain ONE queued trigger when the engine goes idle. One-at-a-time so
        each follow-up runs as its own turn (and re-queues correctly if another
        arrives mid-turn).

        The drain is DEFERRED (queued dispatch), never run inside this
        ``busy_changed(False)`` delivery: a synchronous ``send`` here makes the
        new turn's ``busy_changed(True)`` reach later slots BEFORE the False
        they are still processing, leaving the web UI showing not-busy during a
        live run with its poll timer stopped."""
        if busy or not self._pending_triggers:
            return
        from PySide6.QtCore import QCoreApplication, QTimer
        if QCoreApplication.instance() is not None:
            QTimer.singleShot(0, self._drain_pending_trigger)
        else:
            self._drain_pending_trigger()

    def _drain_pending_trigger(self) -> None:
        """Run the head of the busy-queue if the engine is still idle. If a
        turn started between the schedule and now, leave the queue intact and
        wait for the next ``busy_changed(False)``. After dispatch the text is
        announced on ``queued_dispatched`` so the web UI un-badges exactly the
        bubble that ran (FIX 4)."""
        if not self._pending_triggers or self._engine is None:
            return
        if self._engine.is_busy:
            return
        text = self._pending_triggers.pop(0)
        self.send(text)
        self.queued_dispatched.emit(text)

    # ── Drive folder picker round-trip (M3) ─────────────────────────

    def list_drive_folders(self, parent_id: str = "", request_id: str = "") -> None:
        """The ``picker_api``: list the Drive folders under ``parent_id`` for the
        lazy tree, OFF the main thread.

        MUST NOT call DriveReader synchronously here — that would freeze the Qt
        event loop for the duration of the Drive HTTP (invariant 6). Instead:
          1. If Google isn't active this session, emit
             ``{request_id, needs_connect:True}`` and return — NO Drive HTTP, no
             ``_build_service`` (disable-on-launch; the picker offers Connect).
          2. Else spawn a short-lived ``DriveListWorker`` (stored on self so a
             mid-run QThread isn't GC'd) whose ``finished`` slot (queued → main
             thread) emits ``driveFoldersListed`` with id+name+driveId only.
        """
        rid = request_id or ""
        if not self._google_is_active():
            self._emit_drive_folders({"request_id": rid, "needs_connect": True})
            return
        try:
            worker = DriveListWorker(rid, parent_id or "", parent=self)
            worker.finished.connect(self._on_drive_folders_listed,
                                    Qt.ConnectionType.QueuedConnection)
            worker.failed.connect(self._on_drive_list_failed,
                                  Qt.ConnectionType.QueuedConnection)
            self._drive_list_worker = worker
            worker.start()
        except Exception as exc:  # noqa: BLE001 — never crash the chat on a wiring fault
            logger.warning("Drive list worker failed to start: %s", exc)
            self._drive_list_worker = None
            self._emit_drive_folders({"request_id": rid, "folders": [],
                                      "error": "drive_list_failed"})

    def _google_is_active(self) -> bool:
        """Whether Drive read can run right now (the disable-on-launch gate).

        auth_type-aware: on ``oauth_user`` this is still the per-session
        reconnect flag, but a service-account install is reachable without any
        Reconnect — gating it on ``google_oauth.is_active()`` alone left the
        picker permanently answering ``needs_connect``. Cheap: no Drive service
        is built and no credentials are loaded, so no Google HTTP."""
        try:
            from src.data.google_access import google_access_ready
            return google_access_ready()
        except Exception:  # noqa: BLE001 — treat any failure as "not active"
            return False

    @Slot(str, str, list)
    def _on_drive_folders_listed(self, request_id, parent_id, folders) -> None:
        """Main-thread: relay the worker's folder rows out as driveFoldersListed."""
        self._drive_list_worker = None
        self._emit_drive_folders({"request_id": request_id or "",
                                  "parent_id": parent_id or "",
                                  "folders": list(folders or [])})

    @Slot(str, str)
    def _on_drive_list_failed(self, request_id, msg) -> None:
        """Main-thread: surface a Drive-list failure (no PHI; short message)."""
        self._drive_list_worker = None
        self._emit_drive_folders({"request_id": request_id or "",
                                  "folders": [], "error": msg or "drive_list_failed"})

    def _emit_drive_folders(self, payload: dict) -> None:
        import json
        try:
            self.driveFoldersListed.emit(json.dumps(payload, default=str))
        except Exception:  # noqa: BLE001 — best-effort
            pass

    def resolve_drive_folder(self, request_id: str, folder_id: str,
                             folder_name: str = "", drive_id: str = "") -> dict:
        """Commit the operator's Drive folder pick (MAIN thread), in strict order:

          (a) VALIDATE — the request_id row must exist, be a drive_folder_picker,
              AND belong to the CURRENT session (session-switch guard, invariant 8).
          (b) PERSIST FIRST — write ``enablement.drive.active_folders`` via
              ``_set_drive_folder_impl`` (settings is the source of truth — inv. 7).
              On failure, return early WITHOUT resolving so the operator can retry;
              the human-gate stays closed throughout (no TOCTOU window).
          (c) RESOLVE single-winner — ``mark_resolved`` must win ONLY after the
              choice is durable; a double-pick is rejected (already resolved).
          (d) NOTIFY id-only — append a ``[SYSTEM]`` line + enqueue the follow-up
              trigger carrying ONLY the folder_id; the NAME/drive_id never enter the
              transcript or the next prompt (PHI — invariants 7, 13). Busy-queued.
          (e) EMIT actionResolved → React closes the picker.
        """
        rid = (request_id or "").strip()
        fid = (folder_id or "").strip()
        if not rid or not fid:
            return {"ok": False, "error": "request_id_and_folder_id_required"}
        conn = self._open_conn(readonly=False)
        if conn is None:
            return {"ok": False, "error": "no_db"}
        try:
            from src.data.chat_action_requests import get_action_request, mark_resolved
            # (a) validate row / type / session ownership (invariant 8).
            row = get_action_request(conn, rid)
            if row is None or row.get("type") != "drive_folder_picker":
                return {"ok": False, "error": "unknown_request"}
            if row.get("session_id") != self._session_id:
                return {"ok": False, "error": "session_mismatch"}
            # (b) PERSIST FIRST (invariant 7) — settings is the durable source of
            #     truth. The human-gate (has_pending_action -> resolved=0) stays
            #     CLOSED throughout this write, so a concurrent set_drive_folder
            #     cannot slip a guessed id through the window. If persist fails the
            #     row is left resolved=0 so the operator can retry (no ghost).
            from src.data.chat_tools.enablement_tools import _set_drive_folder_impl
            persisted = _set_drive_folder_impl(conn, fid, folder_name or None,
                                               drive_id or None)
            if not persisted.get("ok"):
                # React surfaces resolve failures weakly — log so a silent
                # "Use this did nothing" leaves forensics (2026-07-22 incident).
                logger.warning("resolve_drive_folder: persist failed: %s",
                               persisted.get("error"))
                return {"ok": False,
                        "error": persisted.get("error", "persist_failed")}
            # (c) only now mark resolved — single-winner dedupe + closes the gate,
            #     AFTER the choice is durable.
            if not mark_resolved(conn, rid):
                return {"ok": False, "error": "already_resolved"}
        except Exception as exc:  # noqa: BLE001
            logger.warning("resolve_drive_folder failed: %s", exc)
            return {"ok": False, "error": str(exc)}
        finally:
            conn.close()
        # (d) NOTIFY id-only — the name/drive_id MUST NOT appear (invariant 13).
        self._notify_renn(
            f"[SYSTEM: operator selected the active Drive folder (id {fid}); "
            "it is saved.]")
        self.enqueue_trigger("Confirm the active folder is set and ask what to do next.")
        # (e) tell React to close the picker.
        self._emit_action_resolved(rid)
        return {"ok": bool(persisted.get("ok")), "request_id": rid, "folder_id": fid}

    def _emit_action_resolved(self, request_id: str) -> None:
        import json
        try:
            self.actionResolved.emit(json.dumps({"request_id": request_id or ""}))
        except Exception:  # noqa: BLE001 — best-effort
            pass

    # ── Asana board picker round-trip (M4) ──────────────────────────

    def list_asana_projects_for_picker(self, request_id: str = "") -> None:
        """The ``picker_api`` for Asana: list the projects/boards the shared PAT
        can see, OFF the main thread (mirrors ``list_drive_folders``).

        MUST NOT call AsanaClient synchronously here — even though it's stdlib
        urllib, the HTTP blocks and would freeze the Qt event loop while the picker
        opens (invariant 6). Instead spawn a short-lived ``AsanaListWorker`` (stored
        on self so a mid-run QThread isn't GC'd) whose ``finished`` slot (queued →
        main thread) emits ``asanaProjectsListed`` with gid+name only. No Drive-
        style is_active() gate: Asana auth is the shared PAT (no per-session OAuth),
        so the worker itself reports ``asana_not_connected`` when no PAT is set.
        """
        rid = request_id or ""
        try:
            worker = AsanaListWorker(rid, parent=self)
            worker.finished.connect(self._on_asana_projects_listed,
                                    Qt.ConnectionType.QueuedConnection)
            worker.notConnected.connect(self._on_asana_not_connected,
                                        Qt.ConnectionType.QueuedConnection)
            worker.failed.connect(self._on_asana_list_failed,
                                  Qt.ConnectionType.QueuedConnection)
            self._asana_list_worker = worker
            worker.start()
        except Exception as exc:  # noqa: BLE001 — never crash the chat on a wiring fault
            logger.warning("Asana list worker failed to start: %s", exc)
            self._asana_list_worker = None
            self._emit_asana_projects({"request_id": rid, "projects": [],
                                       "error": "asana_list_failed"})

    @Slot(str, list)
    def _on_asana_projects_listed(self, request_id, projects) -> None:
        """Main-thread: relay the worker's project rows out as asanaProjectsListed."""
        self._asana_list_worker = None
        self._emit_asana_projects({"request_id": request_id or "",
                                   "projects": list(projects or [])})

    @Slot(str)
    def _on_asana_not_connected(self, request_id) -> None:
        """Main-thread: the shared PAT isn't set — let the picker offer a Settings hint."""
        self._asana_list_worker = None
        self._emit_asana_projects({"request_id": request_id or "",
                                   "asana_not_connected": True})

    @Slot(str, str)
    def _on_asana_list_failed(self, request_id, msg) -> None:
        """Main-thread: surface an Asana-list failure (no PHI; short message)."""
        self._asana_list_worker = None
        self._emit_asana_projects({"request_id": request_id or "",
                                   "projects": [], "error": msg or "asana_list_failed"})

    def _emit_asana_projects(self, payload: dict) -> None:
        import json
        try:
            self.asanaProjectsListed.emit(json.dumps(payload, default=str))
        except Exception:  # noqa: BLE001 — best-effort
            pass

    def resolve_asana_board(self, request_id: str, project_gid: str,
                            project_name: str = "") -> dict:
        """Commit the operator's Asana board pick (MAIN thread), in the SAME strict
        order as ``resolve_drive_folder`` (the M3 template, post-fix):

          (a) VALIDATE — the request_id row must exist, be an asana_board_picker,
              AND belong to the CURRENT session (session-switch guard, invariant 8).
          (b) PERSIST FIRST — write ``enablement.asana.active_board`` via
              ``_set_asana_board_impl`` (settings is the source of truth — inv. 7).
              On failure, return early WITHOUT mark_resolved so the operator can
              retry; the human-gate stays closed throughout (no TOCTOU window /
              data loss). This deliberate ordering is M3's just-fixed bug fix —
              DO NOT mark_resolved before the persist succeeds.
          (c) RESOLVE single-winner — ``mark_resolved`` wins ONLY after the choice
              is durable; a double-pick is rejected (already resolved).
          (d) NOTIFY + enqueue the follow-up trigger.
          (e) EMIT actionResolved → React closes the picker.

        NOTE ON REDACTION (invariant 13): unlike ``resolve_drive_folder``, the
        [SYSTEM] notify here MAY include the ``project_name``. Asana board names are
        OPERATIONAL metadata (project trackers / roadmaps), NOT patient PHI like
        Drive/Shared-Drive folder names (which can be case/patient names). Including
        the name gives Renn a natural confirmation. This is a deliberate distinction
        from the Drive path — the board gid is still the load-bearing identifier.
        """
        rid = (request_id or "").strip()
        gid = (project_gid or "").strip()
        if not rid or not gid:
            return {"ok": False, "error": "request_id_and_project_gid_required"}
        conn = self._open_conn(readonly=False)
        if conn is None:
            return {"ok": False, "error": "no_db"}
        try:
            from src.data.chat_action_requests import get_action_request, mark_resolved
            # (a) validate row / type / session ownership (invariant 8).
            row = get_action_request(conn, rid)
            if row is None or row.get("type") != "asana_board_picker":
                return {"ok": False, "error": "unknown_request"}
            if row.get("session_id") != self._session_id:
                return {"ok": False, "error": "session_mismatch"}
            # (b) PERSIST FIRST (invariant 7) — settings is the durable source of
            #     truth. The human-gate (has_pending_action -> resolved=0) stays
            #     CLOSED throughout this write. If persist fails the row is left
            #     resolved=0 so the operator can retry (no ghost / no data loss).
            from src.data.chat_tools.enablement_tools import _set_asana_board_impl
            persisted = _set_asana_board_impl(conn, gid, project_name or None)
            if not persisted.get("ok"):
                return {"ok": False,
                        "error": persisted.get("error", "persist_failed")}
            # (c) only now mark resolved — single-winner dedupe + closes the gate,
            #     AFTER the choice is durable.
            if not mark_resolved(conn, rid):
                return {"ok": False, "error": "already_resolved"}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}
        finally:
            conn.close()
        # (d) NOTIFY — the board NAME is operational metadata (not PHI), so we may
        #     include it for a natural confirmation (see the redaction note above).
        name = (project_name or "").strip()
        if name:
            self._notify_renn(
                f'[SYSTEM: operator set the active Asana board to "{name}" '
                f"(gid {gid}); it is saved.]")
        else:
            self._notify_renn(
                f"[SYSTEM: operator set the active Asana board (gid {gid}); "
                "it is saved.]")
        self.enqueue_trigger(
            "Confirm the active Asana board is set, then list its tasks with "
            "list_asana_tasks and report what's on the board.")
        # (e) tell React to close the picker.
        self._emit_action_resolved(rid)
        return {"ok": bool(persisted.get("ok")), "request_id": rid, "project_gid": gid}

    # ── Guru publish-target picker round-trip (M5) ──────────────────

    def list_guru_targets(self, collection_id: str = "", request_id: str = "") -> None:
        """The ``picker_api`` for Guru: list the operator's publish targets, OFF the
        main thread (mirrors ``list_asana_projects_for_picker``).

        Two-level — an EMPTY ``collection_id`` lists the COLLECTIONS; a non-empty
        one lists THAT collection's FOLDERS (so the React drawer drills in). MUST NOT
        call GuruClient synchronously here — its HTTP blocks and would freeze the Qt
        event loop while the picker opens (invariant 6). Instead spawn a short-lived
        ``GuruListWorker`` (stored on self so a mid-run QThread isn't GC'd) whose
        ``finished`` slot (queued → main thread) emits ``guruTargetsListed`` with
        id+name only. No is_active() gate: Guru auth is shared creds (no per-session
        OAuth), so the worker itself reports ``guru_not_connected`` when creds are
        missing.
        """
        rid = request_id or ""
        cid = collection_id or ""
        try:
            worker = GuruListWorker(rid, cid, parent=self)
            worker.finished.connect(self._on_guru_targets_listed,
                                    Qt.ConnectionType.QueuedConnection)
            worker.notConnected.connect(self._on_guru_not_connected,
                                        Qt.ConnectionType.QueuedConnection)
            worker.failed.connect(self._on_guru_list_failed,
                                  Qt.ConnectionType.QueuedConnection)
            self._guru_list_worker = worker
            worker.start()
        except Exception as exc:  # noqa: BLE001 — never crash the chat on a wiring fault
            logger.warning("Guru list worker failed to start: %s", exc)
            self._guru_list_worker = None
            self._emit_guru_targets({"request_id": rid, "items": [],
                                     "error": "guru_list_failed"})

    @Slot(str, str, str, list)
    def _on_guru_targets_listed(self, request_id, level, collection_id, items) -> None:
        """Main-thread: relay the worker's target rows out as guruTargetsListed."""
        self._guru_list_worker = None
        payload = {"request_id": request_id or "", "level": level or "collections",
                   "items": list(items or [])}
        if collection_id:
            payload["collection_id"] = collection_id
        self._emit_guru_targets(payload)

    @Slot(str)
    def _on_guru_not_connected(self, request_id) -> None:
        """Main-thread: Guru creds aren't set — let the picker offer a Settings hint."""
        self._guru_list_worker = None
        self._emit_guru_targets({"request_id": request_id or "",
                                 "guru_not_connected": True})

    @Slot(str, str)
    def _on_guru_list_failed(self, request_id, msg) -> None:
        """Main-thread: surface a Guru-list failure (no PHI; short message)."""
        self._guru_list_worker = None
        self._emit_guru_targets({"request_id": request_id or "",
                                 "items": [], "error": msg or "guru_list_failed"})

    def _emit_guru_targets(self, payload: dict) -> None:
        import json
        try:
            self.guruTargetsListed.emit(json.dumps(payload, default=str))
        except Exception:  # noqa: BLE001 — best-effort
            pass

    def resolve_guru_target(self, request_id: str, collection_id: str,
                            folder_id: str = "") -> dict:
        """Commit the operator's Guru publish-target pick (MAIN thread), in the SAME
        strict order as ``resolve_asana_board`` / ``resolve_drive_folder`` (the M3
        template, post-fix):

          (a) VALIDATE — the request_id row must exist, be a guru_publish_picker,
              AND belong to the CURRENT session (session-switch guard, invariant 8).
          (b) PERSIST FIRST — write ``enablement.guru.publish_collection_id`` (+
              optional ``publish_folder_id``) via ``_set_guru_publish_target_impl``
              (settings is the source of truth — inv. 7; these are the exact ids
              ``_push_guru_draft_impl`` already reads as its fallback target). On
              failure, return early WITHOUT mark_resolved so the operator can retry;
              the human-gate stays closed throughout (no TOCTOU window / data loss).
              This deliberate ordering is M3's just-fixed bug fix — DO NOT
              mark_resolved before the persist succeeds.
          (c) RESOLVE single-winner — ``mark_resolved`` wins ONLY after the choice
              is durable; a double-pick is rejected (already resolved).
          (d) NOTIFY + enqueue the follow-up trigger.
          (e) EMIT actionResolved → React closes the picker.

        NOTE ON REDACTION (invariant 13, source-aware): unlike
        ``resolve_drive_folder``, the [SYSTEM] notify here MAY include the collection/
        folder ids. Guru collection/folder NAMES are OPERATIONAL KB metadata (the
        knowledge base's own structure), NOT patient PHI like Drive/Shared-Drive
        folder names (which can mirror case/patient files). Same source-aware
        distinction the Asana board path makes — the enablement lane is decoupled
        from the PHI ticket warehouse, so Guru KB structure carries no patient
        records. We notify with the ids (the load-bearing identifiers
        ``_push_guru_draft_impl`` consumes); the picker already showed the names.
        """
        rid = (request_id or "").strip()
        cid = (collection_id or "").strip()
        fid = (folder_id or "").strip()
        if not rid or not cid:
            return {"ok": False, "error": "request_id_and_collection_id_required"}
        conn = self._open_conn(readonly=False)
        if conn is None:
            return {"ok": False, "error": "no_db"}
        try:
            from src.data.chat_action_requests import get_action_request, mark_resolved
            # (a) validate row / type / session ownership (invariant 8).
            row = get_action_request(conn, rid)
            if row is None or row.get("type") != "guru_publish_picker":
                return {"ok": False, "error": "unknown_request"}
            if row.get("session_id") != self._session_id:
                return {"ok": False, "error": "session_mismatch"}
            # (b) PERSIST FIRST (invariant 7) — settings is the durable source of
            #     truth. The human-gate (has_pending_action -> resolved=0) stays
            #     CLOSED throughout this write. If persist fails the row is left
            #     resolved=0 so the operator can retry (no ghost / no data loss).
            from src.data.chat_tools.enablement_tools import _set_guru_publish_target_impl
            persisted = _set_guru_publish_target_impl(conn, cid, fid or None)
            if not persisted.get("ok"):
                return {"ok": False,
                        "error": persisted.get("error", "persist_failed")}
            # (c) only now mark resolved — single-winner dedupe + closes the gate,
            #     AFTER the choice is durable.
            if not mark_resolved(conn, rid):
                return {"ok": False, "error": "already_resolved"}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}
        finally:
            conn.close()
        # (d) NOTIFY — the collection/folder ids are operational KB metadata (not
        #     PHI), so we include them for a natural confirmation (see the redaction
        #     note above). _push_guru_draft_impl reads exactly these ids.
        if fid:
            self._notify_renn(
                f"[SYSTEM: operator set the Guru publish target to collection "
                f"{cid}, folder {fid}; it is saved.]")
        else:
            self._notify_renn(
                f"[SYSTEM: operator set the Guru publish target to collection "
                f"{cid} (collection level); it is saved.]")
        self.enqueue_trigger(
            "Confirm the Guru publish target is set and ask what to publish next.")
        # (e) tell React to close the picker.
        self._emit_action_resolved(rid)
        return {"ok": bool(persisted.get("ok")), "request_id": rid,
                "collection_id": cid, "folder_id": fid or None}

    # ── gated WRITE channel (M7b) — Confirm/Cancel a non-idempotent write ──

    def execute_write(self, request_id: str) -> dict:
        """Commit a confirm_write the operator clicked Confirm on (MAIN thread).

        ORDER IS THE INVERSE OF THE PICKERS' resolve_*, AND DELIBERATELY SO. A
        picker's resolve persists-to-settings FIRST then mark_resolved, because a
        re-pick of the same folder/board is idempotent — re-writing the setting is
        harmless, so it's safe to do the durable write before claiming the row.
        A confirm_write is the opposite: the write is a NON-IDEMPOTENT live API
        call (creating a Guru folder twice = two folders; creating an Asana task
        twice = two tasks). So we mark_resolved FIRST — that single ``UPDATE …
        WHERE resolved=0`` is the single-winner CLAIM. Only the call that wins the
        claim spawns the worker; a double-confirm (double click / replayed bridge
        call) loses the claim (already_resolved) and NEVER reaches the write. One
        click = one write.

        Steps:
          (a) VALIDATE — the row exists, is a confirm_write, and belongs to the
              CURRENT session (session-switch guard, invariant 8).
          (b) CLAIM FIRST — ``mark_resolved`` (single-winner). If it loses, return
              already_resolved and do NOT execute (the inverse-of-the-picker order
              that makes the non-idempotent write fire exactly once).
          (c) read op + params from the row payload.
          (d) spawn the off-thread WriteWorker; its finished/failed slot notifies
              Renn with the operational result + emits actionResolved.
        """
        rid = (request_id or "").strip()
        if not rid:
            return {"ok": False, "error": "request_id_required"}
        conn = self._open_conn(readonly=False)
        if conn is None:
            return {"ok": False, "error": "no_db"}
        try:
            import json
            from src.data.chat_action_requests import get_action_request, mark_resolved
            # (a) validate row / type / session ownership (invariant 8).
            row = get_action_request(conn, rid)
            if row is None or row.get("type") != "confirm_write":
                return {"ok": False, "error": "unknown_request"}
            if row.get("session_id") != self._session_id:
                return {"ok": False, "error": "session_mismatch"}
            # (a2) read the payload BEFORE the claim so a per-op pre-dispatch
            #      check can run (a cheap READ) without burning the row.
            try:
                payload = json.loads(row.get("payload_json") or "{}")
            except (ValueError, TypeError):
                payload = {}
            op = payload.get("op") or ""
            params = payload.get("params") or {}
            # Pre-dispatch check (WS1-M6 hook): a dict result BLOCKS the write
            # WITHOUT mark_resolved — the card stays open, the operator fixes
            # the precondition (e.g. reconnect Google) and clicks Confirm again.
            pre_check = PRE_DISPATCH_CHECKS.get(op)
            if pre_check is not None:
                try:
                    blocked = pre_check(self, params)
                except Exception as exc:  # noqa: BLE001 — a broken check never blocks
                    logger.debug("pre-dispatch check failed for %s: %s", op, exc)
                    blocked = None
                if blocked:
                    return {**blocked, "ok": False, "request_id": rid,
                            "kept_open": True}
            # (b) CLAIM FIRST (single-winner) — BEFORE the non-idempotent write.
            #     A double-confirm loses here and never executes (one click = one
            #     write). This is the inverse of the pickers' persist-first order,
            #     on purpose: the write below cannot be undone or de-duped after the
            #     fact, so the claim must gate it.
            if not mark_resolved(conn, rid):
                return {"ok": False, "error": "already_resolved"}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}
        finally:
            conn.close()
        # (d) spawn the off-thread write (the worker is stored on self — a GC'd
        #     QThread mid-run crashes). The finished/failed slot does the notify +
        #     actionResolved on the main thread.
        try:
            worker = WriteWorker(rid, op, params, parent=self,
                                 ctx={"db_path": self._db_path()})
            worker.finished.connect(self._on_write_finished,
                                    Qt.ConnectionType.QueuedConnection)
            worker.failed.connect(self._on_write_failed,
                                  Qt.ConnectionType.QueuedConnection)
            # Live-worker SET (WS1-M6 GC fix): a second Confirm while a slow
            # write runs must not drop the only reference to a running QThread.
            if not hasattr(self, "_write_workers"):
                self._write_workers = set()
            self._write_workers.add(worker)
            worker.start()
        except Exception as exc:  # noqa: BLE001 — never crash the chat on a wiring fault
            logger.warning("Write worker failed to start: %s", exc)
            self._notify_renn(
                f"[SYSTEM: the {op or 'write'} could not be started: {str(exc)[:120]}.]")
            self._emit_action_resolved_payload({"request_id": rid, "ok": False})
            return {"ok": False, "error": "write_start_failed", "request_id": rid}
        return {"ok": True, "request_id": rid, "op": op, "started": True}

    @Slot(str, bool, str)
    def _on_write_finished(self, request_id, ok, result_json) -> None:
        """Main-thread: a gated write completed. Notify Renn with the OPERATIONAL
        result (new folder id / task permalink+name / 'renamed'), enqueue any
        follow-up trigger, and tell React to close the card."""
        import json
        self._prune_write_workers()
        try:
            result = json.loads(result_json or "{}")
        except (ValueError, TypeError):
            result = {}
        op = self._notify_write_result(bool(ok), result)
        # For a new Guru folder, invite Renn to offer setting it as the publish
        # target (so the next push lands there) — the natural next step.
        if ok and op == "create_guru_folder" and result.get("id"):
            self.enqueue_trigger(
                "The new Guru folder was created. Offer to set it as the publish "
                "target with set_guru_publish_target (use the new folder id), then "
                "ask what to do next.")
        self._emit_action_resolved_payload({"request_id": request_id or "", "ok": bool(ok)})

    @Slot(str, str)
    def _on_write_failed(self, request_id, msg) -> None:
        """Main-thread: a gated write raised. Notify Renn the error + close the card."""
        self._prune_write_workers()
        self._notify_renn(f"[SYSTEM: the write failed: {msg or 'unknown error'}.]")
        self._emit_action_resolved_payload({"request_id": request_id or "", "ok": False})

    def _prune_write_workers(self) -> None:
        """Drop references to finished WriteWorkers (main thread only)."""
        workers = getattr(self, "_write_workers", None)
        if workers:
            self._write_workers = {w for w in workers if w.isRunning()}

    def _notify_write_result(self, ok: bool, result: dict) -> str:
        """Inject a [SYSTEM] line describing the operational write result. Returns
        the op so the caller can chain a follow-up. Operational ids/names only
        (KB/board metadata, not PHI — invariant 13, source-aware)."""
        # The worker's result dicts don't carry ``op``; infer it from the shape so
        # the notify reads naturally. (create_folder/rename_folder both carry
        # ok+id+title; create_task carries ok+gid+name+permalink_url.)
        if result.get("error") == "guru_not_connected":
            self._notify_renn("[SYSTEM: the write failed — Guru is not connected. "
                              "Ask the operator to connect Guru in Settings.]")
            return ""
        if result.get("error") == "asana_not_connected":
            self._notify_renn("[SYSTEM: the write failed — Asana is not connected. "
                              "Ask the operator to add a shared Asana token in Settings.]")
            return ""
        if "permalink_url" in result or "gid" in result:   # Asana task create
            if ok:
                self._notify_renn(
                    f'[SYSTEM: operator confirmed — created the Asana task '
                    f'"{result.get("name") or ""}" '
                    f'({result.get("permalink_url") or result.get("gid") or ""}).]')
            else:
                self._notify_renn("[SYSTEM: the Asana task could not be created.]")
            return "create_asana_task"
        # Guru folder create vs rename: a create resolves a *new* id; a rename
        # echoes the same folder id back. We can't perfectly distinguish from the
        # result alone, so report the durable facts (id + title).
        if ok:
            self._notify_renn(
                f'[SYSTEM: operator confirmed — the Guru folder "{result.get("title") or ""}" '
                f'is saved (id {result.get("id") or "unknown"}).]')
            return "create_guru_folder" if result.get("id") else ""
        self._notify_renn("[SYSTEM: the Guru folder write could not be completed.]")
        return ""

    def cancel_write(self, request_id: str) -> dict:
        """The operator clicked Cancel on a Confirm card (MAIN thread).

        Validate session ownership, mark_resolved (so the human-gate re-opens and
        the row can't be re-confirmed), notify Renn it was cancelled, and tell
        React to close the card. NO write ever runs.
        """
        rid = (request_id or "").strip()
        if not rid:
            return {"ok": False, "error": "request_id_required"}
        conn = self._open_conn(readonly=False)
        if conn is None:
            return {"ok": False, "error": "no_db"}
        op = ""
        try:
            import json
            from src.data.chat_action_requests import get_action_request, mark_resolved
            row = get_action_request(conn, rid)
            if row is None or row.get("type") != "confirm_write":
                return {"ok": False, "error": "unknown_request"}
            if row.get("session_id") != self._session_id:
                return {"ok": False, "error": "session_mismatch"}
            try:
                op = (json.loads(row.get("payload_json") or "{}") or {}).get("op") or ""
            except (ValueError, TypeError):
                op = ""
            if not mark_resolved(conn, rid):
                return {"ok": False, "error": "already_resolved"}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}
        finally:
            conn.close()
        self._notify_renn(f"[SYSTEM: operator cancelled the {op or 'write'}.]")
        self._emit_action_resolved_payload({"request_id": rid, "cancelled": True})
        return {"ok": True, "request_id": rid, "cancelled": True}

    def _emit_action_resolved_payload(self, payload: dict) -> None:
        """Emit actionResolved carrying a richer payload (write outcome) than the
        bare {request_id} the pickers emit — React shows a one-line outcome then
        closes the matching confirm card."""
        import json
        try:
            self.actionResolved.emit(json.dumps(payload, default=str))
        except Exception:  # noqa: BLE001 — best-effort
            pass

    # ── connect Google in chat (M2) ─────────────────────────────────

    def start_google_connect(self) -> None:
        """Begin the in-chat Google OAuth flow (the ConnectGoogleCard button).

        Single-flight (invariant 16): a second call while a flow is in flight is
        ignored, so a forged/double QWebChannel invoke spins up exactly one
        worker. The ``GoogleOAuthWorker`` runs the BLOCKING loopback PKCE flow off
        the UI thread; it is stored on ``self`` so a mid-run QThread isn't GC'd
        and crashed (invariant 9). ``finished``/``error`` are delivered back on
        the MAIN thread (queued connection) where we persist + reconnect + notify.

        Disable-on-launch (invariant 9): there is NO import-time/boot reconnect
        anywhere — ``_active`` is only ever set by the finished handler below,
        i.e. by THIS explicit click.
        """
        if self._google_connect_pending:
            return   # single-flight: one OAuth flow per click
        self._google_connect_pending = True
        self._emit_google_state("connecting")
        try:
            from src.ui.widgets.google_oauth_worker import GoogleOAuthWorker
            worker = GoogleOAuthWorker(mode="connect", parent=self)
            # Queued so the slot bodies run on the main thread (the worker emits
            # from its own QThread); we touch settings/keyring + the engine there.
            worker.finished.connect(self._on_google_connect_finished,
                                    Qt.ConnectionType.QueuedConnection)
            worker.error.connect(self._on_google_connect_error,
                                 Qt.ConnectionType.QueuedConnection)
            self._google_worker = worker
            worker.start()
        except Exception as exc:  # noqa: BLE001 — never crash the chat on a wiring fault
            logger.warning("Google connect worker failed to start: %s", exc)
            self._google_connect_pending = False
            self._google_worker = None
            self._emit_google_state("failed")

    @Slot(dict)
    def _on_google_connect_finished(self, record: dict) -> None:
        """Main-thread: persist the record, activate it THIS session, notify Renn.

        Order: store_credentials (keyring + auth_type=oauth_user) → reconnect
        (sets ``_active`` for this session only) → STATUS-ONLY notify. The
        ``[SYSTEM]`` injection carries NO token, email, or account name
        (invariant 13) — only that Drive read access is now active."""
        self._google_connect_pending = False
        self._google_worker = None
        ok = False
        try:
            from src.data import google_oauth
            if google_oauth.store_credentials(record or {}):
                # reconnect() is the ONLY setter of the live session creds; runs
                # silently (no browser). It's a quick token refresh, fine on the
                # main thread.
                ok = google_oauth.reconnect() is not None
        except Exception as exc:  # noqa: BLE001
            logger.warning("Google connect finalize failed: %s", exc)
            ok = False
        if not ok:
            self._emit_google_state("failed")
            self._notify_renn(
                "[SYSTEM: Google connection failed: the authorization could not be "
                "saved or activated. Ask the operator to try connecting again.]")
            return
        self._emit_google_state("connected")
        # STATUS ONLY — no token, no email, no name (invariant 13).
        self._notify_renn(
            "[SYSTEM: the operator connected their Google account; Drive read "
            "access is now active this session.]")
        self.enqueue_trigger("Confirm Drive is connected and ask which folder to use.")

    @Slot(str)
    def _on_google_connect_error(self, msg: str) -> None:
        """Main-thread: clear the single-flight latch + surface a redacted error.

        The worker already token-redacts ``msg`` (``_redact``), so we inject it
        as-is — no token/secret can leak through this path."""
        self._google_connect_pending = False
        self._google_worker = None
        self._emit_google_state("failed")
        self._notify_renn(f"[SYSTEM: Google connection failed: {msg}]")

    def _notify_renn(self, system_text: str) -> None:
        """Append a [SYSTEM] line to the engine history (so Renn sees the status)
        without persisting it as a user turn / starting a send. Best-effort."""
        if self._engine is None:
            return
        try:
            self._engine.append_to_history("user", system_text)
        except Exception as exc:  # noqa: BLE001 — notify is best-effort
            logger.debug("Google connect notify failed: %s", exc)

    def _emit_google_state(self, state: str, **extra) -> None:
        """Emit the connect-card state as a JSON string. STATUS ONLY — the
        payload never carries a token/email/name (invariant 13)."""
        import json
        payload = {"state": state}
        payload.update(extra)
        try:
            self.googleAuthState.emit(json.dumps(payload, default=str))
        except Exception:  # noqa: BLE001
            pass

    # ── tool-edit review / sign-off (M5) ────────────────────────────

    def pending_drafts(self, *, bind: bool = True) -> list[dict]:
        """Card drafts awaiting human sign-off before publishing to Guru (M5),
        each with a precomputed red/green diff + offline pre-flight checks for the
        in-thread review panel. Best-effort, read-only.

        The reviewed payload is derived from the PUBLISH BODY, never from the
        markdown column: ``enablement_store.review_text(draft)`` is the diff
        input (the reviewer-facing projection of ``publish_body(draft)``, which
        is the exact byte string ``publish_draft`` sends). Where that projection
        is lossy — script bodies, event handlers, attribute payloads — the item
        carries a ``notice`` naming what ships unseen, because this panel is the
        gate: ``require_approval`` defaults to 1 for every draft, so every
        chat-initiated push passes through here.

        Every item ALSO carries ``publish_body``: the exact byte string that
        will be sent, unconditionally, for every draft shape. The panel renders
        it as escaped text behind an always-present disclosure. No detector
        decides whether the operator may read what they are about to publish —
        the notice tells them they should, the bytes are there either way.

        RENDERING A DRAFT FOR A HUMAN ALSO BINDS ITS APPROVAL (2026-07-27).
        What this call puts on screen is fingerprinted into
        ``self._draft_reviews``, and ``approve_draft`` refuses to publish
        anything whose fingerprint, recomputed from the row at click time, no
        longer matches. A draft whose publish body could not be read has its
        binding DROPPED rather than recorded, so it cannot be approved at all.

        ``bind=False`` IS WHAT MAKES THAT BINDING REAL (the same day, hours
        later). ``ChatBridge._on_busy`` calls the draft poll on every
        ``busy_changed(False)`` — i.e. at the end of EVERY Renn turn — and the
        poll called this method, which silently RE-MINTED the fingerprint. Since
        ``revise_draft`` runs inside a turn, the very TOCTOU this binding exists
        to refuse was guaranteed to be re-authorized instead: traced, the
        operator's approve then shipped ``<script>``-bearing bytes they never
        saw, and removing only the poll turned the same run into
        ``draft_changed_after_review`` with nothing sent. So the background poll
        passes ``bind=False``: it refreshes the LIST, and an existing binding is
        never silently replaced. A draft whose bytes moved comes back with
        ``review_state='changed'`` — the panel says so and disables approval
        until a human re-opens the review.
        """
        conn = self._open_conn(readonly=True)
        if conn is None:
            return []
        try:
            from src.data import enablement_store as store
            from src.data.text_diff import diff_rows, change_count
            out = []
            for d in store.list_pending_approvals(conn):
                card_id = d.get("card_id") or ""
                target = store.push_target(d)
                item = {"draft_id": d.get("id"),
                        "title": d.get("title") or "Untitled",
                        "card_id": card_id, "status": d.get("status"),
                        # WHERE THIS GOES, on the panel. The approval binds the
                        # target, so the operator has to be able to see it: a
                        # re-requested push at ATTACKER_COLL/ATTACKER_FOLD used
                        # to be invisible here while remaining approvable.
                        "target": dict(target, card_id=card_id),
                        "target_label": _target_label(card_id, target),
                        # Why this draft came back, when a publish failed.
                        "failure": store.push_failure(d)}
                # The exact bytes FIRST and on their own, so the operator's
                # access to what will be sent survives a failure of everything
                # downstream (projection, diff, checks, scanner).
                try:
                    body = store.publish_body(d)      # the exact bytes that ship
                except Exception as exc:  # noqa: BLE001
                    logger.warning("draft %s: publish body unavailable: %s",
                                   d.get("id"), exc)
                    body = None
                item["publish_body"] = body or ""
                item["publish_body_available"] = body is not None
                # BIND THE APPROVAL TO THIS WHOLE ACT, before anything that can
                # fail. Bound to what was rendered — title, target card,
                # collection/folder and the exact publish body — not to the
                # draft id, and never re-minted by a background refresh.
                item["review_state"] = self._bind_review(d, body, bind=bind)
                try:
                    if body is None:
                        raise RuntimeError("publish body unavailable")
                    reviewed = store.review_text(d)   # their reviewer-facing text
                    baseline, has_baseline = self._current_card_baseline(conn, card_id)
                    rows = diff_rows(baseline, reviewed)
                    item.update({
                        "diff": rows, "change_count": change_count(rows),
                        "checks": self._draft_checks(reviewed, card_id,
                                                     publish_body=body),
                        "notice": review_notice(reviewed, body, card_id=card_id,
                                                has_baseline=has_baseline),
                    })
                except Exception as exc:  # noqa: BLE001
                    # One draft that cannot be projected must not blank the whole
                    # panel (that would hide pending pushes), and must never look
                    # like a clean, reviewed draft either.
                    logger.warning("draft %s could not be prepared for review: %s",
                                   d.get("id"), exc)
                    item.update({
                        "diff": [], "change_count": 0, "checks": [],
                        "notice": "This draft could NOT be prepared for review — "
                                  "nothing below represents what would be sent. "
                                  "Do not approve it; open it in the Workbench.",
                    })
                out.append(item)
            return out
        except Exception:  # noqa: BLE001 — best-effort, never fatal
            return []
        finally:
            conn.close()

    def _bind_review(self, draft, body, *, bind: bool = True) -> str:
        """Record what ``pending_drafts`` just rendered — and say what state the
        review is in. Returns one of:

        ``bound``       a live binding matches exactly what is on screen;
        ``changed``     a binding existed and the row has since moved, so this
                        render is NOT authorized — approval is refused until a
                        human re-opens the review (``bind=True``);
        ``unreviewed``  no binding (a background refresh never mints one);
        ``unavailable`` the publish body could not be produced.

        ``body is None`` DROPS any existing binding instead of recording one: a
        draft whose bytes cannot be shown must not be approvable, and dropping
        can only ever make an approval harder.

        AN EXISTING BINDING IS NEVER SILENTLY REPLACED. That was the whole
        defect: the end-of-turn poll re-minted, so a revision made INSIDE the
        turn — by a prompt-injected Renn calling ``revise_draft`` — was
        re-authorized before the operator's finger came off the button. When
        the bytes moved, the old binding is dropped (never spendable) and the
        panel is told ``changed``; only a human-initiated re-open mints again.
        """
        try:
            did = int(draft.get("id"))
        except (TypeError, ValueError):
            return "unavailable"
        if body is None:
            self._draft_reviews.pop(did, None)
            return "unavailable"
        from src.data.enablement_store import push_target
        current = approval_fingerprint(
            draft.get("title") or "", draft.get("card_id") or "", body,
            push_target(draft))
        previous = self._draft_reviews.get(did)
        if previous == current:
            return "bound"
        if previous is not None:
            if not bind:
                # OBSERVE, DO NOT TOUCH. A background refresh neither mints a
                # binding nor spends one: it reports that the row moved, and
                # ``approve_draft`` — which recomputes from the row itself —
                # refuses with the specific ``draft_changed_after_review``
                # rather than the generic "you never reviewed this".
                return "changed"
            # A human re-opened the review, so they ARE looking at these bytes
            # now — replace the binding, and still say it moved since last time.
            self._draft_reviews[did] = current
            return "rebound"
        if not bind:
            return "unreviewed"
        self._draft_reviews[did] = current
        return "bound"

    def _refuse_approval(self, draft_id, error: str, message: str) -> dict:
        """One refusal shape. ``published`` is stated explicitly so no caller
        can read a missing key as success."""
        logger.warning("approve_draft refused for draft %s: %s", draft_id, error)
        return {"ok": False, "draft_id": draft_id, "error": error,
                "message": message, "published": False, "refused": True}

    def approve_draft(self, draft_id) -> dict:
        """Publish the draft the operator reviewed — or refuse, and publish
        nothing.

        THE BINDING IS CHECKED FIRST, AND IT IS RECOMPUTED FROM THE ROW.
        ``pending_drafts`` fingerprinted exactly what it rendered; this reads
        the row as it stands NOW, fingerprints it again, and requires the two
        to match. Anything that moved the draft in between — most concretely
        ``update_draft_content``, which the ``revise_draft`` chat tool calls
        with no lock, no gate and no status change — refuses here. Nothing
        client-supplied enters the comparison, so there is no value a page
        script or a prompt-injected Renn can forge to satisfy it.

        The check runs BEFORE any sign-off is recorded. Recording it first and
        refusing afterwards would leave ``approved_at`` set, and the M5 gate in
        ``_push_guru_draft_impl`` would then let the model's own next push
        through unattended.

        THEN THE NATIVE CONFIRM. The binding proves the bytes did not move; it
        cannot prove a human was ever there, because ``approveDraft`` is a
        page-callable QWebChannel slot and a page script can call it (and
        ``refreshDrafts`` to mint the binding first). A Qt dialog — unreachable
        from Chromium — showing the exact bytes and the resolved target is the
        only channel that proves perception, so it runs before any state
        changes, defaults to Cancel, and its absence REFUSES.

        THEN, AND ONLY THEN, THE SIGN-OFF — and it is spent by a publish that
        SUCCEEDED, or not at all. Every other outcome (the Guru client raising,
        an ``ok:false`` result, ``publish_draft`` itself blowing up) calls
        ``revoke_approval``: ``approved_at`` goes back to NULL, the claim is
        destroyed, the push request is re-armed with the failure reason, and the
        draft reappears on this panel. Before that, a single transient 401 left
        a permanent pre-authorization AND deleted the draft from the panel, so
        the model's own next push sailed through the M5 gate unattended.

        A successful check CONSUMES the binding: one review authorizes one
        publish. The bridge refreshes the panel after every approve, so a
        legitimate retry simply re-renders and re-binds.
        """
        try:
            did = int(draft_id)
        except (TypeError, ValueError):
            return self._refuse_approval(draft_id, "draft_id_required",
                                         NO_REVIEW_REFUSAL)
        conn = self._open_conn(readonly=False)
        if conn is None:
            return {"ok": False, "draft_id": did, "error": "no_db",
                    "message": UNREADABLE_BODY_REFUSAL, "published": False,
                    "refused": True}
        try:
            from src.data import enablement_store as store
            from src.data.chat_tools.enablement_tools import _push_guru_draft_impl

            # ── the time-of-use check ───────────────────────────────
            reviewed = self._draft_reviews.get(did)
            if reviewed is None:
                return self._refuse_approval(did, "not_reviewed",
                                             NO_REVIEW_REFUSAL)
            draft = store.get_draft(conn, did)
            if draft is None:
                self._draft_reviews.pop(did, None)
                return self._refuse_approval(did, "draft_not_found",
                                             MISSING_DRAFT_REFUSAL)
            current = draft_fingerprint(draft)   # RECOMPUTED FROM THE ROW
            if current is None:
                self._draft_reviews.pop(did, None)
                return self._refuse_approval(did, "publish_body_unavailable",
                                             UNREADABLE_BODY_REFUSAL)
            if current != reviewed:
                # The row moved under the operator. Drop the binding so a
                # second click cannot retry against a stale review either —
                # only a fresh render re-authorizes.
                self._draft_reviews.pop(did, None)
                return self._refuse_approval(did, "draft_changed_after_review",
                                             STALE_REVIEW_REFUSAL)

            # ── the native confirm (the only proof of a human) ──────
            target = store.push_target(draft)
            confirmed, why = self._confirm_publish(draft, target)
            if not confirmed:
                self._draft_reviews.pop(did, None)   # a decision, not a retry
                return self._refuse_approval(
                    did, why,
                    NO_CONFIRM_HOST_REFUSAL if why == "no_confirm_host"
                    else DECLINED_REFUSAL)
            self._draft_reviews.pop(did, None)   # one review, one publish

            # ── the sign-off, scoped to exactly this act ────────────
            claim = store.record_approval(
                conn, did, approved_by=self._approver_identity(),
                fingerprint=current)
            try:
                result = _push_guru_draft_impl(
                    conn, did, target.get("collection_id") or None,
                    target.get("folder_id") or None, approval_claim=claim)
            except Exception as exc:  # noqa: BLE001 — a raise is a failed publish
                logger.warning("publish raised for draft %s: %s", did, exc)
                result = {"ok": False, "error": f"publish_failed: {exc}"}
            result = result if isinstance(result, dict) else {"ok": False}
            if result.get("ok"):
                # The sign-off is SPENT — by a publish that succeeded, and only
                # by that. Clearing the push request destroys the claim with it.
                store.clear_push_request(conn, did)
                return {"ok": True, "draft_id": did, "result": result,
                        "published": True}
            reason = str(result.get("error") or result.get("message")
                         or "the publish did not complete")[:300]
            store.revoke_approval(conn, did, reason=reason)
            logger.warning("publish failed for draft %s; sign-off rolled back: %s",
                           did, reason)
            return {"ok": False, "draft_id": did, "result": result,
                    "published": False, "refused": True,
                    "error": result.get("error") or "publish_failed",
                    "message": PUBLISH_FAILED_REFUSAL.format(reason=reason)}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "draft_id": did, "error": str(exc),
                    "published": False}
        finally:
            conn.close()

    def _confirm_publish(self, draft, target) -> tuple[bool, str]:
        """Take the operator's NATIVE confirmation for this publish.

        Returns ``(confirmed, reason)``. Fails closed on every branch that is
        not an explicit accept:

        * no host injected (``no_confirm_host``) — a build that cannot show the
          dialog cannot publish. This is not degradation-tolerance: the dialog
          IS the human, and without it ``approveDraft`` is a page-callable slot
          with a live Guru token behind it;
        * the host raising (``confirm_failed``) — an exception is not a yes;
        * the operator choosing Cancel (``declined_at_confirm``).

        The payload carries the EXACT publish bytes (never a projection) and
        the resolved target, because those two are what the sign-off commits to.
        """
        host = self._confirm_host
        if host is None or not hasattr(host, "confirm_publish"):
            logger.warning("publish refused: no native confirmation host")
            return False, "no_confirm_host"
        try:
            from src.data.enablement_store import publish_body
            body = publish_body(draft)
        except Exception as exc:  # noqa: BLE001 — no bytes to show → no publish
            logger.warning("publish refused: body unavailable at confirm: %s", exc)
            return False, "confirm_failed"
        card_id = (draft.get("card_id") or "") if hasattr(draft, "get") else ""
        payload = {
            "draft_id": draft.get("id") if hasattr(draft, "get") else None,
            "title": (draft.get("title") or "") if hasattr(draft, "get") else "",
            "publish_body": body or "",
            "card_id": card_id,
            "collection_id": (target or {}).get("collection_id") or "",
            "folder_id": (target or {}).get("folder_id") or "",
            "target_label": _target_label(card_id, target),
        }
        try:
            accepted = bool(host.confirm_publish(payload))
        except Exception as exc:  # noqa: BLE001 — a raise is never consent
            logger.warning("publish confirmation failed: %s", exc)
            return False, "confirm_failed"
        return (True, "confirmed") if accepted else (False, "declined_at_confirm")

    def reject_draft(self, draft_id) -> dict:
        """Decline a pending publish: drop the push request (the draft stays
        editable). No sign-off recorded, nothing reaches Guru."""
        conn = self._open_conn(readonly=False)
        if conn is None:
            return {"ok": False, "error": "no_db"}
        try:
            from src.data import enablement_store as store
            did = int(draft_id)
            store.clear_push_request(conn, did)
            # A declined review is spent: it must not authorize a later click.
            self._draft_reviews.pop(did, None)
            return {"ok": True, "draft_id": did, "rejected": True}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}
        finally:
            conn.close()

    def _current_card_md(self, conn, card_id: str) -> str:
        return self._current_card_baseline(conn, card_id)[0]

    def _current_card_baseline(self, conn, card_id: str) -> tuple[str, bool]:
        """(diff baseline, whether it is a REAL baseline).

        The flag is what the panel needs: a draft with no ``card_id`` creates a
        new card, so an all-additions diff is the truth; a draft that REPLACES a
        live card and has no local copy renders the same way while meaning
        something completely different, and the operator has to be told which
        one they are looking at. (The missing local cache — ``guru_cards`` may
        not exist at all — is a deferred audit finding; this only stops it from
        masquerading as a clean diff.)
        """
        if not card_id:
            return "", True   # a new card → all-additions IS the change
        try:
            row = conn.execute(
                "SELECT content FROM guru_cards WHERE card_id = ?", (card_id,)).fetchone()
        except Exception:  # noqa: BLE001 — no local cache → no baseline
            return "", False
        if row is None:
            return "", False
        try:
            return (row["content"] if hasattr(row, "keys") else row[0]) or "", True
        except Exception:  # noqa: BLE001
            return "", False

    def _draft_checks(self, text: str, card_id: str,
                      publish_body: str | None = None) -> list[dict]:
        """Pre-flight checks for the panel.

        ``text`` is the reviewed text (the publish body's projection), so the
        format/readability/style checks run on what ships rather than on the
        markdown column. ``publish_body`` adds one pass over the raw shipped
        bytes: only ``pii_scan`` is meaningful there (its patterns are
        text-level, so it catches a leak parked in an attribute or a script
        body that the projection drops); the markdown-shaped checks would just
        misread markup.
        """
        try:
            from src.data.enablement_checks import pii_scan, run_checks
            rows = run_checks(text, card_id=card_id)
            if publish_body is not None and publish_body != text:
                raw = dict(pii_scan(publish_body))
                raw["check"] = "pii_scan (bytes sent)"
                rows.append(raw)
            return rows
        except Exception:  # noqa: BLE001 — checks are advisory
            return []

    def _approver_identity(self) -> str:
        """Reuse the operator's configured identity for the audit trail; 'user'
        is the safe fallback (the approval timestamp is the load-bearing part).

        Delegates to the M1 single source of truth so the detected_email
        precedence tier is honored (no network on this hot path)."""
        try:
            from src.data import enablement_identity as ident
            from src.data.settings_manager import get_section
            who = ident.operator_email(resolve=False)
            if who:
                return who
            en = get_section("enablement", {}) or {}
            return (en.get("guru") or {}).get("email") or "user"
        except Exception:  # noqa: BLE001
            return "user"

    # ── past-chat browser (M3) ──────────────────────────────────────

    def list_sessions(self, limit: int = 30) -> list[dict]:
        """Recent enablement chat sessions for the history drawer. Scoped in SQL
        to ``source_page='enablement'`` so the Agent only surfaces its own chats
        (never product-mode ticket conversations — keeps it decoupled)."""
        conn = self._open_conn(readonly=True)
        if conn is None:
            return []
        try:
            from src.services.chat_session import list_sessions as _list
            return _list(limit=limit, conn=conn, source_page="enablement")
        except Exception:  # noqa: BLE001 — best-effort
            return []
        finally:
            conn.close()

    def search_sessions(self, query: str, limit: int = 20) -> list[dict]:
        """FTS5 search across this Agent's past chat messages. Scoped to
        ``source_page='enablement'`` so search never leaks product-mode content.
        Returns {session_id, session_title, role, content, created_at}."""
        if not (query or "").strip():
            return []
        conn = self._open_conn(readonly=True)
        if conn is None:
            return []
        try:
            from src.services.chat_session import search_messages
            return search_messages(query.strip(), limit=limit, conn=conn,
                                   source_page="enablement")
        except Exception:  # noqa: BLE001 — best-effort
            return []
        finally:
            conn.close()

    def load_session(self, session_id: str) -> dict:
        """Load a past transcript and make it the *active* session so the chat
        continues coherently: restore the engine's history, repoint the engine
        + the tools→session pointer at it. Returns {session_id, messages}."""
        conn = self._open_conn(readonly=True)
        data = {}
        if conn is not None:
            try:
                from src.services.chat_session import load_session as _load
                data = _load(session_id, conn=conn) or {}
            except Exception:  # noqa: BLE001 — best-effort
                data = {}
            finally:
                conn.close()
        # Decoupling guard: only enablement chats may become the active Agent
        # thread. Never load a product-mode (ticket) conversation — even if a
        # caller hands us its id (e.g. a stale search result).
        if data.get("source_page") != "enablement":
            return {"session_id": session_id, "messages": []}
        msgs = [
            {"role": m.get("role"), "content": m.get("content", "")}
            for m in (data.get("messages") or [])
            if m.get("role") in ("user", "assistant")
        ]
        if self._engine is not None:
            try:
                self._engine.set_history([dict(m) for m in msgs])
                self._engine.set_session_id(session_id)
            except Exception:  # noqa: BLE001
                pass
        self._session_id = session_id
        # FIX 1 (wrong-session drain): a session switch DROPS anything still
        # parked on the busy-queue. A queued message referred to the OLD
        # conversation's context; draining it here would replay it into the
        # newly-loaded thread (verified cross-session contamination). The loss
        # is intentional — there is no re-queue affordance this round. Note
        # this line is only reached AFTER the enablement decoupling guard: a
        # refused load switches nothing, so it drops nothing.
        self._pending_triggers.clear()
        self._write_session_pointer(session_id)
        return {"session_id": session_id, "messages": msgs}

    def delete_session(self, session_id: str) -> bool:
        """Delete a past chat (cascade). If it was the active session, the thread
        resets so the next send starts fresh."""
        conn = self._open_conn(readonly=False)
        if conn is None:
            return False
        try:
            from src.services.chat_session import delete_session as _del
            ok = bool(_del(session_id, conn=conn))
        except Exception:  # noqa: BLE001 — best-effort
            ok = False
        finally:
            conn.close()
        if ok and session_id == self._session_id:
            self.new_session()
        return ok

    def new_session(self) -> None:
        """Start a fresh thread: clear engine history and drop the active session
        so the next send creates a new one; clear the tools→session pointer."""
        self._session_id = None
        # FIX 1: same rule as load_session — queued texts referred to the old
        # thread and must not seed the new one. Intentional drop.
        self._pending_triggers.clear()
        if self._engine is not None:
            try:
                self._engine.clear_history()
                self._engine.set_session_id(None)
            except Exception:  # noqa: BLE001
                pass
        self._write_session_pointer("")

    def _open_conn(self, *, readonly: bool):
        """A connection to the DB the MCP server writes to (same target as
        ``recent_tool_calls``)."""
        db_path = self._db_path()
        if not db_path:
            return None
        try:
            from src.data.connection_factory import get_connection
            return get_connection(db_path, readonly=readonly)
        except Exception:  # noqa: BLE001
            return None

    def _write_session_pointer(self, session_id: str) -> None:
        """Point the persistent MCP server's tools at ``session_id`` (or clear)."""
        db_path = self._db_path()
        if not db_path:
            return
        try:
            Path(db_path).parent.joinpath(".current_chat_session").write_text(
                session_id or "", encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass

    # ── engine ──────────────────────────────────────────────────────

    def _db_path(self) -> str | None:
        if self.demo or self.db is None:
            import os
            import tempfile
            return os.path.join(tempfile.gettempdir(), "alma_enablement_demo.db")
        return str(self.db.db_path) if hasattr(self.db, "db_path") else None

    def _setup_engine(self):
        try:
            from src.services.chat_engine import ChatEngine
            from src.ui.pages.enablement.page import RENN_SYSTEM_PROMPT
            self._engine = ChatEngine(
                system_prompt=RENN_SYSTEM_PROMPT,
                context_provider=self._agent_context,
                task_type="enablement_chat",
                tools_enabled=True,
                use_mcp_tools=True,
                db_path=self._db_path(),
                stream=True,   # the Agent surface streams tokens (M-streaming)
            )
            self._engine.bridge_recycle_requested.connect(self._on_recycle)
            # M0 busy-queue: when a turn finishes, drain one queued trigger (a
            # picker-resolve follow-up that arrived while Renn was busy).
            self._engine.busy_changed.connect(self._on_busy_changed)
            # FIX 2c (transcript divergence): the stop path never runs the
            # telemetry callback, so the persisted transcript would silently
            # diverge from the engine's in-memory history (which already
            # carries the stop marker). hasattr-guarded for engine fakes.
            if hasattr(self._engine, "run_stopped"):
                self._engine.run_stopped.connect(self._on_run_stopped)
            # Persist the assistant turn (content + telemetry) to chat_messages so
            # past chats actually have a transcript + a title. The bridge CHAINS
            # this callback (it adds the live meter on top), so both survive.
            self._engine.set_telemetry_callback(self._persist_turn)
        except Exception as exc:  # noqa: BLE001 — chat degrades, the page still loads
            logger.warning("Agent chat engine unavailable: %s", exc)
            self._engine = None

    def _agent_context(self, user_message, history):
        """Per-turn context for the Agent surface — the operator identity (so Renn
        can confirm 'who am I'), a PERSISTENT note that the operator's tasks are real
        (so Renn never recants a briefing on a later turn, once the one-shot plan
        block is gone), plus, on the first turn only, the [TODAY'S PLAN] block."""
        try:
            from src.data import enablement_identity as ident
            line = ident.operator_context_line()
        except Exception:  # noqa: BLE001 — the chat still works without the line
            line = ""
        block = self._greeting_block
        if block:
            self._greeting_block = None   # one-shot: the full plan only on the first turn
        if not line:
            return block or ""
        # Injected EVERY turn so Renn never later calls a real briefing "fabricated".
        reality = ("[NOTE] The operator's tasks and the morning briefing are REAL data "
                   "from the task store (the same records list_tasks returns) — never call "
                   "them invented or fabricated, and don't re-query just to verify them.")
        core = line + "\n" + reality
        return f"{block}\n\n{core}" if block else core

    def _open_greeting(self):
        """One-shot proactive greeting on the Agent surface: arm the [TODAY'S PLAN]
        block and auto-send a summarize-only opening turn WITHOUT a user bubble
        (send() persists the user turn; here we drive the engine directly). No-ops
        without an engine / identity / dated tasks (fail-closed)."""
        if self._greeting_sent or self._engine is None:
            return
        conn = self._open_conn(readonly=True)
        if conn is None:
            return
        try:
            from src.data import startup_greeting
            block = startup_greeting.build_greeting_block(conn)
        except Exception:  # noqa: BLE001 — a greeting must never break page load
            block = ""
        finally:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
        if not block:
            return
        self._greeting_block = block
        self._greeting_sent = True
        trigger = ("Give me my morning briefing: what is due today, what is overdue, "
                   "and what I should tackle first. Do not spend any budget on research "
                   "— just summarize my current tasks.")
        # Auto-greeting, NOT a user turn → set up session/provider and drive the
        # engine directly, skipping send()'s _persist_message('user', …) so no user
        # bubble appears (only Renn's greeting).
        self._ensure_session()
        self._prepare_provider()
        self._engine.send(trigger)

    def _build_mcp_config(self) -> list[dict]:
        import sys
        db_path = self._db_path() or ""
        pointer = str(Path(db_path).parent / ".current_chat_session") if db_path else ""
        env = [
            {"name": "ALMA_DB_PATH", "value": db_path},
            {"name": "ALMA_CHAT_SESSION_FILE", "value": pointer},
        ]
        try:
            from src.ui import app_modes
            if app_modes.current_mode() == app_modes.MODE_ENABLEMENT:
                env.append({"name": "ALMA_MCP_EXCLUDE_TOOLS", "value": "semantic_search"})
        except Exception:
            pass
        return [{
            "name": "alma-chat-tools",
            "command": sys.executable,
            "args": ["-m", "src.mcp.chat_mcp_server"],
            "env": env,
        }]

    def _ensure_session(self):
        if self._session_id or self._engine is None:
            return
        # Create the session in the SAME db the engine, MCP tools, pointer, and
        # the M3 history browser all read from (``_db_path()``) — not
        # ``self.db.conn`` — so a chat you just had actually shows up in History
        # (in demo mode those two diverge; see _db_path).
        conn = self._open_conn(readonly=False)
        if conn is None:
            return
        try:
            from pathlib import Path
            from src.services.chat_session import resolve_or_create_session
            db_path = self._db_path()
            pointer_dir = Path(db_path).parent if db_path else None
            # Share ONE session with the Workbench panel via the pointer file,
            # instead of each surface minting its own (finding 5).
            self._session_id = resolve_or_create_session(
                "enablement", conn, pointer_dir=pointer_dir)
            self._engine.set_session_id(self._session_id)
            self._write_session_pointer(self._session_id)
        except Exception as exc:  # noqa: BLE001 — telemetry only; tools still work
            logger.debug("agent session create failed: %s", exc)
        finally:
            conn.close()

    # ── provider (mirrors EnablementPage._prepare_provider) ─────────

    def _prepare_provider(self):
        try:
            from src.gemini.client_factory import resolve_provider_for_task
            prov = resolve_provider_for_task("enablement_chat")
        except Exception:
            prov = "gemini"
        if prov == "gemini":
            self._teardown_claude_client()
            self._ensure_warm_bridge()
        else:
            self._teardown_warm_bridge()
            self._ensure_claude_client()

    def _ensure_warm_bridge(self):
        if self._warm_bridge is not None or self._engine is None:
            return
        try:
            from src.agents.report_bridge_client import ReportBridgeClient
            bridge = ReportBridgeClient(model="gemini-2.5-flash")
            bridge.set_mcp_config(self._build_mcp_config())
            self._warm_bridge = bridge
            self._engine.set_client(bridge)
        except Exception as exc:  # noqa: BLE001 — fall back to a per-message client
            logger.warning("Agent warm bridge boot failed: %s", exc)
            self._warm_bridge = None

    def _ensure_claude_client(self):
        if self._engine is None:
            return
        if self._claude_client is not None:
            self._engine.set_client(self._claude_client)
            return
        try:
            from src.gemini.client_factory import build_client_for_task
            client = build_client_for_task("enablement_chat")
            if client is not None and hasattr(client, "set_mcp_config"):
                client.set_mcp_config(self._build_mcp_config())
            self._claude_client = client
            self._engine.set_client(client)
        except Exception as exc:  # noqa: BLE001 — degrade to a per-message client
            logger.warning("Agent Claude client wiring failed: %s", exc)
            self._claude_client = None
            try:
                self._engine.set_client(None)
            except Exception:
                pass

    def _teardown_warm_bridge(self):
        if self._warm_bridge is not None:
            try:
                self._warm_bridge.shutdown()
            except Exception:
                pass
            self._warm_bridge = None

    def _teardown_claude_client(self):
        if self._claude_client is not None:
            try:
                self._claude_client.shutdown()
            except Exception:
                pass
            self._claude_client = None

    def _on_recycle(self):
        # Provider switch / degraded streak: drop the warm client so the next
        # send rebuilds + re-wires the MCP tools.
        self._teardown_warm_bridge()
        self._teardown_claude_client()
