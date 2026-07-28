"""Controller behind the web Zendesk tab (``enablement.web_tabs``).

``ZendeskWebController`` is the Python owner of the web Garden-clone Zendesk
surface: it reads the local mirror (mig 030 + 051 tables) through the injected
``conn_fn``, shapes JSON viewmodels, and pushes them to the React route via
``ZendeskBridge``. The web layer is a pure renderer — every fact it shows
comes from here, and every click routes back here for resolution against
Python-held state.

Security posture (the QWebChannel trust boundary):

* Every ``js_*`` slot receives UNTRUSTED input — any page script can invoke
  it. Invalid input is a SILENT no-op: ids resolve only against the mirror /
  the controller's last-served rows, kinds and scopes against hard
  allowlists, and save payload keys against a hard RENAME-ONLY allowlist
  (a page script can never ride a status transition — or draft CONTENT —
  through a save; reviewed bytes and copied bytes must never diverge).
  The one content-bearing slot, ``js_save_body_edit``, honors ONLY a
  markdown ``body`` key and ALWAYS recomputes ``body_html`` Python-side as
  ``sanitize_html(markdown_to_html(body))`` — page-supplied HTML never
  lands, so the same no-divergence guarantee holds.
* Destructive actions (delete revision, purge mirror) run the workbench
  gate verbatim: validate → single-winner ``_action_inflight`` claim BEFORE
  the native confirm modal → re-verify after approve → dispatch → resolve.
  No ``confirm_fn`` injected → the gate FAILS CLOSED.
* While EITHER inflight claim is held every mutating slot silently refuses
  (confused-deputy freeze); the claim is taken BEFORE any native nested
  event loop (the QMessageBox AND the QFileDialog pickers).
* **THE PREVIEW IS NOT A DISCLOSURE SURFACE (2026-07-27).**

      The ONLY disclosure surface for markup is the native copy dialog.

  Eight rounds were spent making the sandboxed preview tell the truth about
  bytes a human was about to paste, and eight times whoever wrote the
  content defeated whatever the preview had learned to detect:
  ``display:none``, then ``transform:scale(0)`` (a property the allowlist
  admitted and the detector never inspected), then — with the CSS policy
  finally closed — ``<font color="#ffffff">`` and ``<td bgcolor="#000">``,
  which need no CSS at all, and ``<iframe>``/``<rp>``, whose children
  Chromium simply never paints. The pattern, not any one bug, is the
  finding: **a projection decides what the human needs to see, and whoever
  controls the content controls the projection.**

  So the preview was taken OUT of the trust path instead of hardened again.
  Every clipboard release of MARKUP — mirror articles and mirror macros
  included, not just drafts — now goes through the native confirm that
  displays the exact bytes (item 6 below). Once the dialog is the
  disclosure for every HTML copy, no CSS policy, sanitizer report or markup
  notice is load-bearing for disclosure any more: the preview is a DISPLAY
  concern, and ``markup_report`` / ``_MARKUP_NOTICE`` are a heads-up about
  render fidelity, not the thing standing between hidden bytes and the
  clipboard. The review-record/content-hash gate stays as defence in depth.

* Every article HTML string RENDERED by this controller passes a sanitizer
  (``_article_srcdoc`` is the only srcdoc producer). The rendered preview
  uses the rendering-only ``sanitize_html_preview`` profile so a pulled
  article keeps its structure — classes, ids, data-attributes and tables
  are inert inside the SPA's ``sandbox=""`` iframe. That profile still aims
  to fail in ONE direction:

      No CSS surviving into the preview can reduce what the reader sees
      relative to the source bytes.

  It is enforced by a CLOSED CSS allowlist rather than a list of known
  hiding tricks: only typography, colour, non-negative spacing/borders and
  table scaffolding survive, each value-gated, and everything else —
  ``display``, ``position``, ``overflow``, ``opacity``, ``transform``,
  ``clip-path``, ``filter``, ``zoom``, and any property CSS grows next year
  — is dropped, rendered inside a marked wrapper, and named in
  ``markup_report``. The presentational attributes that say the same things
  without any CSS (``<font color/face/size>``, ``bgcolor``) run the same
  gates and the same foreground-vs-background comparison, which now carries
  the INHERITED colour pair down the tree so a black-on-black split across
  two elements is caught. Tags whose children are never painted
  (``<iframe>``, ``<rp>``) are unwrapped so their content becomes visible,
  and named. The ``hidden`` attribute, ``width="0"``, collapsed
  ``<details>`` and HTML comments are the non-CSS members of the same
  family and are handled beside it. All of that is HONESTY about render
  fidelity — the notice fires more often than it used to, and that is now
  cheap, because it is no longer what protects anyone. STORED content and
  the ``text/html`` clipboard flavour keep the STRICT ``sanitize_html``;
  the two profiles are separate objects in ``html_sanitize`` precisely so
  widening the preview can never widen a paste.
* **THE CLIPBOARD INVARIANT (the whole point of this module).**

      Whatever reaches the clipboard is byte-identical to something the
      reviewer was shown, and nothing can be present in the copied bytes
      without being present in the reviewed material.

  A specialist reads a review surface and then pastes the copied bytes
  into live Zendesk by hand, so anything the review surface drops is
  invisible yet published. Projections DROP things by construction (a
  readable text projection erases attributes, styles, script bodies,
  duplicate attributes, zero-size spans...), so a projection can never be
  the authority. The structure here instead is:

  1. **The authoritative review is a SOURCE diff of the exact bytes the
     clipboard will deliver** (``js_request_diff``): the HTML source text
     itself for articles, the canonical actions JSON for macros, diffed
     line/word-wise. Every attribute, style declaration, ``<script>`` body
     and hidden span is literally on screen. The readable
     ``html_to_review_text`` projection ships alongside as an explicitly
     SECONDARY convenience view and is never shown alone.
  2. **Copy releases only reviewed bytes** (``_resolve_copy`` +
     ``_review_covers``): the payload is recomputed from the DB by
     ``_copy_bundle`` — the SAME function that built the reviewed material
     — and every exact string about to be handed to the clipboard (the
     text flavour AND the text/html mime flavour) must hash-match a string
     the recorded review showed, on top of a recompute-from-row content
     hash. This gate covers DRAFTS **and** mirror articles/macros: the
     mirror review is recorded by ``js_open_article`` /``js_open_macro``,
     whose payloads carry the exact source bytes (``body_source``). No
     record, any mismatch, or a row that changed under the review means NO
     clipboard write — fail closed everywhere.
  3. **Verbatim bytes are honest, not silently rewritten.** ``origin='pull'``
     mirror rows are byte-faithful to remote Zendesk by design, so when the
     bytes differ from what ``sanitize_html`` would produce the review and
     the copy status line say so explicitly (``_MARKUP_NOTICE``).
  4. **No silent zero-change**: if the source diff reports zero changed
     lines while the bytes differ from the baseline, that is a diff bug —
     the payload carries ``warning`` instead of an innocent "0 changed
     lines".
  5. Every draft-content mutator drops the recorded review, so a stale
     on-screen diff can never authorize a copy, and pending drafts are
     refused at the clipboard regardless.

  Precision on "shown": of the strings ``_record_review`` binds, the plain
  flavours are rendered literally — the diff rows, ``body_source``, the
  title row. The rich (``text/html`` mime) flavour is not independent
  content: it is ``sanitize_html`` of the string the reviewer WAS shown,
  and sanitize only removes and escapes — it can never introduce a tag,
  attribute or URL absent from the source. That derivation is asserted in
  tests, not assumed. (The rendered preview srcdoc is a THIRD, wider
  rendering — ``sanitize_html_preview``, see ``_article_srcdoc`` — and is
  never a clipboard flavour.)

  6. **EVERY COPY OF MARKUP TAKES A NATIVE CONFIRM THAT DISPLAYS THE
     BYTES.**

         No clipboard release of MARKUP — ``body_html``, ``body_rich`` or
         ``macro_reply``, on a MIRROR row or a draft — and no clipboard
         release of DRAFT content at all — article draft or macro draft,
         every field, both mime flavours — happens without a NATIVE confirm
         showing the EXACT characters about to be released, IN A VISIBLE
         SCROLLABLE VIEW, with the row's title and the field named.
         No ``confirm_fn`` injected, declined, or the row's bytes moved
         while the dialog was open ⇒ nothing is written to the clipboard and
         NO ``copy_resolved`` receipt is emitted. The rule lives in
         ``_copy_needs_confirm`` and is a pure function of (target kind,
         field name) — nothing the content can steer.

         Titles and macro names stay un-gated on MIRROR rows: they are not
         markup, the destination does not interpret them, and the review
         record already covers them. (A DRAFT still confirms them, because
         a draft confirms everything.)

     "Displays" is literal and was once false. The first implementation
     routed anything over 400 characters into ``QMessageBox``'s DETAIL pane,
     which Qt collapses behind a "Show Details…" button — so for every
     realistic body (all of them exceed 400 characters) the dialog said
     "these are the EXACT characters that will go on the clipboard" and then
     showed none of them. ``_copy_confirm_text`` therefore hands the bytes
     over as ``content``, page.py renders them in a read-only text view
     sized to show a meaningful chunk without a further click, and
     ``_confirm_shows_content`` verifies by PARAMETER NAME that the host
     really implements that contract before trusting it with the payload.

     Two further properties make the gate cover the CLIPBOARD rather than
     just the release:

     * ``js_copy_field`` is freeze-checked and an approved release holds the
       clipboard for ``_COPY_HOLD_S``, so no un-gated copy can substitute
       different bytes between the approval and the paste;
     * every refusal at or after the confirm reports ONE string
       (``_COPY_REFUSED``) and arms an escalating per-target cooldown, so a
       page can neither tell a human decline from a machine refusal nor
       summon modals until one is mis-clicked.

     Rationale, and why this replaced an authorship ledger: a draft is by
     definition content that is NOT yet in Zendesk and is about to be
     pasted into a public site by hand. Python can prove a human perceived
     exactly two surfaces — a native Qt dialog and the native status line.
     Everything emitted over QWebChannel may be discarded by a page that
     renders nothing, and ``js_request_diff`` / ``js_open_article`` /
     ``js_open_macro`` are page-callable and record the review as a side
     effect, so "was this reviewed" is not provable on its own.

     Round 4 tried to gate only bytes the PAGE authored, tracked in a
     provenance ledger. That model assumed "a Python-side actor wrote it"
     implies "the page did not choose it". **That is false.** page.py
     co-registers the Renn chat bridge on this same QWebChannel, and
     ``ChatBridge.send`` is a page-callable slot — so a page script chooses
     the exact text Renn receives, Renn calls ``propose_article_update``,
     and the page-chosen bytes land in a draft through a Python actor this
     controller never observes. No stamp, no confirm, clipboard. Any Python
     actor whose INPUT the page controls is not a trustworthy authorship
     source, and authorship is therefore unknowable here. The ledger also
     failed on partial writes (a second write raising after the first
     committed left the bytes stamped by nobody).

     Gating EVERY draft copy is simpler AND strictly stronger: it does not
     matter who authored the content, because the human sees exactly what
     is going to the clipboard at the moment it goes. It doubles as a
     useful "this is what you are pasting" preview, and the owner's
     workflow is copy-then-paste, so the dialog costs one click on an
     action that is already deliberate.

     The confirm runs the destructive-gate discipline verbatim (freeze
     check → claim before the nested event loop → confirm → re-verify the
     bytes did not move → release). mark-ready / mark-copied stay
     confirm-free by design.

     MIRROR rows used to be exempt: content already live in Zendesk was
     released under the review record plus the preview and the markup
     notice. That exemption is GONE (2026-07-27), because the preview +
     notice chain is exactly the chain all three of the round-8 attackers
     walked — a mirror row is a row an IMPORT can write, and a pull is
     byte-faithful to whatever the remote instance holds. The ergonomic
     cost is one click on an already-deliberate action, and the dialog
     doubles as the "this is what you are pasting" view.
* **This controller can never write to real Zendesk**: it holds no client,
  the compat ``article_push`` / ``macro_push`` / ``sync_requested`` signals
  exist for page.py wiring parity only and are NEVER emitted, and the only
  conduit toward Zendesk is the injected Python-side clipboard.
"""

