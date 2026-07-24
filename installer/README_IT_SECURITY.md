# Alma Insights Installer — IT / Infosec Review

> This document is written for the IT and information-security team to
> evaluate the standalone installer before it is distributed to end users.

---

## 1. What Is Being Distributed

A self-contained zip archive (~150-250 MB) that contains:

1. **A bundled Python 3.12.8 runtime** (no system-wide install needed)
2. **Six open-source Python packages** (and their transitive dependencies)
3. **The Alma Insights application source code** (pure Python)
4. **Shell scripts** that wire the above together (`.bat` / `.command`)

End users extract the zip, run the installer script, and the application
is copied to a user-space directory. **No admin / elevated privileges are
required at any point.**

---

## 2. Bundled Software Components

### 2.1 Python Runtime

| Detail | Windows | macOS |
|--------|---------|-------|
| **What** | CPython 3.12.8 Embeddable Package | CPython 3.12.8 Standalone Build |
| **Source** | `https://www.python.org/ftp/python/3.12.8/python-3.12.8-embed-amd64.zip` | `https://github.com/astral-sh/python-build-standalone/releases/download/20241219/cpython-3.12.8+20241219-aarch64-apple-darwin-install_only.tar.gz` |
| **Publisher** | Python Software Foundation (PSF) | Astral (Gregory Szorc / `python-build-standalone`) |
| **License** | PSF License 2.0 | PSF License 2.0 (repackaged CPython, same license) |
| **Why bundled** | Target machines may not have Python. Embeddable package is the official PSF distribution for this exact use case. | Target machines may not have Python. `python-build-standalone` provides fully relocatable builds of official CPython; widely used by tools like `rye`, `uv`, and `pdm`. |
| **Verification** | Downloaded over HTTPS directly from `python.org` during the CI build | Downloaded over HTTPS from the GitHub Releases page of `astral-sh/python-build-standalone` |

### 2.2 Python Packages (installed via pip during build)

All packages are downloaded from PyPI (`https://pypi.org`) over HTTPS
during the automated build. No vendored or pre-downloaded wheels are
committed to the repository.

| Package | Version Constraint | Purpose | License |
|---------|--------------------|---------|---------|
| **PySide6-Essentials** | `>=6.6.0` | Qt GUI framework (QtCore, QtGui, QtWidgets only) | LGPL v3 |
| **pandas** | `>=2.0.0` | Data manipulation and analysis | BSD 3-Clause |
| **scikit-learn** | `>=1.3.0` | Machine learning (TF-IDF, clustering) | BSD 3-Clause |
| **pyyaml** | `>=6.0` | YAML config file parsing | MIT |
| **numpy** *(transitive)* | *(pulled by pandas/sklearn)* | Numerical computing | BSD 3-Clause |
| **scipy** *(transitive)* | *(pulled by sklearn)* | Scientific computing | BSD 3-Clause |

> **Note:** `PySide6-Essentials` is used instead of the full `PySide6`
> package. The `-Essentials` variant excludes QtWebEngine, QtQuick,
> Qt3D, and QtMultimedia — reducing attack surface and download size
> by ~200 MB.

### 2.3 Application Source Code

The `app/` directory in the zip contains the Alma Insights application:

| Path | Contents |
|------|----------|
| `app/main.py` | Application entry point |
| `app/src/` | Python source modules (UI, data, analysis) |
| `app/config/` | YAML configuration files |
| `app/assets/` | Icons and static assets |
| `app/data/` | Empty directory; SQLite database created at runtime |

All application code is first-party Python written in-house. No
obfuscation or compilation is applied — the source is readable `.py`
files.

### 2.4 Installer / Launcher Scripts

| File | Platform | What It Does |
|------|----------|-------------|
| `Install Alma Insights.bat` | Windows | Entry point. Calls the bundled `python.exe` to run `_installer/install.py`. 5 lines of batch script. |
| `Install Alma Insights.command` | macOS | Entry point. Strips Gatekeeper quarantine attribute, then calls the bundled `python3` to run `_installer/install.py`. 5 lines of bash. |
| `_installer/install.py` | Both | Console-based installer. Copies files to user-space, creates shortcuts, initializes SQLite DB. Pure Python, ~490 lines, no network access. |
| `AlmaInsights.bat` | Windows | Direct launcher. Runs `pythonw.exe app/main.py`. 2 lines. |
| `AlmaInsights.command` | macOS | Direct launcher. Strips quarantine, runs `python3 app/main.py`. 5 lines. |

---

## 3. How the Distribution Is Built

### 3.1 Build Automation

