# Mac Implementation Guide — Style Guide Preview/Edit + Card/Article Template

**Audience:** the Claude session working on the macOS checkout. This feature was
built and tested on the Windows dev machine (branch `enablement-content-tabs`,
2026-07-14). Everything travels as ordinary git-tracked files — there is no
Windows-specific code in the change. Your job on the Mac is to **verify**, not
re-implement, and to check the two macOS-specific behaviors called out below.

## What the feature is

The enablement Settings → **Style Guide** tab now has two sections, both with
the same controls:

1. **STYLE GUIDE** (existing, upgraded) — tone/formatting rules.
2. **CARD / ARTICLE TEMPLATE** (new) — the *Unified Support Center/Guru
   Article Template*: a heading skeleton that card generation must reproduce
   exactly (same headings, same order).

Both sections support: **Paste… / Upload… / Upload folder… / From Drive… /
Clear**, a **rendered markdown preview** of the active document ("polished
block of text"), an inline **Edit → Save** editor (saves in place, keeps the
stored document's name), and the stored-document library (Make active /
Delete).

Prompt injection: the template block rides into card generation
(`draft_card_from_document`) and revision (`_revise_draft_impl`) alongside the
style-guide block. The two are independent documents with independent
settings pointers (`enablement.style_guide_doc_id`,
`enablement.card_template_doc_id`).

## Files changed / added

| File | Change |
|------|--------|
| `src/data/enablement_store.py` | Tagged-guide helpers generalized (`_get_guide`/`_set_guide`/`_list_guides`/`_set_active_guide`/`_delete_guide`/`_clear_guide`); style-guide API now delegates to them (behavior unchanged, all old tests pass). NEW: `get/set/list/set_active/delete/clear_card_template`, `card_template_block`, `update_style_guide_text`, `update_card_template_text` (in-place edit, name preserved). `_set_guide` strips `<span …>` color markup that `doc_reader` preserves from Word headings. Template block injected in `draft_card_from_document` via the `{style_guide}` slot. |
| `src/data/chat_tools/enablement_tools.py` | `_revise_draft_impl` injects `card_template_block` next to `style_guide_block`. |
| `src/ui/pages/enablement/settings.py` | NEW `_GuideSection(QFrame)` widget (actions row, rendered `QTextBrowser` preview ⇄ `QPlainTextEdit` editor, library rows). `SettingsPage` builds two instances; old `_sg_row`/`_style_guide` code removed. NEW signals: `style_guide_saved`, `card_template_action/activate/delete/saved`. NEW setters: `set_style_guide_content`, `set_card_templates`, `set_card_template_status`, `set_card_template_content`. Old public API (`set_style_guides`, `set_style_guide_status`) preserved. |
| `src/ui/pages/enablement/page.py` | Generalized `_guide_action` handler drives both sections. Upload is now **multi-select**, starts at `~`, and carries an `All files (*)` fallback filter. NEW `upload_folder` action (`getExistingDirectory` → recursive scan, cap 50, skips hidden/`~$` lock files). Editor-save handlers call the `update_*_text` store functions. Drive import worker + `_on_import_finished` route the new `template` kind. Demo "From Drive" on the template section seeds the bundled asset. |
| `assets/templates/support_center_article_template.md` | NEW — the Unified Support Center/Guru Article Template converted from the source docx **by the project's own `doc_reader`** (so it matches exactly what an upload of the docx produces), span markup stripped. |
| `tests/test_card_template.py` | NEW — 17 tests: storage round trip, span stripping, in-place edit keeps name, library activate/delete, prompt injection (gen + revise + both blocks), `_GuideSection` preview/edit/cancel, demo seed through a real `EnablementPage`. |

## How the pieces connect

```
SettingsPage ─ _GuideSection("STYLE GUIDE")          signals → EnablementPage._guide_action(...)
            └ _GuideSection("CARD / ARTICLE TEMPLATE")            │  paste/upload/upload_folder/drive/clear
                                                                  ▼
                                            enablement_store tagged docs ([STYLE-GUIDE] / [CARD-TEMPLATE])
                                                                  │ active pointers in settings section
                                                                  ▼
                       style_guide_block(conn) + card_template_block(conn)
                                                                  │
                     draft_card_from_document (gen)  ·  _revise_draft_impl (revise)
```

Refresh path: every action ends in `_refresh_style_guide_status()` /
`_refresh_card_template_status()`, which pushes status text, the **active
document's full text** (→ rendered preview), and the library list into the
settings widgets. If a preview ever looks stale, that refresh is the place to
look.

## Verify on macOS (the actual point of this guide)

Run the test groups first (groups of 3–4, never full `tests/`):

```bash
python -m pytest tests/test_card_template.py tests/test_style_guide.py -q
python -m pytest tests/test_enablement_ui_live.py tests/test_chat_tools.py -q
python -m pytest tests/test_content_update_fanout.py tests/test_enablement_live_cutover.py -q
```

All were green on Windows (26 + 55 + 10). Then launch the app and check the
two macOS-specific items that motivated this change:

1. **File upload findability (the reported Mac bug).** Settings → Style Guide
   → Upload…. The dialog must open at the home directory (previously it opened
   at an opaque default location). In the native NSOpenPanel, switch the
   filter popup to "All files" and confirm files that don't match the document
   filter are selectable rather than greyed out. Multi-select several files —
   each stores; the last becomes active.
   - Caveat to check: on macOS the filter menu in `getOpenFileNames` is the
     small popup at the bottom of the panel. If testers still can't find
     files, the likely cause is the document living in iCloud Drive/Google
     Drive streaming placeholders — the file must be downloaded locally
     ("Available offline") for `doc_reader` to read it. `.gdoc` stubs are NOT
     readable locally by design — use **From Drive…** for Google Docs.
2. **Upload folder… (new).** Pick a folder containing a few `.md`/`.docx`
   files. Expect a status line like `3 documents uploaded — "x" is the active
   style guide`, and all three in the library list. `getExistingDirectory`
   uses the native macOS folder panel; confirm it opens and folders are
   selectable.

Then the demo-critical flow:

3. **Template upload with exact headings.** Upload the real template docx
   (`Untitled document.docx` / the Guru card export) under CARD / ARTICLE
   TEMPLATE. The preview must render `Purpose:` / `Article Title` /
   `Beginning of Article` / `Body` / `FAQs (SEO + AI Readiness)` /
   `Still Need Help?` as headings, with **no literal `<span` text** anywhere.
4. **Preview + inline edit.** Both sections: after upload the rendered block
   appears; Edit shows markdown source; Save persists (relaunch the app —
   content must survive) and does NOT rename the stored document; Cancel
   discards.
5. **Demo mode.** With demo mode ON, template section → From Drive… must load
   the bundled asset instantly (no network). If
   `assets/templates/support_center_article_template.md` were missing the
   code falls back to a minimal inline skeleton — if you see the short
   fallback instead of the full template, the asset didn't travel.
6. **Generation actually follows the template.** Workbench → import any doc →
   generate a card with an LLM configured: the draft should carry the
   template's heading structure (Body / FAQs / Still Need Help?).

## Rules of engagement (repeat of repo conventions)

- DB access only via `connection_factory.get_connection()`; settings only via
  `settings_manager`. Both already respected in this change.
- Do not strip the docstrings in the touched files.
- If something fails on the Mac, fix forward in these files rather than
  diverging: the Windows side considers the store + widget API frozen for the
  demo (`set_*_content` / `*_saved` signal names are load-bearing in
  `page.py`).
- Zombie cleanup before any E2E run: `pkill -f alma_mcp_server` (mac
  equivalent of the Windows `wmic` line).