from __future__ import annotations

import hashlib
import json
import re
import time

from PySide6.QtCore import QObject, Signal

_VIEWS = ("articles", "macros", "revisions")
_REVISION_FILTERS = ("open", "copied", "all")
_SEARCH_KINDS = ("all", "articles", "macros")
_DRAFT_KINDS = ("article", "macro")
_PURGE_SCOPES = ("all", "articles", "macros", "imported")
# Per-target copy-field allowlists: the TARGET kind (never a polymorphic id)
# decides the table, so a draft id can never silently address an article row.
_COPY_FIELDS = {
    "article": ("title", "body_html", "body_rich"),
    "article_draft": ("title", "body_html", "body_rich"),
    "macro": ("macro_name", "macro_reply"),
    "macro_draft": ("macro_name", "macro_reply"),
}
_COMMENT_FIELDS = ("comment_value", "comment_value_html")
# Zendesk Admin action wording (ui dossier §macro actions): id-suffixed
# fields display their object name; multi-word fields are sentence case
# ("Add tags", never "Add Tags"). Unknown fields fall back to sentence case.
_FIELD_DISPLAY = {"comment_value": "Comment/Reply",
                  "comment_value_html": "Comment/Reply (HTML)",
                  "subject": "Subject",
                  "status": "Status",
                  "priority": "Priority",
                  "type": "Type",
                  "group_id": "Group",
                  "assignee_id": "Assignee",
                  "ticket_form_id": "Form",
                  "brand_id": "Brand",
                  "set_tags": "Set tags",
                  "add_tags": "Add tags",
                  "remove_tags": "Remove tags",
                  "comment_mode": "Comment mode"}

# draft kind -> copy target, the key shape of the review ledger.
_DRAFT_TARGET = {"article": "article_draft", "macro": "macro_draft"}
# The copy targets that are DRAFT content — not yet in Zendesk, about to be
# pasted into a public site by hand. Every clipboard release from one of
# these takes the native confirm (module docstring, item 6).
_DRAFT_COPY_TARGETS = frozenset(_DRAFT_TARGET.values())
# THE COPY FIELDS THAT CARRY MARKUP, on every target kind — mirror rows
# included (module docstring, item 6). A static set, not a look-at-the-bytes
# test: "does this string contain a tag" is a content-derived branch, and a
# content-derived branch is something whoever controls the content can steer.
# ``macro_reply`` is here because a reply is routinely ``comment_value_html``
# and the live Zendesk comment editor INTERPRETS what is pasted into it.
# Titles and macro names are not markup and stay un-gated (a draft still
# confirms them, because a draft confirms everything).
_MARKUP_COPY_FIELDS = frozenset({"body_html", "body_rich", "macro_reply"})


def _copy_needs_confirm(target: str, field: str) -> bool:
    """Whether this clipboard release takes the native confirm.

    TRUE for every release of markup, and for every release from a DRAFT.
    Deliberately a pure function of (target kind, field name): no row state,
    no byte inspection, nothing the content can influence."""
    return target in _DRAFT_COPY_TARGETS or field in _MARKUP_COPY_FIELDS

# Said out loud whenever the sandboxed preview and the stored bytes are not
# the same picture, in EITHER direction:
#
# * something could not be displayed — a script/style/form/plugin container
#   was dropped, an event handler stripped, a URL scheme refused or merely
#   unexpected;
# * the preview did not honour the source's presentation — every CSS
#   declaration outside the closed display-safe allowlist is dropped and
#   named (display / position / overflow / opacity / transform / clip-path /
#   filter / zoom / anything unrecognised), as are the ``hidden`` attribute,
#   zero-size presentational attributes and HTML comments.
#
# The mirror keeps pull-origin bytes verbatim on purpose, so the honest move
# is to announce the divergence, never to silently rewrite or hide it.
#
# Measured against the PREVIEW profile (``sanitize_html_preview``), not the
# strict one: the strict profile also throws away classes, ids, data-attrs
# and table scaffolding, none of which hides content, so measuring against it
# fired this notice on essentially every pulled article. Under the closed CSS
# allowlist the notice DOES fire more often than it did while the allowlist
# was wide — an article that lays itself out with flexbox now says so. That
# is deliberate: the alternative was `transform:scale(0)` reporting nothing
# at all. Everything dropped is named, so this is never a bare "something
# changed" a reader has to ignore.
#
# The report it is computed from is EMITTED alongside it (``markup_report``)
# so the surface can name what was hidden instead of just warning that
# something was.
_MARKUP_NOTICE = ("this content contains markup the preview does not "
                  "display faithfully - hidden or dropped in the source; "
                  "read the exact HTML before pasting")
# The macro flavour of the same honesty. A macro reply has no sanitized
# preview (every action value renders verbatim as escaped text), so the
# warning is about the DESTINATION: these bytes carry active markup that
# the live Zendesk comment editor will interpret.
_MACRO_MARKUP_NOTICE = ("this reply contains active markup a sanitizer "
                        "would strip - read the exact action source before "
                        "pasting")
# A macro reply is frequently PLAIN TEXT, where sanitize_html differs from
# the input purely by entity-escaping ("Billing & Claims"). Escaping alone
# hides nothing, so the macro notice additionally requires something that
# actually opens a tag; article bodies are HTML by definition and keep the
# stricter "any divergence is announced" rule unchanged.
_MARKUP_TAG_RE = re.compile(r"<[A-Za-z/!]")
# A source diff that finds nothing while the bytes differ is a DIFF BUG,
# not a clean revision. Never render it as an innocent "0 changed lines".
_ZERO_CHANGE_WARNING = ("The stored bytes differ from the baseline but the "
                        "source diff found no changed line. Do NOT copy this "
                        "revision - report it.")

_ID_RE = re.compile(r"^-?\d+$")     # mirror ids (imports use negative ids)
_QUERY_CAP = 200
_TITLE_CAP = 255
_BODY_CAP = 200_000                 # specialist body-edit markdown ceiling
_BODY_EDIT_KINDS = ("draft", "article")
# The ONE draft status js_save_body_edit accepts. Also decides which drafts
# the revisions feed carries a `body` for — the seed for the edit textarea
# is served exactly where an edit is accepted, so the feed can never grow
# hundreds of 200k-char bodies and the editor can never open blank.
_EDITABLE_DRAFT_STATUS = "pending"
# ── copy-gate timing (F2/F3) ─────────────────────────────────────────
# After an APPROVED draft release, the clipboard belongs to the operator for
# this long: no un-gated copy (mirror rows self-serve theirs) may overwrite
# it. What the human approved has to still be there when they paste.
_COPY_HOLD_S = 10.0
# A refused copy confirm (declined, no dialog host, or bytes that moved) arms
# a per-target cooldown, and repeated refusals escalate to a long lockout —
# the js_request_pull pattern. A page cannot summon modals until one is
# mis-clicked.
_COPY_CONFIRM_COOLDOWN_S = 10.0
_COPY_CONFIRM_MAX_ATTEMPTS = 3
_COPY_CONFIRM_LOCKOUT_S = 300.0
# THE ONE refusal string every copy refusal at or after the confirm uses.
# Distinct wording per branch was an ORACLE: it let a page tell a human
# decline ("Copy cancelled") from a missing dialog host ("can only be copied
# from the app window") and grind accordingly.
_COPY_REFUSED = "Copy refused - nothing was placed on the clipboard."
# Parameter names a confirm host may use for the third, CONTENT-SHOWING
# argument. Checked by NAME (see _confirm_shows_content) so a host whose
# third parameter means something else — a parent widget, a flag — can never
# be handed the clipboard payload and silently swallow it.
_CONFIRM_CONTENT_PARAMS = ("content", "exact_bytes", "detail")
# ...and the FOURTH argument: the markup report, as its own list. It is
# rendered in its own widget, never concatenated into the heading, so no
# report — however long, however crafted — can displace the heading's fixed
# disclosure lines. A host that does not declare it gets the report appended
# to the message instead (still visible, still after the fixed lines, and
# harmless because every entry is flattened and capped at construction).
_CONFIRM_NOTES_PARAMS = ("notes", "markup_notes", "report")
# The one sentence that introduces the report, wherever it is rendered.
_MARKUP_REPORT_LEAD = ("These bytes contain markup the preview does not "
                       "display faithfully")
_SPECIALIST_REF = "specialist-edit"
_SPECIALIST_RATIONALE = "Edited in the workspace."
_PULL_COOLDOWN_S = 60               # untrusted slot must not drive traffic
_PULL_FAIL_COOLDOWN_S = 5           # a failed pull must not lock out retry
# The copy confirm's heading is a FIXED number of lines, none of them
# content-derived except the quoted row title on line 1 (whitespace-collapsed
# and length-capped). Asserted in the tests: a report, a title or any other
# untrusted string that could add or remove a line is a defect.
_CONFIRM_HEADING_LINES = 5
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

# Zendesk Guide (Copenhagen theme) article typography, so the sandboxed
# preview approximates what an end user sees on the Help Center rather than
# unstyled browser defaults. Ships INSIDE the srcdoc: the iframe is
# ``sandbox=""``, so this stylesheet is the only CSS in that document and it
# cannot reach — or be reached by — the host page.
_ARTICLE_PREVIEW_CSS = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body { margin: 0; padding: 28px 32px 40px; background: #fff; color: #2f3941;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
  "Helvetica Neue", Arial, sans-serif; font-size: 15px; line-height: 1.65;
  word-wrap: break-word; }
.alma-hc-article { max-width: 46rem; margin: 0 auto; }
h1, h2, h3, h4, h5, h6 { color: #2f3941; font-weight: 600; line-height: 1.3;
  margin: 1.8em 0 .6em; }