Builds are performed by **GitHub Actions** on GitHub-hosted runners.
The workflow file is `.github/workflows/build.yml` in the repository.

| Job | Runner | Output |
|-----|--------|--------|
| `build-windows` | `windows-latest` (GitHub-hosted) | `AlmaInsights-win64.zip` |
| `build-macos` (arm64) | `macos-15` (GitHub-hosted, Apple Silicon) | `AlmaInsights-macOS-arm64.zip` |
| `build-macos` (x64) | `macos-15-intel` (GitHub-hosted, Intel) | `AlmaInsights-macOS-x64.zip` |

### 3.2 Build Steps (both platforms)

1. **Check out** the repository source from GitHub (`actions/checkout@v4`)
2. **Set up Python 3.12** on the runner (`actions/setup-python@v5`)
3. **Run `installer/build_release.py`** which:
   - Downloads the platform-appropriate Python runtime over HTTPS from
     its official source (python.org or GitHub Releases)
   - Extracts the runtime into a staging directory
   - (Windows only) Patches `python312._pth` to enable `import site`
   - Bootstraps pip (Windows: `get-pip.py` from `https://bootstrap.pypa.io`)
   - Runs `pip install` to fetch packages from PyPI over HTTPS
   - Verifies that PySide6 loads correctly
   - Copies the application source into the staging directory
   - Removes build artifacts (`__pycache__`, test dirs, pip itself).
     Package `.dist-info` metadata is retained: `importlib.metadata`
     entry points live there, and the `keyring` credential backends are
     discovered through them at runtime.
   - Zips everything into the final distribution archive
4. **Upload** the zip as a GitHub Actions artifact (`actions/upload-artifact@v4`)

### 3.3 Build Environment Integrity

- **Ephemeral runners**: GitHub Actions runners are freshly provisioned
  virtual machines. No persistent state carries between builds.
- **No secrets in the build**: The build script does not use any API
  keys, tokens, or credentials. All downloads are from public HTTPS
  endpoints.
- **Deterministic inputs**: The Python version (`3.12.8`),
  `python-build-standalone` release tag (`20241219`), and package
  version constraints are pinned in `build_release.py`.
- **Artifact retention**: Build artifacts are stored by GitHub for 30
  days with SHA-256 checksums visible on the Actions artifact page.

---

## 4. What the Installer Does on the End User's Machine

When the user runs the installer, the following actions are taken:

1. **Prompts** for an install location (defaults to user-space directory)
2. **Copies** `python/` and `app/` from the extracted zip to the chosen
   install directory — standard file copy, no downloads
3. **Creates launcher scripts** with absolute paths to the installed
   Python and `main.py`
4. **Initializes** an empty SQLite database in `app/data/` by running
   a one-line Python command via the bundled interpreter
5. **Creates shortcuts**:
   - Windows: Desktop `.lnk` and Start Menu `.lnk` via PowerShell
     `WScript.Shell` COM object
   - macOS: `.app` bundle in `~/Applications/` with `Info.plist` +
     symlink on Desktop
6. **Optionally launches** the application

### What the installer does NOT do

- Does NOT require or request admin/root privileges
- Does NOT modify system PATH or environment variables
- Does NOT install system-wide services or daemons
- Does NOT make network connections of any kind
- Does NOT modify the Windows registry (shortcuts use COM, not registry)
- Does NOT write outside the chosen install directory (except the
  shortcut files on Desktop and Start Menu)

---

## 5. Network Behavior

### At Build Time (CI only)

| Connection | Destination | Purpose |
|-----------|-------------|---------|
| HTTPS | `python.org` | Download CPython embeddable (Windows) |
| HTTPS | `github.com` (python-build-standalone releases) | Download CPython standalone (macOS) |
| HTTPS | `bootstrap.pypa.io` | Download `get-pip.py` (Windows) |
| HTTPS | `pypi.org` / `files.pythonhosted.org` | Download Python packages |

### At Install Time (end user)

**None.** The installer is fully offline. All dependencies are
pre-bundled in the zip.

### At Runtime (application)

The Alma Insights application connects to:

- **Lightdash API** (`*.lightdash.cloud` or user-configured endpoint)
  to pull RCM conversation data — only when the user explicitly triggers
  a data pull, authenticated via their personal access token (PAT)

No telemetry, analytics, crash reporting, or auto-update connections are
made.

---

## 6. File System Footprint

### Default Install Locations (user-space, no admin)

