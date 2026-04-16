# Hardening — Rollback, EULA, Crash Export, Secrets Audit

**Modules:**
- [src/updater/rollback.py](../src/updater/rollback.py) — post-update rollback guard
- [src/ui/dialogs/eula_dialog.py](../src/ui/dialogs/eula_dialog.py) — first-run consent
- [scripts/audit_secrets.py](../scripts/audit_secrets.py) — credential-leak scanner
- [src/core/crash_handler.py](../src/core/crash_handler.py) — global crash handler (Phase 1, now consumed by Settings UI + rollback)
- Settings → Support card in [settings_page.py](../src/ui/pages/settings_page.py) — Export crash reports + Rollback buttons

**Tests:** [test_rollback.py](../tests/test_rollback.py), [test_eula_dialog.py](../tests/test_eula_dialog.py), [test_audit_secrets.py](../tests/test_audit_secrets.py), [test_offline_mode.py](../tests/test_offline_mode.py), [test_phase5_e2e.py](../tests/test_phase5_e2e.py).

## Auto-rollback guard

When [updater.py::apply_staged_update](../src/updater/updater.py) swaps a new version into place, it now:

1. Leaves the previous-version dirs at `_src_backup/`, `_config_backup/`, `_migrations_backup/`.
2. Calls [`rollback.record_apply(previous, new)`](../src/updater/rollback.py), which
   - Renames `_*_backup` → `_*_previous`
   - Writes `data/rollback_state.json` with the apply timestamp + a 60 s grace window.

On every subsequent launch, `main.py` calls [`rollback.needs_rollback()`](../src/updater/rollback.py) *before* importing any `src.*` module:

```text
                  ┌── grace window expired ──→ stable, cleanup scheduled
rollback_state    │
     +        ───→├── crashes ≥ 3 in window ──→ perform_rollback()
crash_reports/    │
                  └── crashes < 3, still in window ──→ continue
```

If rollback fires:
- `_src_previous/` moves back to `src/` (same for `config/` and `migrations/`).
- `src/__init__.py` VERSION string is rewritten to the previous version.
- `data/rollback_state.json` is deleted.
- `importlib.invalidate_caches()` runs so the now-current `src/` code is imported fresh.

After `MainWindow.show()`, a 65 s `QTimer.singleShot` calls [`clear_state_if_stable()`](../src/updater/rollback.py) which, if the grace window has expired, deletes `_*_previous/` and the rollback state — the update is considered stable.

**Manual override** lives in Settings → Support → *Rollback to Previous Version*. The button is enabled only when `_src_previous/` exists (i.e. inside the grace window or before stable-cleanup fires).

## EULA

[EulaDialog](../src/ui/dialogs/eula_dialog.py) is a modal `QDialog` shown once per EULA version. The flow:

1. `main.py` instantiates `QApplication`, crash handler, and applies staged updates.
2. `ensure_accepted()` reads `data/settings.yaml → eula.version_accepted`.
3. If it matches `EULA_VERSION`, return True and continue.
4. Otherwise show the dialog; the **Accept** button is disabled until the consent checkbox is ticked.
5. On accept, `eula.record_acceptance(settings)` adds:
   ```yaml
   eula:
     version_accepted: "1"
     accepted_at: "2026-04-15T20:00:00Z"
     accepted_by: "chris"
   ```
   and `settings_manager.save_settings(...)` persists.
6. On decline, `main.py` exits with status 1.

Body covers: local-first data handling, PII redaction, Gemini BAA, Anthropic scope, Lightdash, update check scope, crash report scope (local-only), keyring credential storage, no-warranty clause. Bump `EULA_VERSION` to force re-prompt on policy changes.

## Crash handler — user-facing export

Phase 1 built [crash_handler.py](../src/core/crash_handler.py) with `list_reports()`, `export_bundle(dest)`, and `clear(keep=N)`. Phase 5 wires them to **Settings → Support → Export Crash Reports**. Clicking:

1. Lists up to 20 newest reports from `data/crash_reports/`.
2. Opens a `QFileDialog.getSaveFileName` (default filename `alma-crash-reports-YYYYMMDD-HHMMSS.zip`).
3. `crash_handler.export_bundle(dest, limit=20)` writes a zip containing every report JSON.
4. User emails the zip manually. No auto-upload.

Reports retain full tracebacks, file paths, and line numbers. Only credential-shaped `key=value` patterns are redacted.

## Secrets audit

[`scripts/audit_secrets.py`](../scripts/audit_secrets.py) scans the working tree and optionally the git history for credential-shaped strings:

- `ldpat_[A-Za-z0-9]{16,}` — Lightdash PAT
- `sk-ant-[A-Za-z0-9_-]{20,}` — Anthropic API key
- `gh[pousr]_[A-Za-z0-9]{36,}` — GitHub fine-grained PAT
- `AIza[0-9A-Za-z_\-]{35}` — Google API key
- `Bearer\s+[A-Za-z0-9_\-.]{40,}` — generic bearer tokens

Skipped paths: `.git`, `.claude`, `.venv`, `node_modules`, `__pycache__`, `data/models`, `data/alma_insights.db`, `dist`, `tests/` (test fixtures), `docs/` (documentation examples), `data/crash_reports`, `scan_server/node_modules`.

Exit codes:
- `0` — clean tree (and, unless `--allow-history`, clean git history)
- `1` — at least one hit
- `2` — invocation / environment error

CI can run it as a blocking step; local devs can run it before a push. Phase 4's `ci.yml` now delegates its secret scan to this script.

### ⚠ Finding during Phase 5 development

The first run caught **5 real Google API keys** in `.claude/settings.local.json` — Claude Code's own local command-log. Those keys live in `~/.claude/` and are gitignored (verified by `git log --all -p | grep AIza` → 0 hits), so they were never committed, but they are **on disk in plaintext**. Recommendation: rotate those keys anyway and avoid embedding raw `GEMINI_API_KEY=` values in command lines that get captured by Claude Code.

## Offline degradation

[`tests/test_offline_mode.py`](../tests/test_offline_mode.py) blocks `urllib.request.urlopen` and `socket.create_connection`, then exercises:

- Startup update check (`pat` mode) → warns with a network-unavailable message
- Startup update check (`disabled` mode) → still passes (no network attempt)
- `UpdateChecker._do_check()` → emits `check_failed` signal
- `github_app_auth.mint_installation_token` → returns `None`
- `env_guard.enforce()` + `scan_for_issues()` → run without raising
- `integrity.check_integrity()` → no network path, passes
- `Checker.run_all()` with the full `DEFAULT_CHECKS` list → completes, 10 results

Nothing crashes, every user-facing path has an actionable message.

## Operational runbook

| Situation | What to do |
|-----------|------------|
| A shipped update is crashing on startup | Nothing — auto-rollback fires on the third crash inside 60 s. User sees `[startup] Auto-rollback (…): rolled back to v9.2.0` on stderr. |
| A user reports crashes | Ask them to **Settings → Support → Export Crash Reports** and email the zip. |
| Need to force a downgrade without a crash loop | **Settings → Support → Rollback to Previous Version** (only works while `_src_previous/` still exists — during the 60 s grace window). |
| Force EULA re-prompt across the fleet | Bump `EULA_VERSION` in [eula_dialog.py](../src/ui/dialogs/eula_dialog.py) and ship a release. |
| Pre-PR credential check | `python scripts/audit_secrets.py` — runs in under a second. |