h1 { font-size: 2rem; } h2 { font-size: 1.5rem; } h3 { font-size: 1.25rem; }
h4 { font-size: 1.1rem; } h5, h6 { font-size: 1rem; }
h1:first-child, h2:first-child, h3:first-child { margin-top: 0; }
p, ul, ol, dl, table, blockquote, pre, figure { margin: 0 0 1.1em; }
ul, ol { padding-left: 1.6em; } li { margin: .3em 0; }
a { color: #1f73b7; text-decoration: none; }
a:hover { text-decoration: underline; }
img { max-width: 100%; height: auto; }
hr { border: 0; border-top: 1px solid #e9ebed; margin: 2em 0; }
blockquote { border-left: 4px solid #e9ebed; margin-left: 0;
  padding: .2em 0 .2em 1em; color: #68737d; }
code, pre, kbd, samp { font-family: "SFMono-Regular", Menlo, Consolas,
  monospace; font-size: .9em; }
code { background: #f8f9f9; border: 1px solid #e9ebed; border-radius: 3px;
  padding: .1em .35em; }
pre { background: #f8f9f9; border: 1px solid #e9ebed; border-radius: 4px;
  padding: 12px 14px; overflow-x: auto; }
pre code { background: none; border: 0; padding: 0; }
table { border-collapse: collapse; width: 100%; }
th, td { border: 1px solid #e9ebed; padding: 8px 10px; text-align: left;
  vertical-align: top; }
th { background: #f8f9f9; font-weight: 600; }
caption { caption-side: bottom; color: #68737d; font-size: .875em;
  padding-top: .5em; }
iframe { max-width: 100%; border: 1px solid #e9ebed; border-radius: 4px; }
details { border: 1px solid #e9ebed; border-radius: 4px; padding: .6em .9em;
  margin: 0 0 1.1em; }
summary { cursor: pointer; font-weight: 600; }
mark { background: #fff7d5; }
/* BELT AND BRACES over the sanitizer's closed CSS allowlist. Nothing in
   this document should carry any of these — html_sanitize drops every one
   of them, whatever value they hold — so this rule costs a faithful render
   nothing and makes a construct that ever slips through INERT instead of
   INVISIBLE. Only properties with no legitimate use in an article body are
   listed: neutralising display/position/opacity globally would break real
   layout, which is why those are the sanitizer's job alone. */
.alma-hc-article *, .alma-hc-article *::before, .alma-hc-article *::after {
  transform: none !important; filter: none !important;
  backdrop-filter: none !important; clip: auto !important;
  clip-path: none !important; mask-image: none !important;
  mix-blend-mode: normal !important; zoom: 1 !important;
  -webkit-text-security: none !important; }
/* Content the SOURCE hides and this preview shows anyway (see
   html_sanitize's preview profile). Loud on purpose: an invisible payload
   in a review surface is the whole attack, so the un-hidden version has to
   be the most conspicuous thing on the page. !important because the
   author's own declarations are still in the document. */
.alma-unhidden { outline: 2px dashed #cc3340 !important;
  outline-offset: 2px; background: #fff0f1 !important;
  color: #2f3941 !important; opacity: 1 !important;
  visibility: visible !important; display: block !important;
  position: static !important; font-size: 15px !important;
  text-indent: 0 !important; max-height: none !important;
  max-width: none !important; min-height: 0 !important; overflow: visible
  !important; z-index: auto !important; }
.alma-unhidden-note, .alma-source-comment { display: block;
  font-family: "SFMono-Regular", Menlo, Consolas, monospace;
  font-size: 12px !important; font-weight: 600; color: #cc3340 !important;
  background: #fff0f1; border-left: 3px solid #cc3340; padding: 2px 8px;
  margin: 2px 0; white-space: pre-wrap; opacity: 1 !important;
  visibility: visible !important; }
.alma-source-comment { color: #68737d !important; border-left-color: #68737d;
  background: #f8f9f9; }
""".strip()


def _probe_connected() -> bool:
    """Whether a read-only Zendesk client could be built from settings.
    Module-level so tests can monkeypatch away the keyring probe."""
    try:
        from src.data.zendesk_client import ZendeskClient
        return ZendeskClient.from_settings() is not None
    except Exception:  # noqa: BLE001 — display-only fact, never fatal
        return False


def _last_pull_iso():
    """``enablement.zendesk.last_pull`` (written by page.py's main-thread
    ``zendesk_mirror_done`` slot). Module-level for test monkeypatching."""
    try:
        from src.data.settings_manager import get_section
        return ((get_section("enablement", {}) or {}).get("zendesk", {})
                or {}).get("last_pull")
    except Exception:  # noqa: BLE001
        return None


def _display_date(value) -> str:
    """'2026-07-01…' → 'Jul 1, 2026' (locked presentation format — the only
    duplicated display constant; see the parity note in the test module)."""
    s = str(value or "")[:10]
    try:
        y, m, d = int(s[0:4]), int(s[5:7]), int(s[8:10])
        return f"{_MONTHS[m - 1]} {d}, {y}"
    except (ValueError, IndexError):
        return ""


def _actions_plain(actions) -> str:
    """Plain-text projection of a macro actions list — mirrors the store's
    actions_text projection so diffs compare like against like."""
    parts = []
    for a in actions or []:
        if isinstance(a, dict):
            piece = f"{a.get('field', '')} {a.get('value', '')}".strip()
            if piece:
                parts.append(piece)
    return "\n".join(parts)


def _sha(text) -> str:
    """Hash of an EXACT string.

    This is the identity used to prove that bytes reaching the clipboard
    are bytes the reviewer was shown, so it must hash the string itself —
    never a projection, a strip, or a normalization of it."""
    return hashlib.sha256(
        ("" if text is None else str(text)).encode("utf-8", "surrogatepass")
    ).hexdigest()


def _actions_source(actions) -> str:
    """Canonical SOURCE rendering of a macro actions list for the
    authoritative review diff.

    JSON with a key per line, so every character of every action value —
    markup, whitespace, control characters, duplicate-looking fields —
    is literally visible to the reviewer. ``_actions_plain`` (the readable
    projection) is the secondary view, not this."""
    rows = []
    for a in actions or []:
        if isinstance(a, dict):
            rows.append({"field": str(a.get("field", "")),
                         "value": str(a.get("value", ""))})
    return json.dumps(rows, indent=2, ensure_ascii=False, sort_keys=True)


def _first_reply(actions):
    """Text of the first comment action (``comment_value`` OR
    ``comment_value_html`` — real macros routinely use the html form)."""
    for a in actions or []:
        if isinstance(a, dict) and a.get("field") in _COMMENT_FIELDS:
            return str(a.get("value", ""))
    return None


class ZendeskWebController(QObject):
    """State + viewmodels for the web Zendesk tab.

    Mirrors the native ``ZendeskPage`` surface (7 signals + 5 setters) so
    page.py's existing wiring and ``_load_zendesk`` work unchanged against
    either implementation — but ``article_push`` / ``macro_push`` /
    ``sync_requested`` are NEVER emitted here, which is the structural
    guarantee that no live Zendesk write can originate from the web surface.
    """

    # ── web-facing pushes (JSON strings unless noted) ────────────────
    zendesk_data = Signal(str)      # main viewmodel {view, counts, articles, …}
    article_detail = Signal(str)    # one article + sanitized body_srcdoc
    macro_detail = Signal(str)      # one macro + decoded actions
    revisions_data = Signal(str)    # {filter, revisions: […]}
    diff_ready = Signal(str)        # workbench DiffBody row shape
    import_resolved = Signal(str)   # zendesk_import report dict verbatim
    pull_resolved = Signal(str)     # pull report dict verbatim
    copy_resolved = Signal(str)     # {request_id, target, field, ok, chars, sanitized}
    action_resolved = Signal(str)   # {request_id, action, ok, approved, error, …}
    status_text = Signal(str)       # PLAIN text (compat set_status echo)

    # ── ZendeskPage-compatible surface (wiring parity; page.py:381-388).
    # article_push / macro_push / sync_requested are NEVER emitted.
    sync_requested = Signal()
    article_selected = Signal(int)
    article_saved = Signal(int, str, str)
    article_push = Signal(int)
    macro_selected = Signal(int)
    macro_saved = Signal(int, str, str)
    macro_push = Signal(int)

    def __init__(self, conn_fn=None, confirm_fn=None, clipboard_fn=None,
                 file_pick_fn=None, folder_pick_fn=None,
                 pull_runner=None, import_runner=None,
                 now_fn=None, demo=False, parent=None):
        super().__init__(parent)
        # ``conn_fn() -> sqlite3.Connection`` — page.py passes self._conn
        # (demo/live aware). Only fast indexed reads run here; heavy work
        # (pull/import) is delegated to the injected runners, which page.py
        # implements as daemon-thread workers.
        self._conn_fn = conn_fn
        # ``confirm_fn(title, text) -> bool`` — NATIVE QMessageBox, injected
        # by page.py, physically unreachable from Chromium. Absent → every
        # destructive gate fails closed.
        self._confirm_fn = confirm_fn
        # ``clipboard_fn(text, html|None) -> bool`` — Python-side QClipboard.
        self._clipboard_fn = clipboard_fn
        # Native QFileDialog pickers (the page never sees filesystem paths).
        self._file_pick_fn = file_pick_fn
        self._folder_pick_fn = folder_pick_fn
        # ``pull_runner() -> bool`` / ``import_runner(paths) -> bool`` start
        # off-thread work; results return via notify_pull_done/import_done.
        self._pull_runner = pull_runner
        self._import_runner = import_runner
        self._now_fn = now_fn           # test-pinnable clock (epoch seconds)
        self._demo = bool(demo)
        self._view = "articles"
        self._status = ""
        self._action_inflight = False   # destructive-gate claim
        self._pull_inflight = False     # pull/import serialization claim
        self._last_pull_done = None     # epoch seconds; cooldown floor
        self._pull_cooldown_s = _PULL_COOLDOWN_S   # per-outcome window
        self._connected_cache = None
        self._req_seq = 0
        # Last-served row registries — the ONLY ids untrusted slots may
        # reference (accumulated across pushes; the DB re-check at dispatch
        # time stays authoritative for status/state).
        self._served_article_ids: set[int] = set()
        self._served_macro_ids: set[int] = set()
        self._served_drafts: dict[tuple[str, int], str] = {}
        # THE REVIEW LEDGER — (copy target, row id) -> {"content": hash of
        # the row's content at review time, "bytes": frozenset of _sha() of
        # every EXACT string that review showed}. Written only by
        # _record_review (from js_request_diff / js_open_article /
        # js_open_macro), read only by _review_covers. Covers drafts AND
        # mirror rows; see the clipboard-invariant note above.
        self._reviewed: dict[tuple[str, int], dict] = {}
        # F2: an approved draft release owns the clipboard until this epoch
        # second; un-gated (mirror) copies are refused until then.
        self._copy_hold_until = 0.0
        # F3: (target, id) -> {"count", "until"} — escalating cooldown after
        # a refused copy confirm, so repeated modals cannot be summoned.
        self._copy_backoff: dict[tuple[str, int], dict] = {}
        self._last_filter = "open"
        self._open_article_id = None    # last article served as a detail

    # ── the ZendeskPage-compatible setters page.py drives ────────────
    def set_articles(self, synced_count, drafts):
        """Host signalled article data changed (post-sync/import reload) —
        the args carry the NATIVE tab's shapes; the web viewmodel re-reads
        the mirror directly instead."""
        self._serve_data()

    def show_article_draft(self, draft):
        """Parity no-op — the web surface loads drafts from the store itself
        (the article_selected loop that feeds this is never emitted here)."""

    def set_macros(self, synced_count, drafts):
        self._serve_data()

    def show_macro_draft(self, draft):
        """Parity no-op (see show_article_draft)."""

    def set_status(self, text):
        """Native status line text. ALSO invoked internally for the copy
        announcements — a trusted surface the page cannot forge."""
        self._status = str(text or "")
        try:
            self.status_text.emit(self._status)
        except Exception:  # noqa: BLE001
            pass

    # ── host surface (page.py / workers) ─────────────────────────────
    def notify_pull_done(self, report):
        """Off-thread pull finished — release the claim, start the cooldown
        floor, hand the report to the page verbatim, re-serve the mirror.
        A FAILED pull arms only the short window (a user retry after a
        transient error must not be silently refused for a minute; sequential
        traffic stays bounded by the inflight claim + the short floor)."""
        self._pull_inflight = False
        ok = isinstance(report, dict) and bool(report.get("ok"))
        # A pull rewrites mirror rows underneath any recorded review; the
        # content-hash check would catch it, but dropping the records is
        # the fail-closed move (re-open, re-read, then copy).
        self._reviewed.clear()
        self._last_pull_done = self._now()
        self._pull_cooldown_s = (_PULL_COOLDOWN_S if ok
                                 else _PULL_FAIL_COOLDOWN_S)
        # The connected/mirror-only probe is stale by definition after a
        # pull resolves (credentials may have just been added/removed).
        self._connected_cache = None
        self._emit(self.pull_resolved,
                   report if isinstance(report, dict) else {})
        self._serve_data()

    def notify_import_done(self, report):
        self._pull_inflight = False
        self._reviewed.clear()          # imports rewrite mirror rows too
        self._emit(self.import_resolved,
                   report if isinstance(report, dict) else {})
        self._serve_data()

    def request_refresh(self):
        """Host asked for a re-serve (fresh build — no dedup cache exists on
        this feed, so a rebuild is also the invalidation; the connected
        probe re-runs too so a Settings change lands without a restart)."""
        self._connected_cache = None
        self._serve_data()

    # ── bridge-facing entry points (UNTRUSTED input) ─────────────────
    def js_refresh(self):
        self._connected_cache = None    # probe again — creds may have changed
        self._serve_data()

    def js_set_view(self, view):
        view = str(view or "")
        if view not in _VIEWS:
            return
        self._view = view
        self._serve_data()

    def js_search(self, query, kind):
        """FTS search over the mirror; results ship as a filtered
        zendesk_data payload (the lists keep their full row shape)."""
        q = str(query or "")[:_QUERY_CAP]
        kind = str(kind or "")
        if kind not in _SEARCH_KINDS:
            return
        if not q.strip():
            self._serve_data()
            return
        self._serve_data(query=q, search_kind=kind)

    def js_open_article(self, article_id):
        """Open an article detail. Digits/'-'-digits only, and the row must
        exist in the mirror — anything else is a silent no-op.

        THIS IS THE MIRROR REVIEW SURFACE. The payload carries BOTH the
        ``body_srcdoc`` (the rendered preview — the PRIMARY surface, and
        the reason it uses the wider preview profile) and ``body_source``
        — the exact stored bytes the clipboard would deliver, which the SPA
        renders as escaped text in a collapsed disclosure — plus
        ``markup_notice`` when the preview genuinely cannot display part of
        those bytes. Serving it
        RECORDS the review for this article, which is what unlocks
        ``js_copy_field`` for it; before this, a mirror copy is refused
        exactly like an unreviewed draft. Previously the ONLY on-screen
        view of a mirror article was the sanitized preview while
        ``_resolve_copy`` released the raw row bytes, so imported
        script/form markup was invisible yet copyable."""
        s = str(article_id or "")
        if not _ID_RE.match(s):
            return
        conn = self._db()
        if conn is None:
            return
        from src.data import zendesk_store as store
        art = store.get_article(conn, int(s))
        if art is None:
            return
        aid = art["article_id"]
        section, category = self._section_names(conn, art.get("section_id"))
        revisions = []
        for r in store.list_revisions(conn, kind="article"):
            if r.get("target_id") == aid:
                revisions.append({
                    "draft_id": r["draft_id"], "status": r["status"],
                    "title": r.get("title") or "",
                    "source_ref": r.get("source_ref"),
                    "updated_display": _display_date(r.get("updated_at"))})
                self._served_drafts[("article", r["draft_id"])] = r["status"]
        self._served_article_ids.add(aid)
        self._open_article_id = aid
        payloads = self._html_payloads(art.get("title") or "",
                                       self._article_source(art))
        self._record_review("article", aid, payloads,
                            str(art.get("content_hash") or ""))
        self._emit(self.article_detail, {
            "id": aid, "title": art.get("title") or "",
            "section_id": art.get("section_id"), "section": section,
            "category": category, "labels": art.get("labels") or [],
            "author": art.get("author_name") or "—",
            "draft": bool(art.get("draft")),
            "outdated": bool(art.get("outdated")),
            "position": art.get("position"),
            "html_url": art.get("html_url") or "",
            "origin": art.get("origin") or "pull",
            "source_file": art.get("source_file"),
            "updated_display": _display_date(art.get("updated_at")),
            "body_srcdoc": self._article_srcdoc(art),
            # The EXACT stored bytes — what "Copy HTML" delivers. Rendered
            # as escaped text by the SPA (never as markup), so the reviewer
            # reads the same characters the clipboard will carry.
            "body_source": payloads["body_html"]["text"],
            # Plain/markdown seed for the specialist edit textarea
            # (ArticleEditor -> BodyEditForm). Without it the editor opened
            # BLANK and a save silently replaced the whole body with only
            # what was typed.
            "body_text": self._article_body_text(art),
            "markup_notice": payloads["body_html"]["notice"],
            # WHAT diverges, item by item (dropped containers, refused or
            # merely unexpected URL schemes, and every hiding construct the
            # preview neutralized). The notice says "something"; this says
            # what, so the reviewer knows where to look in the source.
            "markup_report": payloads["body_html"].get("report") or [],
            "revisions": revisions,
        })

    def js_open_macro(self, macro_id):
        s = str(macro_id or "")
        if not _ID_RE.match(s):
            return
        conn = self._db()
        if conn is None:
            return
        from src.data import zendesk_store as store
        macro = store.get_macro(conn, int(s))
        if macro is None:
            return
        mid = macro["macro_id"]
        actions = []
        for a in macro.get("actions") or []:
            if isinstance(a, dict):
                field = str(a.get("field", ""))
                actions.append({
                    "field": field,
                    "display": _FIELD_DISPLAY.get(
                        field, field.replace("_", " ").capitalize()),
                    "value": str(a.get("value", ""))})
        revisions = []
        for r in store.list_revisions(conn, kind="macro"):
            if r.get("target_id") == mid:
                revisions.append({
                    "draft_id": r["draft_id"], "status": r["status"],
                    "name": r.get("title") or ""})
                self._served_drafts[("macro", r["draft_id"])] = r["status"]
        self._served_macro_ids.add(mid)
        # Mirror review record: the editor renders every action value
        # verbatim as escaped text, so opening a macro IS being shown the
        # exact bytes its copy fields release. Without this record
        # js_copy_field refuses the macro exactly like an unreviewed draft.
        self._record_review(
            "macro", mid,
            self._macro_payloads(macro.get("name") or "",
                                 macro.get("actions")),
            str(macro.get("content_hash") or ""))
        self._emit(self.macro_detail, {
            "id": mid, "name": macro.get("name") or "",
            "description": macro.get("description") or "",
            "active": bool(macro.get("active")),
            "updated_display": _display_date(macro.get("updated_at")),
            "actions": actions, "revisions": revisions,
            # Canonical source rendering of the same actions — the exact
            # bytes "Copy reply" delivers, shown as escaped text.
            "actions_source": _actions_source(macro.get("actions")),
        })

    def js_request_revisions(self, filter):  # noqa: A002 — bridge slot name
        f = str(filter or "")
        if f not in _REVISION_FILTERS:
            return
        self._last_filter = f
        self._push_revisions(f)

    def js_request_diff(self, kind, draft_id):
        """THE AUTHORITATIVE REVIEW SURFACE for a draft.

        ``rows`` is a diff of the exact SOURCE bytes the clipboard will
        deliver — the HTML source text for articles, the canonical actions
        JSON (``_actions_source``) for macros — so every attribute, style
        declaration, ``<script>`` body, duplicate attribute and zero-size
        span is literally on screen. A readable projection
        (``html_to_review_text`` / ``_actions_plain``, both sides
        like-for-like) ships as ``text_rows``, explicitly SECONDARY: it
        drops things by construction and must never be the only thing a
        reviewer sees before a copy.

        Emitting the diff RECORDS the review: hashes of every exact string
        ``_copy_bundle`` can release for this draft. ``_resolve_copy``
        releases nothing whose hash is absent, so nothing can be present in
        the copied bytes without having been present, character for
        character, in this diff.

        ``warning`` is set when the source diff reports no changed line
        although the bytes differ from the baseline — a diff bug, surfaced
        instead of an innocent "0 changed lines"."""
        kind = str(kind or "")
        if kind not in _DRAFT_KINDS:
            return
        did = self._served_draft_id(kind, draft_id)
        if did is None:
            return
        conn = self._db()
        if conn is None:
            return
        from src.data import zendesk_store as store
        from src.data.text_diff import change_count, diff_words
        target = _DRAFT_TARGET[kind]
        bundle = self._copy_bundle(conn, target, did)
        if bundle is None:
            return
        payloads, content_hash, d = bundle
        if kind == "article":
            base_row = (store.get_article(conn, d["article_id"])
                        if d.get("article_id") else None)
            base_src = ("" if base_row is None
                        else self._article_source(base_row))
            # EXACTLY the bytes _resolve_copy will hand to the clipboard —
            # never a field the copy path ignores.
            new_src = payloads["body_html"]["text"]
            base_text = store.html_to_review_text(base_src)
            new_text = store.html_to_review_text(new_src)
            old_title = (base_row or {}).get("title") or ""
            new_title = payloads["title"]["text"]
        else:
            base_row = (store.get_macro(conn, d["macro_id"])
                        if d.get("macro_id") else None)
            base_actions = (base_row or {}).get("actions")
            base_src = "" if base_row is None else _actions_source(base_actions)
            new_src = _actions_source(d.get("actions"))
            base_text = ("" if base_row is None
                         else _actions_plain(base_actions))
            new_text = _actions_plain(d.get("actions"))
            old_title = (base_row or {}).get("name") or ""
            new_title = payloads["macro_name"]["text"]
        rows = diff_words(base_src, new_src)
        n_changed = change_count(rows)
        bytes_equal = base_src == new_src
        text_rows = diff_words(base_text, new_text)
        notice = next((p["notice"] for p in payloads.values()
                       if p.get("notice")), "")
        report = next((p["report"] for p in payloads.values()
                       if p.get("report")), [])
        self._record_review(target, did, payloads, content_hash)
        self._req_seq += 1
        self._emit(self.diff_ready, {
            "request_id": f"d-{self._req_seq}", "kind": kind, "draft_id": did,
            "baseline_present": base_row is not None,
            "change_count": n_changed,
            "bytes_equal": bytes_equal,
            "warning": (_ZERO_CHANGE_WARNING
                        if (n_changed == 0 and not bytes_equal) else None),
            "markup_notice": notice,
            "markup_report": list(report),
            "title": {"changed": old_title != new_title,
                      "old": old_title, "new": new_title},
            "rows": rows,                       # SOURCE — authoritative
            "text_rows": text_rows,             # readable — SECONDARY
            "text_change_count": change_count(text_rows),
        })

    def js_save_draft(self, kind, draft_id, payload_json):
        """RENAME a pending/ready draft. Payload keys are a HARD allowlist —
        article: {title}; macro: {name, description} — every other key is
        silently dropped. The SPA only renames; draft CONTENT (body /
        body_html / actions) is deliberately NOT page-writable: the review
        diff is computed from it and the clipboard delivers it, so a
        page-written body would ride attacker content past the review gate
        (reviewed bytes and copied bytes must never diverge). Status keys
        stay dropped too, so a page script can never ride a status
        transition past the js_mark_* gates.

        Title/name ARE copy fields, so the recorded review is dropped BEFORE
        the write is attempted: dropping a review can only make a copy
        harder, so doing it first means a write that fails halfway can never
        leave a stale review blessing bytes it did not show. Copying the
        result then takes the universal draft-copy confirm regardless
        (module docstring, item 6) — no authorship question is asked.

        E1: the macro branch writes ``name`` and ``description`` in ONE
        transaction. They used to be two separately-committed writes, so a
        payload whose description sqlite refuses to bind (a lone unpaired
        surrogate survives ``json.loads`` and dies at the bind) committed
        the page's new NAME and then raised — a partially-applied page
        write, with the controller's post-write bookkeeping skipped."""
        if self._frozen():
            return
        kind = str(kind or "")
        if kind not in _DRAFT_KINDS:
            return
        did = self._served_draft_id(kind, draft_id)
        if did is None:
            return
        try:
            payload = json.loads(str(payload_json or ""))
        except (ValueError, TypeError):
            return
        if not isinstance(payload, dict):
            return
        conn = self._db()
        if conn is None:
            return
        from src.data import zendesk_store as store
        target = _DRAFT_TARGET[kind]
        # Drop the recorded review FIRST — fail-closed ordering, so a write
        # that raises part-way through cannot leave the old review in place.
        self._reviewed.pop((target, did), None)
        try:
            if kind == "article":
                d = store.get_article_draft(conn, did)
                # PRECONDITION: the draft's CURRENT DB status is editable —
                # copied/pushed rows are immutable from every surface.
                if d is None or d.get("status") not in ("pending", "ready"):
                    return
                if not isinstance(payload.get("title"), str):
                    return
                store.update_article_draft(
                    conn, did, title=payload["title"][:_TITLE_CAP])
            else:
                d = store.get_macro_draft(conn, did)
                if d is None or d.get("status") not in ("pending", "ready"):
                    return
                name = payload.get("name")
                desc = payload.get("description")
                if not isinstance(name, str) and not isinstance(desc, str):
                    return
                # ONE transaction for both fields (E1). update_macro_draft's
                # own _txn passes through when a transaction is already open,
                # and the description UPDATE hits the same table, so a bind
                # failure on either rolls BOTH back — a page rename is
                # applied whole or not at all.
                from datetime import datetime, timezone
                with store._txn(conn):  # noqa: SLF001
                    if isinstance(name, str):
                        res = store.update_macro_draft(
                            conn, did, name=name[:_TITLE_CAP])
                        if not res.get("ok"):
                            raise RuntimeError(res.get("error"))
                    if isinstance(desc, str):
                        # update_macro_draft has no description kwarg.
                        conn.execute(
                            "UPDATE zendesk_macro_drafts SET description=?, "
                            "updated_at=? WHERE id=?",
                            (desc[:_TITLE_CAP],
                             datetime.now(timezone.utc).isoformat(), did))
        except Exception:  # noqa: BLE001 — a failed save must not crash the tab
            return
        self._push_revisions(self._last_filter)

    def js_save_body_edit(self, target_kind, target_id, payload):
        """Specialist body edit — articles only, v1. The ONLY page-writable
        content path, and safe ONLY because of the recompute invariant
        (do not weaken — this closes review finding C1's class): the payload's
        ``body`` key (markdown/plain text) is the single honored input, and
        ``body_html`` is ALWAYS recomputed Python-side as
        ``sanitize_html(markdown_to_html(body))`` whenever body changes.
        Page-supplied HTML (body_html/html/rich keys) is IGNORED, so the
        reviewed diff (js_request_diff projects body_html when set with
        html_to_review_text), the stored body_html, and the clipboard copy
        can never diverge. The edit also DROPS the draft's recorded review
        (see ``_finish_body_edit``) — content the specialist has not seen
        cannot be copied — and the resulting draft, like every draft,
        reaches the clipboard only behind the native confirm that displays
        those exact bytes (module docstring, item 6).

        target_kind 'draft': target_id is an article draft id; only
        status=='pending' drafts are editable (ready/copied/pushed → silent
        no-op + status_text explaining). target_kind 'article': target_id is
        a mirror article id — the mirror row is NEVER modified (the baseline
        stays hash-faithful to remote); the edit updates the open pending
        source_ref='specialist-edit' draft targeting it, or creates one."""
        if self._frozen():
            return
        target_kind = str(target_kind or "")
        if target_kind not in _BODY_EDIT_KINDS:
            return
        try:
            parsed = json.loads(str(payload or ""))
        except (ValueError, TypeError):
            return
        if not isinstance(parsed, dict):
            return
        body = parsed.get("body")       # the ONLY honored payload key
        if not isinstance(body, str):
            return
        body = body[:_BODY_CAP]
        conn = self._db()
        if conn is None:
            return
        from src.data import zendesk_store as store
        from src.data.html_markdown import markdown_to_html
        from src.data.html_sanitize import sanitize_html
        # THE INVARIANT: page can never supply HTML — recompute + sanitize.
        body_html = sanitize_html(markdown_to_html(body))

        if target_kind == "draft":
            did = self._served_draft_id("article", target_id)
            if did is None:
                return
            try:
                d = store.get_article_draft(conn, did)
            except Exception:  # noqa: BLE001
                return
            if d is None:
                return
            if d.get("status") != _EDITABLE_DRAFT_STATUS:
                # No DB change; explain on the trusted status surface.
                self.set_status(
                    f"Revision {did} is {d.get('status')} - only pending "
                    "revisions can be edited.")
                return
            try:
                res = store.update_article_draft(conn, did, body=body,
                                                 body_html=body_html)
                if not res.get("ok"):
                    raise RuntimeError(res.get("error"))
            except Exception:  # noqa: BLE001
                self._resolve_body_edit(False, target="draft",
                                        draft_id=did, error="store_error")
                return
            self._finish_body_edit(did, "draft", d.get("article_id"))
            return

        # target_kind == 'article': edits land in the revision layer only.
        s = str(target_id or "")
        if not _ID_RE.match(s):
            return
        aid = int(s)
        if aid not in self._served_article_ids:
            return
        try:
            art = store.get_article(conn, aid)
        except Exception:  # noqa: BLE001
            return
        if art is None:
            return
        try:
            row = conn.execute(
                "SELECT id FROM zendesk_article_drafts WHERE article_id=? "
                "AND status='pending' AND source_ref=? ORDER BY id LIMIT 1",
                (aid, _SPECIALIST_REF)).fetchone()
            if row is not None:
                did = int(row[0])
                res = store.update_article_draft(conn, did, body=body,
                                                 body_html=body_html)
                if not res.get("ok"):
                    raise RuntimeError(res.get("error"))
            else:
                did = store.save_article_draft(
                    conn, title=art.get("title") or "", body=body,
                    article_id=aid, source_ref=_SPECIALIST_REF,
                    rationale=_SPECIALIST_RATIONALE, body_html=body_html)
        except Exception:  # noqa: BLE001
            self._resolve_body_edit(False, target="article",
                                    draft_id=None, error="store_error")
            return
        self._served_drafts[("article", did)] = "pending"
        self._finish_body_edit(did, "article", aid)

    def _resolve_body_edit(self, ok, *, target, draft_id, error=None):
        self._req_seq += 1
        self._emit(self.action_resolved, {
            "request_id": f"a-{self._req_seq}", "action": "body_edit",
            "ok": ok, "kind": "article", "draft_id": draft_id,
            "target": target, "error": error})

    def _finish_body_edit(self, draft_id, target, article_id):
        """Success emissions: revisions re-push (last filter), article
        detail re-push when the edited target is the open article, then
        the action_resolved receipt.

        Layer 3 FIRST: the draft's content just changed, so any diff still
        on screen for it is stale. Dropping the recorded review means that
        stale diff can no longer authorize a copy — the specialist has to
        re-open it (which re-records the review) before the clipboard will
        release the new bytes. Re-opening it is page-callable though, so the
        review record is NOT what protects these bytes; the universal
        draft-copy confirm is, and it displays them."""
        self._reviewed.pop(("article_draft", draft_id), None)
        self._push_revisions(self._last_filter)
        if article_id is not None and article_id == self._open_article_id:
            self.js_open_article(str(article_id))
        self._resolve_body_edit(True, target=target, draft_id=draft_id)

    def js_mark_ready(self, kind, draft_id):
        """pending → ready only, validated against the CURRENT DB status."""
        self._transition(kind, draft_id, "ready", allowed_from=("pending",))

    def js_mark_copied(self, kind, draft_id):
        """pending|ready → copied (sets copied_at in the store)."""
        self._transition(kind, draft_id, "copied",
                         allowed_from=("pending", "ready"))

    def js_copy_field(self, target, target_id, field):
        """Copy exact: re-reads the EXACT DB bytes at click time (never the
        page's copy of the text) and hands them to the Python-side clipboard.

        Every release of MARKUP (``body_html`` / ``body_rich`` /
        ``macro_reply``, mirror rows included) and every release from a
        DRAFT takes the native confirm that displays the exact bytes
        (``_resolve_copy`` → ``_confirm_copy_release``); if it is absent or
        declined, ``_resolve_copy`` returns None and NOTHING is emitted —
        no clipboard write and no ``copy_resolved`` receipt. Only mirror
        titles and macro names copy under the review record alone.

        Every successful copy is ALSO announced on the native status line
        via the compat set_status, a surface the page cannot forge —
        including the ``_MARKUP_NOTICE`` when the released bytes carry
        markup the preview cannot display faithfully.

        THE GATE GOVERNS THE CLIPBOARD, NOT ONLY THE RELEASE (F2). Two
        belts, because "the human approved these bytes" is worthless if
        different bytes are on the clipboard by the time they paste:

        * this slot is FROZEN like every other mutating slot, so a page
          script cannot write the clipboard from inside the nested event
          loop of an open native modal (the observed exploit fired an
          un-gated MIRROR copy while the draft dialog was still up);
        * an approved GATED release arms ``_COPY_HOLD_S`` during which no
          copy that carries no gate of its own — since 2026-07-27 that is
          only a mirror title / macro name, which the page self-serves
          through js_open_article — may overwrite it. A gated copy may, and
          pays for its own confirm."""
        if self._frozen():
            # a native modal or a pull/import owns this moment
            self.set_status(_COPY_REFUSED)
            return
        target = str(target or "")
        fields = _COPY_FIELDS.get(target)
        if fields is None or str(field or "") not in fields:
            return
        field = str(field)
        tid = self._copy_target_id(target, target_id)
        if tid is None:
            return
        gated = _copy_needs_confirm(target, field)
        if not gated and self._now() < self._copy_hold_until:
            # the operator's approved bytes are still the clipboard's
            self.set_status(_COPY_REFUSED)
            return
        conn = self._db()
        if conn is None:
            return
        resolved = self._resolve_copy(conn, target, tid, field)
        if resolved is None:
            return
        text, html, sanitized, notice = resolved
        ok = False
        if self._clipboard_fn is not None:
            try:
                ok = bool(self._clipboard_fn(text, html))
            except Exception:  # noqa: BLE001
                ok = False
        if ok and gated:
            self._copy_hold_until = self._now() + _COPY_HOLD_S
        self._req_seq += 1
        self._emit(self.copy_resolved, {
            "request_id": f"c-{self._req_seq}", "target": target,
            "target_id": tid, "field": field, "ok": ok,
            "chars": len(text), "sanitized": sanitized, "notice": notice})
        if ok:
            note = (" (rich copy sanitized: script/iframe content removed)"
                    if sanitized else "")
            warn = f" - WARNING: {notice}" if notice else ""
            self.set_status(
                f"Copied {target} {tid} {field} - "
                f"{len(text):,} chars{note}{warn}")

    def js_request_import(self):
        """Manual file import. The ``_pull_inflight`` claim is taken BEFORE
        ``file_pick_fn`` runs — the native picker spins a nested event loop,
        so a re-entrant bridge invoke must already find the claim held."""
        self._start_import(self._pick_files)

    def js_request_import_folder(self):
        self._start_import(self._pick_folder)

    def js_request_pull(self):
        """Read-only API pull (GET only, non-destructive → no confirm,
        locked decision). Python-side cooldown: silently refused within 60s
        of the last pull completion so an untrusted slot can never drive
        unbounded authenticated traffic."""
        if self._frozen():
            return
        if (self._last_pull_done is not None
                and self._now() - self._last_pull_done
                < self._pull_cooldown_s):
            return
        self._pull_inflight = True
        started = False
        if self._pull_runner is not None:
            try:
                started = bool(self._pull_runner())
            except Exception:  # noqa: BLE001
                started = False
        if not started:
            # Never wedge pullBusy: release the claim and tell the page.
            self._pull_inflight = False
            self._emit(self.pull_resolved,
                       {"ok": False, "error": "not_started"})

    def js_delete_revision(self, kind, draft_id):
        """DESTRUCTIVE gate — the workbench pattern verbatim: validate
        against Python-held state → claim BEFORE the modal → native confirm
        (fail closed) → re-verify existence + status → dispatch → resolve."""
        kind = str(kind or "")
        if kind not in _DRAFT_KINDS:
            return
        did = self._served_draft_id(kind, draft_id)
        if did is None:
            return
        if self._frozen():
            return
        conn = self._db()
        if conn is None:
            return
        from src.data import zendesk_store as store
        getter = (store.get_article_draft if kind == "article"
                  else store.get_macro_draft)
        try:
            row = getter(conn, did)
        except Exception:  # noqa: BLE001
            row = None
        if row is None:
            return
        status_before = row.get("status")
        ok = False
        self._action_inflight = True
        try:
            self._req_seq += 1
            request_id = f"a-{self._req_seq}"
            approved = self._confirm(
                "Delete revision",
                "Delete this revision? This cannot be undone.")
            error = None
            if approved:
                # Re-verify after the modal: the row must still exist with
                # the status the operator was shown.
                try:
                    now_row = getter(conn, did)
                except Exception:  # noqa: BLE001
                    now_row = None
                if now_row is None:
                    error = "draft_not_found"
                elif now_row.get("status") != status_before:
                    error = "status_changed"
                else:
                    try:
                        res = store.delete_draft(conn, kind, did)
                        ok = bool(res.get("ok"))
                        error = res.get("error")
                    except Exception:  # noqa: BLE001 — resolves un-dispatched
                        ok, error = False, "store_error"
            self._emit(self.action_resolved, {
                "request_id": request_id, "action": "delete_revision",
                "kind": kind, "draft_id": did, "ok": ok,
                "approved": approved, "error": error})
        finally:
            self._action_inflight = False
        if ok:
            self._served_drafts.pop((kind, did), None)
            self._reviewed.pop((_DRAFT_TARGET[kind], did), None)
            self._push_revisions(self._last_filter)

    def js_purge_mirror(self, scope):
        """DESTRUCTIVE gate over the whole mirror. Scope-specific confirm
        text with live counts; counts are RECOMPUTED at dispatch (the store
        reports actual deleted rowcounts) and those recomputed counts are
        what action_resolved carries. copied/pushed draft rows are excluded
        from purge entirely (store rule — the audit trail survives)."""
        scope = str(scope or "")
        if scope not in _PURGE_SCOPES:
            return
        if self._frozen():
            return
        conn = self._db()
        if conn is None:
            return
        from src.data import zendesk_store as store
        ok = False
        self._action_inflight = True
        try:
            self._req_seq += 1
            request_id = f"a-{self._req_seq}"
            approved = self._confirm("Purge mirror",
                                     self._purge_text(conn, scope))
            error = None
            counts = {}
            if approved:
                try:
                    res = store.purge_mirror(conn, scope=scope)
                    ok = bool(res.get("ok"))
                    if ok:
                        counts = {k: v for k, v in res.items() if k != "ok"}
                    else:
                        error = res.get("error")
                except Exception:  # noqa: BLE001
                    ok, error = False, "store_error"
            self._emit(self.action_resolved, {
                "request_id": request_id, "action": "purge_mirror",
                "scope": scope, "ok": ok, "approved": approved,
                "error": error, "counts": counts})
        finally:
            self._action_inflight = False
        if ok:
            self._served_article_ids.clear()
            self._served_macro_ids.clear()
            # Purge deletes pending/ready drafts and SQLite reuses rowids —
            # a recorded review must never survive to bless a new draft that
            # inherits a deleted one's id.
            self._reviewed.clear()
            self._serve_data()
            self._push_revisions(self._last_filter)

    # ── internals ────────────────────────────────────────────────────
    def _db(self):
        if self._conn_fn is None:
            return None
        try:
            return self._conn_fn()
        except Exception:  # noqa: BLE001
            return None

    def _now(self) -> float:
        if self._now_fn is not None:
            try:
                return float(self._now_fn())
            except Exception:  # noqa: BLE001
                pass
        return time.time()

    def _frozen(self) -> bool:
        """Confused-deputy freeze: every mutating slot refuses while a
        destructive modal OR a pull/import is inflight."""
        return self._action_inflight or self._pull_inflight

    def _confirm(self, title, text, content=None, notes=None) -> bool:
        """Native confirm; absent fn or a broken dialog means NO.

        ``content`` carries bytes the operator MUST SEE ON SCREEN (the copy
        gate's exact clipboard payload). A host that declares it renders it
        in a VISIBLE, scrollable, read-only view — never a collapsed
        disclosure (page.py's ``_build_copy_confirm_dialog``); a host that
        does not still gets the bytes, appended to the message, so they are
        never dropped. Which of the two applies is decided by introspection,
        not by catching a TypeError, so a dialog can never be shown twice.

        ``notes`` is the markup report, kept OUT of the heading. It used to
        be joined into it, and the report is attacker-reachable: a page could
        drive a body whose sanitizer report grew the heading from five lines
        to eight and forged its line structure — defeating the very property
        the title's whitespace collapse exists to protect. A host that
        declares the fourth parameter renders the report in its own widget;
        one that does not gets it appended AFTER the fixed lines, which is
        safe now that every entry is flattened and capped at construction."""
        if self._confirm_fn is None:
            return False
        notes = [str(n) for n in (notes or [])]
        try:
            if content is None:
                return bool(self._confirm_fn(title, text))
            if self._confirm_shows_content():
                if notes and self._confirm_shows_notes():
                    return bool(self._confirm_fn(title, text, content, notes))
                return bool(self._confirm_fn(
                    title, self._with_notes(text, notes), content))
            return bool(self._confirm_fn(
                title, f"{self._with_notes(text, notes)}\n\n{content}"))
        except Exception:  # noqa: BLE001
            return False

    @staticmethod
    def _with_notes(text, notes) -> str:
        """The fallback rendering: the report AFTER the fixed lines, never
        interleaved with them."""
        if not notes:
            return text
        return f"{text}\n\n{_MARKUP_REPORT_LEAD}: " + "; ".join(notes)

    def _confirm_shows_content(self) -> bool:
        """Whether the injected host declares the content-showing form.

        Arity alone is NOT enough, and that was a live hazard: any
        three-positional callable passed the old check, so a host whose third
        parameter meant something else entirely — ``(title, text,
        parent=None)`` is the obvious one — would have been handed the
        clipboard payload as a parent widget and shown the operator NOTHING.
        The parameter NAME must be one this contract defines, and ``*args``
        (whose names cannot be inspected) fails closed to the append path,
        where the bytes are still visible in the message."""
        return self._confirm_param(2) in _CONFIRM_CONTENT_PARAMS

    def _confirm_shows_notes(self) -> bool:
        """Whether the injected host declares the separate REPORT parameter
        (checked by name, for the same reason ``content`` is)."""
        return self._confirm_param(3) in _CONFIRM_NOTES_PARAMS

    def _confirm_param(self, index):
        """Name of the host's Nth positional parameter, or None."""
        import inspect
        try:
            sig = inspect.signature(self._confirm_fn)
        except (TypeError, ValueError):
            return None
        positional = [p for p in sig.parameters.values()
                      if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
        if len(positional) <= index:
            return None
        return positional[index].name

    def _emit(self, signal, payload: dict):
        try:
            signal.emit(json.dumps(payload, default=str))
        except Exception:  # noqa: BLE001 — never crash the feed path
            pass

    def _connected(self) -> bool:
        if self._demo:
            return False
        if self._connected_cache is None:
            self._connected_cache = _probe_connected()
        return self._connected_cache

    def _served_draft_id(self, kind, draft_id):
        """Resolve an untrusted draft id against the last-served revisions
        (forged draft ids never reach the store)."""
        s = str(draft_id or "")
        if not s.isdigit():
            return None
        did = int(s)
        return did if (kind, did) in self._served_drafts else None

    def _copy_target_id(self, target, target_id):
        """Resolve a copy target id against THAT kind's last-served rows."""
        s = str(target_id or "")
        if not _ID_RE.match(s):
            return None
        tid = int(s)
        if target == "article":
            return tid if tid in self._served_article_ids else None
        if target == "macro":
            return tid if tid in self._served_macro_ids else None
        kind = "article" if target == "article_draft" else "macro"
        return tid if (kind, tid) in self._served_drafts else None

    def _transition(self, kind, draft_id, status, *, allowed_from):
        if self._frozen():
            return
        kind = str(kind or "")
        if kind not in _DRAFT_KINDS:
            return
        did = self._served_draft_id(kind, draft_id)
        if did is None:
            return
        conn = self._db()
        if conn is None:
            return
        from src.data import zendesk_store as store
        getter = (store.get_article_draft if kind == "article"
                  else store.get_macro_draft)
        try:
            d = getter(conn, did)
        except Exception:  # noqa: BLE001
            return
        # Validate against the CURRENT DB status; ``expected`` makes a
        # concurrent change between read and write a silent store refusal.
        if d is None or d.get("status") not in allowed_from:
            return
        try:
            res = store.set_draft_status(conn, kind, did, status,
                                         expected=d.get("status"))
        except Exception:  # noqa: BLE001
            return
        if res.get("ok"):
            self._served_drafts[(kind, did)] = status
            self._push_revisions(self._last_filter)

    # ── viewmodel builders ───────────────────────────────────────────
    def _serve_data(self, query=None, search_kind="all"):
        payload = self._build_data(query=query, search_kind=search_kind)
        for a in payload["articles"]:
            self._served_article_ids.add(a["id"])
        for m in payload["macros"]:
            self._served_macro_ids.add(m["id"])
        self._emit(self.zendesk_data, payload)

    def _build_data(self, query=None, search_kind="all") -> dict:
        payload = {
            "view": self._view, "connected": self._connected(),
            "demo": self._demo,
            "counts": {"articles": 0, "macros": 0, "revisions_open": 0},
            "categories": [], "articles": [], "macros": [],
            "last_pull_display": _display_date(_last_pull_iso()) or "Never",
            "status": self._status,
        }
        if query is not None:
            payload["query"] = query
        conn = self._db()
        if conn is None:
            return payload
        from src.data import zendesk_store as store
        try:
            payload["counts"] = self._counts(conn)
            payload["categories"] = self._category_tree(conn, store)
            open_arts = self._open_rev_map(
                conn, "zendesk_article_drafts", "article_id")
            open_macros = self._open_rev_map(
                conn, "zendesk_macro_drafts", "macro_id")
            sections = {s["section_id"]: s for s in store.list_sections(conn)}
            # Under a search the served lists are AUTHORITATIVE AND COMPLETE
            # for the query (contract with the SPA): rows come straight from
            # the FTS hit ids, fetched by id in rank order — NEVER
            # intersected with the 500-row browse slice, whose recency cap
            # would silently drop older matches.
            hits = (store.search_mirror(conn, query, kind=search_kind,
                                        limit=100)
                    if query is not None else None)
            if hits is not None and search_kind in ("all", "articles"):
                art_rows = [r for r in
                            (store.get_article(conn, h["id"])
                             for h in hits.get("articles", []))
                            if r is not None]
            else:
                art_rows = store.list_articles_full(conn)
            articles = [self._article_vm(row, sections, open_arts)
                        for row in art_rows]
            if hits is not None and search_kind in ("all", "macros"):
                mac_rows = [r for r in
                            (store.get_macro(conn, h["id"])
                             for h in hits.get("macros", []))
                            if r is not None]
            else:
                mac_rows = [dict(r) for r in conn.execute(
                    "SELECT macro_id, name, description, active, updated_at "
                    "FROM zendesk_macros "
                    "ORDER BY updated_at DESC, macro_id LIMIT 500").fetchall()]
            macros = [self._macro_vm(row, open_macros) for row in mac_rows]
            payload["articles"] = articles
            payload["macros"] = macros
        except Exception:  # noqa: BLE001 — a broken mirror degrades to empty
            pass
        return payload

    def _article_vm(self, row, sections, open_map) -> dict:
        """One article list row (browse slice AND search hits share this
        shape — the SPA must never see two dialects)."""
        sect = sections.get(row.get("section_id")) or {}
        labels = row.get("labels")
        if not isinstance(labels, list):
            try:
                labels = json.loads(row.get("labels_json") or "[]")
            except (ValueError, TypeError):
                labels = []
        return {
            "id": row["article_id"],
            "title": row.get("title") or "",
            "section_id": row.get("section_id"),
            "section": sect.get("name") or "",
            "author": row.get("author_name") or "—",
            "updated_display": _display_date(row.get("updated_at")),
            "draft": bool(row.get("draft")),
            "outdated": bool(row.get("outdated")),
            "labels": labels,
            "origin": row.get("origin") or "pull",
            "open_revisions": open_map.get(row["article_id"], 0)}

    def _macro_vm(self, row, open_map) -> dict:
        return {
            "id": row["macro_id"], "name": row.get("name") or "",
            "description": row.get("description") or "",
            "active": bool(row.get("active")),
            "updated_display": _display_date(row.get("updated_at")),
            "open_revisions": open_map.get(row["macro_id"], 0)}

    def _counts(self, conn) -> dict:
        arts = conn.execute(
            "SELECT COUNT(*) FROM zendesk_articles").fetchone()[0]
        macs = conn.execute(
            "SELECT COUNT(*) FROM zendesk_macros").fetchone()[0]
        open_rev = conn.execute(
            "SELECT (SELECT COUNT(*) FROM zendesk_article_drafts "
            "WHERE status IN ('pending','ready')) + "
            "(SELECT COUNT(*) FROM zendesk_macro_drafts "
            "WHERE status IN ('pending','ready'))").fetchone()[0]
        return {"articles": arts, "macros": macs, "revisions_open": open_rev}

    def _open_rev_map(self, conn, table, fk) -> dict:
        rows = conn.execute(
            f"SELECT {fk}, COUNT(*) FROM {table} "                # noqa: S608
            f"WHERE status IN ('pending','ready') AND {fk} IS NOT NULL "
            f"GROUP BY {fk}").fetchall()
        return {r[0]: r[1] for r in rows}

    def _category_tree(self, conn, store) -> list[dict]:
        counts = {r[0]: r[1] for r in conn.execute(
            "SELECT section_id, COUNT(*) FROM zendesk_articles "
            "GROUP BY section_id").fetchall()}
        by_cat: dict = {}
        orphans = []
        cats = store.list_categories(conn)
        known = {c["category_id"] for c in cats}
        for s in store.list_sections(conn):
            entry = {"id": s["section_id"], "name": s.get("name") or "",
                     "article_count": counts.get(s["section_id"], 0)}
            if s.get("category_id") in known:
                by_cat.setdefault(s["category_id"], []).append(entry)
            else:
                orphans.append(entry)
        tree = [{"id": c["category_id"], "name": c.get("name") or "",
                 "sections": by_cat.get(c["category_id"], [])} for c in cats]
        if orphans:
            tree.append({"id": None, "name": "Other", "sections": orphans})
        return tree

    def _section_names(self, conn, section_id):
        if section_id is None:
            return "", ""
        row = conn.execute(
            "SELECT s.name, c.name FROM zendesk_sections s "
            "LEFT JOIN zendesk_categories c ON c.category_id = s.category_id "
            "WHERE s.section_id=?", (section_id,)).fetchone()
        if row is None:
            return "", ""
        return row[0] or "", row[1] or ""

    @staticmethod
    def _article_source(art: dict) -> str:
        """The EXACT stored article bytes — one definition, used by the
        preview, the review diff and the clipboard alike so the three can
        never read different fields."""
        raw = art.get("body_html")
        if raw is None:
            raw = art.get("body") or ""
        return str(raw)

    @staticmethod
    def _article_body_text(art: dict) -> str:
        """The mirror body as plain/markdown text — the seed for the
        specialist edit textarea, NOT a review surface.

        Markdown (not a flat text projection) because a save round-trips it
        back through ``sanitize_html(markdown_to_html(body))``: seeding with
        markdown means an untouched save reproduces the article's structure
        instead of flattening it. The stored ``body_text`` FTS projection is
        the fallback when the converter cannot run."""
        from src.data.html_markdown import html_to_markdown
        try:
            return html_to_markdown(
                ZendeskWebController._article_source(art))
        except Exception:  # noqa: BLE001 — a seed must never break the detail
            return str(art.get("body_text") or "")

    def _article_srcdoc(self, art: dict) -> str:
        """The ONLY producer of preview HTML — EVERY path (mirror body_html,
        legacy body, markdown fallback) passes a sanitizer.

        The RENDERED view, and deliberately the wider ``sanitize_html_preview``
        profile: the owner reads this to judge how an article will look to an
        end user in Zendesk, so classes, ids, data-attributes, style and table
        scaffolding have to survive. They are inert inside the ``sandbox=""``
        iframe the SPA renders this in (no scripts, no navigation, no form
        submission), and scripts / event handlers / active URL schemes are
        still removed — what IS removed is exactly what ``markup_notice``
        warns about.

        This string is a PREVIEW ONLY. It is never a clipboard flavour and
        never stored; those paths keep the strict ``sanitize_html``."""
        from src.data.html_sanitize import sanitize_html_preview
        body = sanitize_html_preview(self._article_source(art))
        return (f"<style>{_ARTICLE_PREVIEW_CSS}</style>"
                f'<div class="alma-hc-article">{body}</div>')

    def _editable_draft_bodies(self, conn) -> dict:
        """``(kind, draft_id) -> stored body`` for the drafts whose body the
        specialist edit form seeds from — i.e. exactly the drafts
        ``js_save_body_edit`` accepts (pending ARTICLE drafts, v1).

        Served nowhere else on purpose: the feed carries up to 500 rows and
        a body is capped at 200k chars, so shipping every body would be a
        multi-megabyte push per revision refresh. Keyed off the same
        constant the save slot validates against, so the two cannot drift
        into "the editor opens blank again"."""
        out: dict[tuple[str, int], str] = {}
        try:
            rows = conn.execute(
                "SELECT id, body FROM zendesk_article_drafts WHERE status=?",
                (_EDITABLE_DRAFT_STATUS,)).fetchall()
        except Exception:  # noqa: BLE001 — a broken mirror degrades to blank
            return out
        for r in rows:
            out[("article", int(r[0]))] = str(r[1] or "")[:_BODY_CAP]
        return out

    def _push_revisions(self, filt: str):
        conn = self._db()
        if conn is None:
            return
        from src.data import zendesk_store as store
        try:
            rows = store.list_revisions(conn)
        except Exception:  # noqa: BLE001
            rows = []
        bodies = self._editable_draft_bodies(conn)
        wanted = {"open": ("pending", "ready"),
                  "copied": ("copied", "pushed"),
                  "all": ("pending", "ready", "copied", "pushed")}[filt]
        out = []
        for r in rows:
            if r.get("status") not in wanted:
                continue
            try:
                sources = json.loads(r.get("sources_json") or "[]")
            except (ValueError, TypeError):
                sources = []
            if not isinstance(sources, list):
                sources = []
            out.append({
                "draft_id": r["draft_id"], "kind": r["kind"],
                "target_id": r.get("target_id"),
                "target_title": r.get("target_title"),
                "title": r.get("title") or "", "status": r.get("status"),
                # The seed for the draft body editor (RevisionCenter ->
                # BodyEditForm). Empty for drafts no edit would be accepted
                # for — the form is not offered for those.
                "body": bodies.get((r["kind"], r["draft_id"]), ""),
                "rationale": r.get("rationale"), "sources": sources,
                "source_ref": r.get("source_ref"),
                "created_display": _display_date(r.get("created_at")),
                "copied_display": _display_date(r.get("copied_at")) or None,
                "is_new": r.get("target_id") is None})
            self._served_drafts[(r["kind"], r["draft_id"])] = r.get("status")
        self._emit(self.revisions_data, {"filter": filt, "revisions": out})

    # ── copy-exact resolution + the reviewed-bytes gate ──────────────
    @staticmethod
    def _html_payloads(title: str, raw: str) -> dict:
        """Releasable payloads for an HTML-bodied row (mirror article or
        article draft), keyed by copy field.

        Each payload is ``{text, html|None, sanitized, notice}``: ``text``
        is the plain flavour handed to the clipboard, ``html`` the
        text/html mime flavour (None when the field pastes as text).
        ``body_rich`` always passes the STRICT ``sanitize_html`` —
        markdown_to_html and the byte-faithful pull both forward markup we
        do not vouch for, so the mime flavour is defanged before it can
        reach the live Zendesk editor as formatted paste. The clipboard
        never sees the wider preview profile.

        ``notice``/``report`` are measured against the PREVIEW profile
        instead: they fire when the sandboxed preview and the bytes are not
        the same picture — something could not be displayed (a dropped
        script/style/form container, a stripped handler, an unexpected URL
        scheme) OR something the source hides is displayed anyway (the
        preview neutralizes every hiding construct it can recognise). Not
        merely because the strict profile would have thrown away a class
        attribute. ``report`` names each item so the surface can say WHAT
        was hidden, not only that something was."""
        from src.data.html_sanitize import sanitize_html, sanitize_html_preview
        raw = "" if raw is None else str(raw)
        safe = sanitize_html(raw)
        _preview, report = sanitize_html_preview(raw, report=True)
        notice = _MARKUP_NOTICE if report else ""
        return {
            "title": {"text": title or "", "html": None,
                      "sanitized": False, "notice": "", "report": []},
            # Plain-text HTML source: byte-verbatim (pastes as text).
            "body_html": {"text": raw, "html": None, "sanitized": False,
                          "notice": notice, "report": list(report)},
            "body_rich": {"text": raw, "html": safe,
                          "sanitized": safe != raw, "notice": notice,
                          "report": list(report)},
        }

    @staticmethod
    def _macro_payloads(name: str, actions) -> dict:
        """Releasable payloads for a macro row/draft. Macro fields are
        plain text on both flavours — the reply is copied as the exact
        stored action value, which the macro editor and the source diff
        both render verbatim.

        The reply carries a ``notice`` on the same honesty rule as an
        article body: a pull-origin macro is byte-faithful to remote
        Zendesk, so a ``comment_value_html`` full of script/handler markup
        is copyable verbatim and the operator is told so instead of being
        left to spot it. Unlike an article body, a reply is frequently
        PLAIN TEXT, where sanitize_html differs purely by entity-escaping
        ("Billing & Claims") and nothing is hidden — hence the extra
        tag-start requirement (``_MARKUP_TAG_RE``)."""
        out = {"macro_name": {"text": name or "", "html": None,
                              "sanitized": False, "notice": "", "report": []}}
        reply = _first_reply(actions)
        if reply is not None:
            from src.data.html_sanitize import sanitize_html
            notice = ""
            report = []
            if (sanitize_html(reply) != reply
                    and _MARKUP_TAG_RE.search(reply)):
                notice = _MACRO_MARKUP_NOTICE
                report = [_MACRO_MARKUP_NOTICE]
            out["macro_reply"] = {"text": reply, "html": None,
                                  "sanitized": False, "notice": notice,
                                  "report": report}
        return out

    def _copy_bundle(self, conn, target, tid):
        """``(payloads, content_hash, row)`` for a copy target, or None when
        the row is gone.

        THE SINGLE SOURCE OF TRUTH for "what could this target put on the
        clipboard". The review recorders and ``_resolve_copy`` both call it,
        which is what makes reviewed bytes and copied bytes the same bytes
        by construction rather than by two code paths agreeing."""
        from src.data import zendesk_store as store
        if target == "article":
            row = store.get_article(conn, tid)
            if row is None:
                return None
            return (self._html_payloads(row.get("title") or "",
                                        self._article_source(row)),
                    str(row.get("content_hash") or ""), row)
        if target == "article_draft":
            row = store.get_article_draft(conn, tid)
            if row is None:
                return None
            html = row.get("body_html")
            if not html:
                # Renn drafts store markdown; render deterministically.
                from src.data.html_markdown import markdown_to_html
                html = markdown_to_html(row.get("body") or "")
            return (self._html_payloads(row.get("title") or "", str(html)),
                    store.draft_content_hash("article", row), row)
        if target == "macro":
            row = store.get_macro(conn, tid)
            if row is None:
                return None
            return (self._macro_payloads(row.get("name") or "",
                                         row.get("actions")),
                    str(row.get("content_hash") or ""), row)
        row = store.get_macro_draft(conn, tid)
        if row is None:
            return None
        return (self._macro_payloads(row.get("name") or "",
                                     row.get("actions")),
                store.draft_content_hash("macro", row), row)

    def _copy_confirm_text(self, target, tid, field, exact, html, row_title,
                           report=None):
        """``(title, heading, content, notes)`` for the copy confirm.

        ``content`` is ALWAYS the exact bytes and is NEVER None, however
        long they are: the host renders it in a visible scrollable view, and
        the heading states the full length so a payload that runs past the
        viewport still announces its size.

        Nothing here routes content by size any more. The old rule sent
        anything over 400 characters into ``QMessageBox.setDetailedText``,
        which Qt COLLAPSES behind a "Show Details…" button — so for every
        realistic article body (all of them are longer than that) the gate's
        headline claim, "these are the EXACT characters that will go on the
        clipboard", was followed on screen by nothing at all.

        The heading also NAMES the row (title + field), because "article
        draft 1" told an operator nothing about what they were approving.

        THE ROW TITLE IS PAGE-WRITABLE and is the only untrusted string in
        this heading: a page script can set it through ``js_save_draft``'s
        rename allowlist, or by driving Renn's propose tools over the
        co-registered chat bridge. Two consequences, both load-bearing:

        * the host MUST render this heading as PLAIN TEXT. It did not — the
          QLabel defaulted to ``Qt::AutoText`` and ``Qt::mightBeRichText``
          scans up to the first newline, which is exactly where the title
          sits, so a title starting ``<!--`` deleted the rest of the
          disclosure INCLUDING the markup-divergence warning below. Fixed in
          page.py; ``tests/test_zendesk_web_tab.py`` pins it.
        * whitespace in the title is collapsed here, so even as plain text a
          crafted title cannot forge the heading's line structure (a fake
          "Field: …" row, a fake warning, a wall of blank lines that scrolls
          the real text away).

        THE HEADING IS ALWAYS EXACTLY ``_CONFIRM_HEADING_LINES`` LINES, and
        every line but the first is Python's alone. That is now a property
        worth naming, because the markup report used to be appended here and
        the report is attacker-reachable: a body whose sanitizer report
        carried an embedded newline in a PROPERTY name grew this heading from
        five lines to eight and forged its structure. The report is returned
        as its own value instead (``notes``) and rendered in its own widget.

        When the text/html mime flavour differs from the plain one BOTH
        strings are shown — the clipboard carries both."""
        kind = target.replace("_", " ")
        exact = "" if exact is None else str(exact)
        title = " ".join(str(row_title or "").split()) or "(untitled)"
        if len(title) > 120:
            title = title[:119] + "…"
        sizes = f"{len(exact):,} characters"
        content = exact
        if html is not None and html != exact:
            sizes += f" (plus a {len(html):,}-character text/html flavour)"
            content = (f"{exact}\n\n"
                       f"----- text/html clipboard flavour "
                       f"({len(html):,} chars) -----\n{html}")
        draft = target in _DRAFT_COPY_TARGETS
        provenance = (
            "This is DRAFT content: it is not in Zendesk yet, and you are "
            "about to paste it into a public site by hand."
            if draft else
            "This is MIRRORED content: it is a local copy of a Zendesk row, "
            "and you are about to paste it somewhere by hand.")
        heading = (
            f"{kind} {tid} — “{title}”\n"
            f"Field: {field}    Size: {sizes}\n\n"
            f"{provenance}\n"
            "The box below is the EXACT text that will go on the clipboard. "
            "Read it — scroll if it does not fit — before you approve.")
        what = "draft" if draft else "mirrored"
        return (f"Copy {what} content to the clipboard?", heading, content,
                [str(r) for r in (report or [])])

    def _confirm_copy_release(self, conn, target, tid, field, payload,
                              content_hash, row_title) -> bool:
        """THE COPY GATE (module docstring, item 6). Every clipboard release
        of MARKUP, and every release from a DRAFT, passes through here — no
        authorship question is asked, because authorship is unknowable when
        the page can drive a Python-side actor (Renn) through the shared
        QWebChannel, and no preview question is asked, because a preview is
        a projection and projections lose.

        Runs the destructive-gate discipline verbatim: freeze check →
        per-target backoff → single-winner claim taken BEFORE the modal's
        nested event loop → native confirm DISPLAYING the exact bytes →
        re-verify that the row still produces those same bytes → release.
        Fails closed on every branch, including no ``confirm_fn`` injected.

        Every refusal — declined, no dialog host, bytes moved, cooled down —
        reports the SAME string and arms the SAME backoff, so a page cannot
        tell a human decline from a machine refusal and cannot grind for a
        mis-click (F3)."""
        if self._frozen():
            return False
        key = (target, tid)
        if self._copy_backoff_active(key):
            self.set_status(_COPY_REFUSED)
            return False
        exact = payload.get("text")
        html = payload.get("html")
        self._action_inflight = True
        try:
            approved = self._confirm(
                *self._copy_confirm_text(target, tid, field, exact, html,
                                         row_title, payload.get("report")))
        finally:
            self._action_inflight = False
        if not approved:
            return self._refuse_copy(key)
        # Re-verify after the modal: a nested event loop ran, so the row may
        # have moved. Byte-identical or no copy.
        try:
            bundle = self._copy_bundle(conn, target, tid)
        except Exception:  # noqa: BLE001
            bundle = None
        fresh = None if bundle is None else bundle[0].get(field)
        if (fresh is None or bundle[1] != content_hash
                or fresh.get("text") != exact or fresh.get("html") != html):
            return self._refuse_copy(key)
        self._copy_backoff.pop(key, None)   # a human said yes: reset
        return True

    def _copy_backoff_active(self, key) -> bool:
        rec = self._copy_backoff.get(key)
        return rec is not None and self._now() < rec["until"]

    def _refuse_copy(self, key) -> bool:
        """One refusal: one generic status line, one escalating cooldown.
        Always returns False so callers can ``return self._refuse_copy(k)``."""
        rec = self._copy_backoff.get(key) or {"count": 0, "until": 0.0}
        rec["count"] += 1
        rec["until"] = self._now() + (
            _COPY_CONFIRM_LOCKOUT_S if rec["count"] >= _COPY_CONFIRM_MAX_ATTEMPTS
            else _COPY_CONFIRM_COOLDOWN_S)
        self._copy_backoff[key] = rec
        self.set_status(_COPY_REFUSED)
        return False

    def _record_review(self, target, tid, payloads, content_hash):
        """Bind the exact strings the reviewer was JUST SHOWN to (target,
        tid). Only these hashes can later leave through the clipboard."""
        blobs = set()
        for p in (payloads or {}).values():
            blobs.add(_sha(p.get("text")))
            if p.get("html") is not None:
                blobs.add(_sha(p["html"]))
        self._reviewed[(target, tid)] = {"content": content_hash,
                                         "bytes": frozenset(blobs)}

    def _review_covers(self, target, tid, payload, content_hash) -> bool:
        """THE CLIPBOARD GATE. Fails closed on every branch.

        Three independent checks, all required:
        1. a review was recorded for this exact (target, id);
        2. the row's content hash, RECOMPUTED from the row at click time,
           still matches the review — so an out-of-band rewrite (Renn's MCP
           tools, another window, a pull) invalidates it even though this
           controller never saw the mutation;
        3. every exact string about to be released — the plain flavour AND
           the text/html mime flavour — hashes to something the review
           showed. This is what makes a projection blind spot unexploitable:
           if the reviewed material and the released bytes disagree by so
           much as one character, there is no copy.

        These refusals keep their specific wording, unlike everything at or
        after the confirm (which all report ``_COPY_REFUSED``). They are not
        an oracle: they describe state the page ALREADY knows — whether it
        asked for a diff, and whether it wrote to the row since — and the
        operator genuinely needs to be told to re-open the source view. What
        a page must not be able to learn is whether a HUMAN said no, and no
        branch here answers that."""
        label = f"{target.replace('_', ' ')} {tid}"
        rec = self._reviewed.get((target, tid))
        if rec is None:
            self.set_status(
                f"{label} has not been reviewed - open the source view "
                "before copying.")
            return False
        if rec.get("content") != content_hash:
            self._reviewed.pop((target, tid), None)
            self.set_status(
                f"{label} changed since you reviewed it - open the source "
                "view again before copying.")
            return False
        blobs = rec.get("bytes") or frozenset()
        candidates = [payload.get("text")]
        if payload.get("html") is not None:
            candidates.append(payload["html"])
        for value in candidates:
            if _sha(value) not in blobs:
                self._reviewed.pop((target, tid), None)
                self.set_status(
                    f"{label} would copy bytes your review never showed - "
                    "open the source view again before copying.")
                return False
        return True

    def _resolve_copy(self, conn, target, tid, field):
        """(text, html|None, sanitized, notice) for a validated copy
        request, or None for a silent refusal.

        DRAFT-content copies additionally require the CURRENT DB status in
        ('ready','copied') — the pending→ready review step is ENFORCED at
        the clipboard boundary, not advisory. Everything else, mirror rows
        included, goes through ``_review_covers``: no recorded review means
        no clipboard write, for every target. That gate stays as defence in
        depth even though it is no longer the disclosure.

        And every release ``_copy_needs_confirm`` names — all markup, on
        mirror rows and drafts alike, plus every draft field whatever it
        carries — then takes the one thing the page cannot fake: a NATIVE
        confirm displaying those exact bytes (module docstring, item 6)."""
        bundle = self._copy_bundle(conn, target, tid)
        if bundle is None:
            return None
        payloads, content_hash, row = bundle
        payload = payloads.get(field)
        if payload is None:
            return None                 # e.g. a macro with no reply action
        if target in _DRAFT_COPY_TARGETS:
            if row.get("status") not in ("ready", "copied"):
                return None
        if not self._review_covers(target, tid, payload, content_hash):
            return None
        if _copy_needs_confirm(target, field):
            row_title = (payloads.get("title") or payloads.get("macro_name")
                         or {}).get("text")
            if not self._confirm_copy_release(
                    conn, target, tid, field, payload, content_hash,
                    row_title):
                return None
        return (payload["text"], payload["html"], payload["sanitized"],
                payload["notice"])

    # ── pull / import claim machinery ────────────────────────────────
    def _pick_files(self):
        if self._file_pick_fn is None:
            return None
        try:
            return list(self._file_pick_fn() or [])
        except Exception:  # noqa: BLE001
            return []

    def _pick_folder(self):
        if self._folder_pick_fn is None:
            return None
        try:
            folder = self._folder_pick_fn()
        except Exception:  # noqa: BLE001
            return []
        return [str(folder)] if folder else []

    def _start_import(self, picker):
        """Claim → native picker (nested event loop!) → runner. The claim is
        held through runner completion; released on cancel or failure."""
        if self._frozen():
            return
        self._pull_inflight = True
        paths = picker()
        if paths is None:
            # No picker injected at all — report rather than wedge.
            self._pull_inflight = False
            self._emit(self.import_resolved,
                       {"ok": False, "error": "not_started"})
            return
        if not paths:
            # Operator cancelled the dialog — silent release.
            self._pull_inflight = False
            return
        started = False
        if self._import_runner is not None:
            try:
                started = bool(self._import_runner(list(paths)))
            except Exception:  # noqa: BLE001
                started = False
        if not started:
            self._pull_inflight = False
            self._emit(self.import_resolved,
                       {"ok": False, "error": "not_started"})

    def _purge_text(self, conn, scope) -> str:
        """Scope-specific confirm text with live counts (§4 wording)."""
        if scope == "imported":
            n = conn.execute(
                "SELECT (SELECT COUNT(*) FROM zendesk_articles "
                "WHERE origin='import') + (SELECT COUNT(*) FROM "
                "zendesk_macros WHERE origin='import')").fetchone()[0]
            return (f"Remove {n} imported rows from the local mirror? "
                    "Revisions are kept.")
        n_articles = conn.execute(
            "SELECT COUNT(*) FROM zendesk_articles").fetchone()[0]
        n_macros = conn.execute(
            "SELECT COUNT(*) FROM zendesk_macros").fetchone()[0]
        if scope == "articles":
            return (f"Remove {n_articles} mirrored articles from the local "
                    "mirror? Revisions are kept.")
        if scope == "macros":
            return (f"Remove {n_macros} mirrored macros from the local "
                    "mirror? Revisions are kept.")
        n_open = conn.execute(
            "SELECT (SELECT COUNT(*) FROM zendesk_article_drafts "
            "WHERE status IN ('pending','ready')) + "
            "(SELECT COUNT(*) FROM zendesk_macro_drafts "
            "WHERE status IN ('pending','ready'))").fetchone()[0]
        return (f"Remove {n_articles} articles and {n_macros} macros AND "
                f"delete {n_open} pending/ready revisions (including Renn's "
                "open work)? Copied/pushed revisions are kept as the audit "
                "record. This cannot be undone.")