| Platform | Path |
|----------|------|
| Windows | `%LOCALAPPDATA%\AlmaInsights\` |
| macOS | `~/Applications/AlmaInsights/` |

### Additional Files Created

| File | Location | Purpose |
|------|----------|---------|
| Desktop shortcut | `~/Desktop/Alma Insights.lnk` (Win) or `~/Desktop/Alma Insights.app` symlink (mac) | Launch shortcut |
| Start Menu entry | `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Alma Insights.lnk` | Windows Start Menu |
| macOS app bundle | `~/Applications/Alma Insights.app/` | macOS Launchpad / Finder |
| SQLite database | `<install_dir>/app/data/*.db` | Local data store (created at runtime) |

### Estimated Disk Usage

| Platform | Installed Size |
|----------|---------------|
| Windows | ~350-500 MB |
| macOS | ~400-550 MB |

The majority of disk usage comes from the Qt framework (PySide6) and
the NumPy/SciPy scientific computing stack.

---

## 7. Security Considerations

### 7.1 Code Signing

The bundled Python binaries and Qt libraries are **not code-signed**
by us. They retain whatever signature their upstream publishers applied:

- **Windows**: The Python embeddable package from python.org is signed
  by the Python Software Foundation. PySide6 wheels are signed by The
  Qt Company. These signatures are preserved in the distribution.
- **macOS**: The `python-build-standalone` binaries are not
  Apple-notarized. macOS Gatekeeper will flag them on first run. The
  installer scripts include `xattr -rd com.apple.quarantine` to clear
  the quarantine flag after user-initiated extraction. Users may also
  need to right-click > Open on first launch.

### 7.2 Supply Chain

| Risk | Mitigation |
|------|-----------|
| Compromised Python download | Downloaded over HTTPS from `python.org` (PSF) during CI on ephemeral GitHub runners. Version is pinned. |
| Compromised PyPI packages | Downloaded over HTTPS from `pypi.org` during CI. Version lower-bounds are pinned. All packages are well-known, high-profile open-source projects (>10M monthly downloads each). |
| Compromised build runner | GitHub-hosted runners are ephemeral VMs provisioned fresh for each build. No secrets or credentials are used. |
| Tampering during distribution | Distribution zips are currently transferred directly from the developer to recipients. For additional assurance, SHA-256 checksums from the GitHub Actions artifact page can be verified. |

### 7.3 What We Recommend for Additional Hardening

If the security team requires stronger guarantees, the following steps
can be added:

1. **Pin exact package versions** (e.g., `PySide6-Essentials==6.8.1`)
   instead of minimum versions, and use `pip install --require-hashes`
   with pre-computed SHA-256 hashes for every wheel.
2. **Publish SHA-256 checksums** of the final zip alongside the
   distribution so recipients can verify integrity. (wip)
3. **Code-sign the distribution** with an organization certificate
   (Windows Authenticode / Apple Developer ID) to eliminate
   SmartScreen and Gatekeeper warnings.
4. **Host on a controlled internal server** (SharePoint, internal
   artifact repository) rather than transferring via email/chat.

### 7.4 Gatekeeper / SmartScreen Behavior

| Platform | Behavior | User Action Required |
|----------|----------|---------------------|
| Windows | SmartScreen may show "Windows protected your PC" on first run of the `.bat` installer | Click "More info" then "Run anyway" |
| macOS | Gatekeeper may show "cannot be opened because the developer cannot be verified" | Right-click the `.command` file > Open, or System Settings > Privacy & Security > Allow |

These warnings are standard for unsigned/non-notarized software and
do not indicate malicious content.

---

## 8. Uninstallation

To fully remove Alma Insights:

1. Delete the install directory:
   - Windows: `%LOCALAPPDATA%\AlmaInsights\`
   - macOS: `~/Applications/AlmaInsights/`
2. Delete shortcuts:
   - Windows: `Desktop\Alma Insights.lnk` and
     `Start Menu\Programs\Alma Insights.lnk`
   - macOS: `~/Desktop/Alma Insights.app` and
     `~/Applications/Alma Insights.app`

No registry entries, system services, LaunchAgents, or hidden files
are left behind.

---

## 9. Summary for Approval

| Question | Answer |
|----------|--------|
| Does it require admin rights? | No |
| Does it modify system files? | No |
| Does it install system services? | No |
| Does it modify the registry? | No |
| Does it phone home or auto-update? | No |
| What network access does it need? | Lightdash API only, user-initiated, PAT-authenticated |
| Is the source readable? | Yes, all `.py` files are unobfuscated |
| Can it be fully removed by deleting folders? | Yes |
| Are all dependencies open-source? | Yes |
| Are builds reproducible? | Yes, via GitHub Actions on ephemeral runners |

---

*Document prepared: February 2025*
*Application version: 1.0.0*
*Contact: [chris guffey/chris.guffey@helloalma.com]*
