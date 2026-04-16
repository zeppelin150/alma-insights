# Alma Insights — Build, Install & Update

## Build Process

### Release Builder (`installer/build_release.py`)

Assembles standalone distribution ZIPs for Windows and macOS.

```bash
python installer/build_release.py --platform windows   # → dist/AlmaInsights-win64.zip
python installer/build_release.py --platform macos      # → dist/AlmaInsights-macOS.zip
python installer/build_release.py --platform all         # → both
```

**Build requirements (build machine only):**
- Python 3.10+
- Internet access (downloads Python embeddable + pip packages)
- ~2 GB free disk space for staging

### What Gets Bundled

| Component | Source | Notes |
|-----------|--------|-------|
| Application code | `main.py`, `src/`, `config/`, `assets/`, `migrations/` | Core app |
| Python runtime | Downloaded embeddable (3.12.8) | Windows: embed zip, macOS: python-build-standalone |
| Python packages | `requirements.txt` (10 packages) | Installed into bundled Python |
| Node.js | Bundled binary | For Gemini bridge (`scan_server/`) |
| Gemini CLI | Bundled via npm | `@anthropic-ai/gemini-cli` |
| Embedding model | Pre-downloaded | `all-MiniLM-L6-v2` (sentence-transformers) |

### Version Management

Single source of truth: `src/__init__.py` → `VERSION = "x.y.z"`

Read by:
- `installer/build_release.py` (for ZIP naming)
- `installer/install.py` (display during install)
- `src/updater/update_checker.py` (compare against GitHub releases)
- `src/updater/updater.py` (log version in staging metadata)

---

## Installation

### Express Installer (`installer/install.py`)

Cross-platform console installer bundled inside the distribution ZIP.

**Launchers:**
- Windows: `Install Alma Insights.bat` → calls `install.py`
- macOS: `Install Alma Insights.command` → calls `install.py`

**Install process:**
1. Prompt for install location (default: `%LOCALAPPDATA%\AlmaInsights` or `~/Applications/AlmaInsights`)
2. Copy application files to install directory
3. Set up bundled Python + Node.js
4. Install Python packages from bundled wheels
5. Download NLTK data if needed
6. Pre-download embedding model
7. Create desktop shortcut / launcher
8. Run Gemini CLI auth if needed

**Launchers (post-install):**
- Windows: `launcher_win.bat` — starts the app
- macOS: `launcher_mac.command` — starts the app

### First-Run Setup (`setup_alma_insights.py`)

Alternative for development environments (not bundled installs):

```bash
python setup_alma_insights.py
```

Checks: Python version, pip, installs requirements, creates desktop shortcut.

---

## Auto-Update System

### Architecture

```
                    Settings → Updates tab
                         │
                         ▼
                  UpdateChecker.check()          ← background QThread
                         │
                         ▼
              GitHub Releases API (latest)
                         │
              ┌──────────┴──────────┐
              ▼                     ▼
         up_to_date()        update_available(current, latest, url)
                                    │
                              User clicks "Install Now"
                                    │
                                    ▼
                            Updater.stage(url, sha256)   ← background thread
                                    │
                            ┌───────┼───────┐
                            ▼       │       ▼
                     progress(%)    │    failed(msg)
                                    ▼
                             complete() → "Restart Now" button
                                    │
                              User clicks "Restart Now"
                                    │
                                    ▼
                        App exits → relaunches
                                    │
                                    ▼
                    apply_staged_update()     ← called early in main.py
                         │
                    backup → swap → cleanup
                         │
                    App starts with new code
```

### UpdateChecker (`src/updater/update_checker.py`)

- Checks GitHub releases API: `https://api.github.com/repos/alma-health/alma-insights/releases/latest`
- Compares semver tags (handles `v1.2.3`, `1.2.3`, `1.2.3-rc1`)
- Supports private repos via GitHub PAT (from `pat_store`)
- Timeout: 15 seconds

**Signals:**
- `update_available(current_version, new_version, release_url)`
- `up_to_date()`
- `check_failed(error_message)`

### Updater (`src/updater/updater.py`)

Stage-and-apply pattern (Windows-safe — running files can't be replaced):

1. **Stage**: Download release ZIP → verify SHA-256 → extract to `_update_staging/`
2. **Apply** (next launch, before `import src`): Backup current `src/`, `config/`, `migrations/` → swap with staged → cleanup backups

**Protected directories** (never deleted during update):
`data`, `_update_staging`, `.git`, `.venv`, `venv`, `node_modules`, `scan_server`, `assets`, `debug`, `docs`, `installer`, `tests`

**Signals:**
- `progress(percent, step_description)` — 0-100
- `complete()` — staged, restart needed
- `failed(error_message)`

### Schema Migrator (`src/updater/schema_migrator.py`)

Applied automatically by `db_manager.initialize()` after table creation.

- Scans `migrations/` directory for `NNN_description.sql` files
- Tracks applied migrations in `schema_migrations` table
- Each migration runs in its own transaction
- Handles `ALTER TABLE ADD COLUMN` with duplicate-column tolerance (wraps in try/except)
- Migration 007 is reserved (skipped)

---

## Gemini Bridge Setup

The scan server requires Node.js and npm packages:

```bash
cd scan_server
npm install
```

**Dependencies** (from `package.json`):
- `express`, `helmet`, `express-rate-limit` — HTTP server
- `uuid` — request IDs
- `node-forge` — self-signed TLS cert generation

**Auth:**
- Gemini CLI uses OAuth: `gemini auth login`
- Credentials cached at `~/.gemini/google_accounts.json`

---

## Release Checklist

1. Update `src/__init__.py` → `VERSION = "x.y.z"`
2. Run tests: `python -m pytest tests/test_pipeline_full.py tests/test_settings_manager.py tests/test_model_registry.py -v`
3. Build: `python installer/build_release.py --platform all`
4. Create GitHub release with tag `vx.y.z`
5. Upload `dist/AlmaInsights-win64.zip` and `dist/AlmaInsights-macOS.zip`
6. Include SHA-256 checksums in release notes
7. Users receive update notification on next check

---

## See Also

- `CLAUDE.md` — Quick reference
- `docs/CONTRIBUTING.md` — Developer setup
- `docs/TROUBLESHOOTING.md` — Common issues
- `docs/DATABASE.md` — Schema migrations reference
- `src/updater/INDEX.md` — Updater module API
