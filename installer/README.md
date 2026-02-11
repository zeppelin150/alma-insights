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
- Internet access (downloads ~200-400 MB of Python + packages)
- ~2 GB free disk space for staging

**End users do NOT need Python installed.** The distribution bundles everything.

## What the build produces

### Windows: `dist/AlmaInsights-win64.zip`

```
AlmaInsights/
  Install Alma Insights.bat     <- User runs this
  AlmaInsights.bat              <- Direct launcher (post-install)
  _installer/install.py         <- Console installer script
  python/                       <- Embeddable Python 3.12 + dependencies
  app/                          <- Application source
```

### macOS: `dist/AlmaInsights-macOS-arm64.zip`

```
AlmaInsights/
  Install Alma Insights.command <- User runs this
  AlmaInsights.command          <- Direct launcher (post-install)
  _installer/install.py         <- Console installer script
  python/                       <- Standalone Python 3.12 + dependencies
  app/                          <- Application source
```

## End-user installation flow

1. Download the zip for their platform
2. Extract the zip
3. Double-click `Install Alma Insights` (.bat on Windows, .command on macOS)
4. The console installer asks for an install location (defaults to user-space)
5. Files are copied, shortcuts are created, and the app is ready

### Install locations (defaults, no admin needed)

- **Windows**: `%LOCALAPPDATA%\AlmaInsights`
- **macOS**: `~/Applications/AlmaInsights`

### What the installer creates

- **Windows**: Desktop shortcut (.lnk) + Start Menu entry
- **macOS**: `.app` bundle in `~/Applications/` + Desktop alias

## Updating

Run the installer again over an existing installation. It preserves the user's database (`data/` directory) and replaces everything else.

## Estimated sizes

| Platform | Zip size | Installed size |
|----------|----------|---------------|
| Windows  | ~150-250 MB | ~350-500 MB |
| macOS    | ~180-280 MB | ~400-550 MB |

Most of the size comes from PySide6 (Qt framework) and scikit-learn (numpy/scipy).

## Files in this directory

| File | Purpose |
|------|---------|
| `build_release.py` | Build script — run on dev/CI machine |
| `install.py` | Console installer — bundled in the zip, run by end users |
| `install_win.bat` | Windows entry point — launches install.py with bundled Python |
| `install_mac.command` | macOS entry point — launches install.py with bundled Python |
| `launcher_win.bat` | Windows direct launcher template |
| `launcher_mac.command` | macOS direct launcher template |
