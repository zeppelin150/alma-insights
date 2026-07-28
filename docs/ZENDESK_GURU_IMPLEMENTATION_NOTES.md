# Zendesk Mirror + Guru Publish — Implementation Notes (2026-07-27)

Branch `enablement-content-tabs`. Read this before touching the Zendesk
workspace or the Guru publish path.

## What shipped

**Zendesk is one-way.** The API is structurally read-only: `ZendeskClient`
has no write methods (`create_article` / `update_article` / `create_macro` /
`update_macro` / `_write` are deleted, not stubbed), and one `_build_request`
choke point raises `ZendeskWriteBlocked` on any non-GET. Approved content
reaches Zendesk only when a person copies it out of the app and pastes it
into Zendesk's own editor. Rationale: a Guide instance is a **public domain**
and its content is **not restorable** the way Guru and Asana content is
(those sit behind domain + Zscaler restrictions). Guru/Asana writes are
deliberately unaffected. Enforced permanently by
`tests/test_zendesk_readonly_guard.py`, which scans all of `src/` — **if it
fails, remove the write, do not relax the guard.**

**Local mirror** (migration 051): Help Center articles, sections, categories
and macros with `content_hash` dedup, `origin` = `pull` | `import`
provenance, and FTS5 search. Populated by manual file import
(`src/data/zendesk_import.py` — API-shaped JSON, saved HTML, doc_reader
documents, hostile-input hardened) or a GET-only paged pull.

**Web workspace** on `#/zendesk` (`enablement.web_tabs` = `zendesk` | `all`):
a Garden-v8 replica of Zendesk's Guide article editor and Admin Center macro
editor. `src/services/zendesk_web.py` holds all authority; the bridge is a
pure relay; the native `ZendeskPage` remains the flag-off default and the
construction-failure fallback.

**Revisions.** Renn proposes into the mirror (rationale + sources mandatory);
the specialist reviews a diff, copies the exact bytes, pastes into Zendesk by
hand, and marks copied (`pending → ready → copied`; `copied`/`pushed`
immutable). Specialists can also edit article bodies — those save as
revisions too, never as direct mirror writes.

**Guru publish** now has one definition of the published body:
`enablement_store.publish_body(draft)` = `expand_blocks(content_html or
markdown_to_html(content))`. `publish_draft` sends exactly that, and every
preview, diff and safety belt derives from the same call.

## The invariant that drove nine rounds of review

> Whatever leaves the machine must be byte-identical to something a human was
> shown, at the moment it left.

Nine adversarial rounds found ~20 real defects, nearly all one shape: **a
projection or heuristic decided what the human saw while different bytes
shipped.** Every fix that added cleverness was defeated; the ones that held
replaced cleverness with a provable predicate plus unconditional access to
the real bytes. Concretely, do not reintroduce any of these:

- A text projection as the review surface (attribute-blind — hrefs and image
  sources vanished from diffs while shipping verbatim).
- A closed enumeration of "bad" constructs (the CSS hiding list mirrored its
  own test corpus; `transform:scale(0)` walked past it).
- A heuristic suppressing a warning based on author-controlled text.
- A disclosure that can be collapsed (`QMessageBox.setDetailedText` hides
  content behind an unclicked button — banned by test).
- Reasoning about **authorship** — a page script can drive Renn through the
  co-registered chat bridge, so "a Python actor wrote it" proves nothing.
- Content-derived text in a `QLabel` left at `Qt::AutoText` (renders as
  markup and can forge a dialog). Use `_common.force_plain_text`.

**Clipboard rule (Zendesk):** every markup-bearing copy passes a native Qt
dialog showing the exact bytes uncollapsed. The preview is therefore a
*display* concern, not a security control — render fidelity is free.

## KNOWN OPEN — read before enabling Guru publish from chat

**CRITICAL, unfixed at time of writing.** `approved_at` is a permanent gate
release (`enablement_tools.py` ~1298) that is **never rolled back when the
publish it authorized fails**. A transient Guru 401/502 leaves the gate open
forever, and `clear_push_request` also drops the draft out of the review
panel, so there is no surface left to notice. A prompt-injected Renn can then
revise the draft and call `push_guru_draft` unattended. Also: the approval
fingerprint does not cover the publish **target** (collection/folder/card),
and `ChatBridge._on_busy` re-mints the binding after every Renn turn, which
makes the revise-between-review-and-approve check inert in production.

A fix was in progress and is **partially applied but unverified** — see
`~/.claude/plans/` and the `fix-guru-approval-gate` workflow. Until it is
verified: **do not use "Approve & publish" for Guru cards from the Renn chat
panel.** Everything else, including the entire Zendesk workspace, is
unaffected.

## Deliberately deferred (owner decisions)

- **No `sanitize_html` on the Guru write path.** Guru's native callouts,
  collapsibles and card-links depend on `class=` and `data-ghq-*` attributes
  that the strict sanitizer strips; sanitizing would break a shipped feature.
  Needs a Guru-aware profile, not the strict one.
- **Renn keeps its Guru/Asana write capability**, and `approveDraft` keeps
  its current gating.
- **17 audit majors left as-is** — full detail in
  `~/.claude/plans/approve-send-audit.md`. Two worth knowing: the Qt
  workbench "Push to Guru" ships the last *saved* body rather than what is on
  screen, and both "Review changes" diffs compare against an empty baseline
  (the `guru_cards` table does not exist and the error is swallowed), so a
  replacement renders as all-additions.

## Operational limits

- **Pull ceiling: 5,000 items per family** (`max_pages=50` × `per_page=100`).
  At 4,000 macros that is 25% headroom — raise it before production. The
  pull reports `truncated: True` rather than lying, but verify that surfaces
  in the UI.
- **Timing at production scale** (measured): 200 articles × 2,000 words plus
  4,000 macros = ~44 sequential GETs, **15–40s** end to end. The database is
  not the bottleneck — 0.46s to upsert the articles, 0.25s for the macros,
  2ms FTS search, 43MB on disk. Re-pulls of unchanged content are near-free
  (content-hash dedup).
- **Never `INSERT OR REPLACE` into mirror tables** (grep-guarded by
  `tests/test_zendesk_mirror_schema.py`).

## Not yet done

- **Never run on Apple Silicon.** All verification was on Windows. The
  Zendesk workspace is a new QtWebEngine surface and macOS has a history of
  blank renders here — launch it on the Mac before trusting it.
- **No human end-to-end UAT.** Verified by test, headless probe and
  screenshot; nobody has pulled → reviewed → copied → pasted for real.
- **Versions/revisions editor is phase 1 only** (migration 053, store module
  and the Versions/History React panels exist and pass; the controller and UI
  wiring are not built).
- Windows console flashes: several `subprocess` sites in `gemini_client.py`
  and `gemini_setup.py` lack `CREATE_NO_WINDOW`. Cosmetic, unrelated to this
  work, one argument each to fix.

## Testing

Run in groups of 3–4 files; full `tests/` hangs on Windows.
`npm --prefix web run test` for JS. WebEngine round-trips live in gitignored
`tests/test_*_local.py` and **must run singly**.
