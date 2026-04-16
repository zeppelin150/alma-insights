# Alma Insights — Release Builder

## Quick Start

Build a distributable zip for the current platform:

```bash
python installer/build_release.py
```

Or specify a platform explicitly:

```bash
python installer/build_release.py --platform windows
python installer/build_release.py --platform macos
```

Output appears in `dist/`.

## Requirements (build machine only)

- Python 3.10+
- Internet access (downloads Python, Node.js, npm packages, ML model)
- ~3 GB free disk space for staging

**End users do NOT need Python or Node.js installed.** The distribution bundles everything.

## What the build produces

### Windows: `dist/AlmaInsights-win64.zip`

```
AlmaInsights/
  Install Alma Insights.bat     <- User runs this
  AlmaInsights.bat              <- Direct launcher (post-install)
  _installer/install.py         <- Console installer script
  python/                       <- Embeddable Python 3.12 + all dependencies
  node/                         <- Standalone Node.js 22.x + Gemini CLI
  app/                          <- Application source
    main.py
    src/
    config/
    assets/
    migrations/
    data/
      models/                   <- Pre-downloaded ML model (offline use)
```

### macOS: `dist/AlmaInsights-macOS-arm64.zip`

```
AlmaInsights/
  Install Alma Insights.command <- User runs this
  AlmaInsights.command          <- Direct launcher (post-install)
  _installer/install.py         <- Console installer script
  python/                       <- Standalone Python 3.12 + all dependencies
  node/                         <- Standalone Node.js 22.x + Gemini CLI
  app/                          <- Application source (same as Windows)
```

## End-user installation flow

1. Download the zip for their platform
2. Extract the zip
3. Double-click `Install Alma Insights` (.bat on Windows, .command on macOS)
4. The console installer asks for an install location (defaults to user-space)
5. Files are copied (Python, Node.js, app) with progress bars
6. Database is initialized and schema migrations applied
7. Settings are migrated if upgrading from an older install
8. Gemini CLI is verified/installed via bundled npm
9. Desktop and Start Menu shortcuts are created
10. User can launch immediately

### Install locations (defaults, no admin needed)

- **Windows**: `%LOCALAPPDATA%\AlmaInsights`
- **macOS**: `~/Applications/AlmaInsights`

### What the installer creates

- **Windows**: Desktop shortcut (.lnk) + Start Menu entry
- **macOS**: `.app` bundle in `~/Applications/` + Desktop alias

## Updating

Run the installer again over an existing installation. It preserves:
- User database (`data/alma_insights.db`)
- User settings (`data/settings.yaml`)
- Export history (`data/exports/`)

Everything else (code, config, migrations, runtimes) is replaced.

## Estimated sizes

| Platform | Zip size | Installed size |
|----------|----------|---------------|
| Windows  | ~250-400 MB | ~500-700 MB |
| macOS    | ~280-420 MB | ~550-750 MB |

Size breakdown: PySide6 (~150MB), scikit-learn/scipy/numpy (~80MB),
sentence-transformers model (~80MB), Node.js + Gemini CLI (~50MB), app source (~5MB).

## Bundled runtimes

| Runtime | Version | Purpose |
|---------|---------|---------|
| Python 3.12 | Embeddable (Win) / python-build-standalone (macOS) | App runtime |
| Node.js 22.x | Standalone binary | Gemini bridge process |
| Gemini CLI | `@google/gemini-cli` via npm | Google Gemini API access |

## Files in this directory

| File | Purpose |
|------|---------|
| `build_release.py` | Build script — run on dev/CI machine |
| `install.py` | Console installer — bundled in the zip, run by end users |
| `install_win.bat` | Windows entry point — launches install.py with bundled Python |
| `install_mac.command` | macOS entry point — launches install.py with bundled Python |
| `launcher_win.bat` | Windows direct launcher template |
| `launcher_mac.command` | macOS direct launcher template |
| `README_IT_SECURITY.md` | IT security review documentation |
