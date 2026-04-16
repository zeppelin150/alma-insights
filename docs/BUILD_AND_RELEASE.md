# Build & Release

**Builder:** [installer/build_release.py](../installer/build_release.py)
**Install/Uninstall:** [installer/install.py](../installer/install.py), [installer/uninstall.py](../installer/uninstall.py)
**CI:** [.github/workflows/ci.yml](../.github/workflows/ci.yml), [.github/workflows/build.yml](../.github/workflows/build.yml), [.github/workflows/release.yml](../.github/workflows/release.yml)
**Tests:** [tests/test_phase4_build.py](../tests/test_phase4_build.py), [tests/test_phase4_integrity.py](../tests/test_phase4_integrity.py), [tests/test_uninstaller.py](../tests/test_uninstaller.py)

## Bundle layout

A built bundle expands into:

```
<install-dir>/
├── python/                       # embeddable CPython 3.12.8 (Win) / python-build-standalone (macOS)
├── node/                         # Node.js 22.14.0 + @google/gemini-cli@0.36.0
├── app/
│   ├── main.py
│   ├── src/
│   ├── config/
│   ├── assets/
│   ├── migrations/
│   ├── data/
│   │   └── models/Qwen3-Embedding-0.6B/   # pre-bundled, ~1.2 GB
│   ├── sbom.json                # NEW — pip list snapshot
│   └── checksums.json           # NEW — SHA-256 of every app/**/*.py
├── _installer/
│   ├── install.py
│   └── uninstall.py             # NEW — Phase 4
├── Install Alma Insights.bat    (or .command)
├── AlmaInsights.bat             (or .command)
└── Uninstall Alma Insights.bat  (or .command)    # NEW — Phase 4
```

## Version pinning policy

Three lockable surfaces:

| Surface | File | Strategy |
|---------|------|----------|
| Python runtime | [installer/build_release.py](../installer/build_release.py) `PY_VERSION`, `PBS_RELEASE` | Exact pin |
| Node runtime | [installer/build_release.py](../installer/build_release.py) `NODE_VERSION` | Exact pin |
| pip packages | [requirements.txt](../requirements.txt) + `PACKAGES` list in build_release | Exact (`==`) |
| Gemini CLI | [installer/build_release.py](../installer/build_release.py) `GEMINI_CLI_PACKAGE` | Version-suffixed npm spec |
| Qwen3 model | [installer/build_release.py](../installer/build_release.py) `ST_MODEL_NAME` | HF repo ID (snapshot pinned via `huggingface_hub`) |

`tests/test_phase4_build.py::TestExactPinning` enforces that `PACKAGES` matches `requirements.txt` entry-for-entry.

### Current pins

```
Python         3.12.8
Node           22.14.0
Gemini CLI     @google/gemini-cli@0.36.0
Model          Qwen/Qwen3-Embedding-0.6B

PySide6-Essentials==6.8.1.1
pandas==2.2.3
scikit-learn==1.5.2
nltk==3.9.1
pyyaml==6.0.2
numpy==1.26.4        # intentionally held on 1.x for torch compat
scipy==1.14.1
vaderSentiment==3.3.2
sentence-transformers==3.3.1
hdbscan==0.8.40
markdown==3.7
keyring==25.5.0
PyJWT==2.10.1
```

## Supply-chain artefacts shipped in every bundle

### `app/sbom.json`

`pip list --format=json` snapshot, written at build time. Example:

```json
{
  "schema_version": 1,
  "generated_by": "installer/build_release.py",
  "app": "AlmaInsights",
  "python_version": "3.12.8",
  "packages": [
    {"name": "pandas", "version": "2.2.3"},
    ...
  ]
}
```

Useful for: auditing an installed bundle in isolation (`cat app/sbom.json`), reproducing a user's environment when reporting issues, and future CVE scanning.

### `app/checksums.json`

SHA-256 of every `app/**/*.py` file. Example:

```json
{
  "schema_version": 1,
  "algorithm": "sha256",
  "root": "app",
  "files": {
    "main.py": "abc123…",
    "src/startup/splash_window.py": "def456…",
    ...
  }
}
```

The startup splash's Check 1 ([integrity.py](../src/startup/checks/integrity.py)) samples 25 random entries per launch. 1 mismatch → warn; > 5 → fail critical and block the app.

## CI

### `ci.yml` — fast test gate

Runs on every push + PR across Ubuntu, Windows, and macOS-14. Installs from `requirements.lock`, runs Phase 1–4 test groups, then a regression set. Also includes a grep-based secret-leak scan on the git history.

### `build.yml` — PR bundle smoke

Triggered by PRs that touch the installer, requirements, or source. 3-matrix bundle build (Win x64 + macOS arm64 + macOS x64) with heavy-download caching. Artifacts kept 14 days — no release publication.

### `release.yml` — tag-triggered release

On `v*` tag push: 3-matrix build + macOS signing + notarization (if secrets present) + aggregated `release_manifest.json` + GitHub Release with auto-generated notes.

## macOS signing

`installer/ci/sign_macos.sh` wraps `codesign --options runtime --timestamp` + `xcrun notarytool submit --wait` + `xcrun stapler staple`. It exits clean when any of the four Apple secrets is missing, so runs from forks or pre-cert builds don't fail.

Once the Apple Developer ID is activated, add these to the repo's Actions secrets:

| Secret | What |
|--------|------|
| `APPLE_ID` | Developer account email |
| `APPLE_TEAM_ID` | 10-char team identifier |
| `APPLE_APP_PASSWORD` | App-specific password for notarytool |
| `APPLE_SIGNING_IDENTITY` | e.g. `Developer ID Application: Alma Health (TEAMID)` |

Windows code signing is deferred per the locked plan decision (users tolerate SmartScreen today).

## Gemini CLI fail-fast

Pre-Phase 4, a failed `npm install -g @google/gemini-cli` in `build_release.py` emitted a warning and continued — the build zip shipped without the CLI, breaking Gemini OAuth at first launch. Phase 4 changed this to `sys.exit(1)` with the stderr dumped, so CI fails loudly.

## Uninstaller

Run from the install directory:

```bash
# Windows
"Uninstall Alma Insights.bat"

# macOS
./Uninstall\ Alma\ Insights.command
```

Behaviour:
- Prompts before removing the install tree.
- **User data (`app/data/`) is preserved** by default — moved to a sibling backup directory (`.AlmaInsights_data_backup`). Pass `--purge-data` to delete it.
- Prompts before clearing the OS keyring entries (Lightdash PAT, API keys, update token). Pass `--keep-secrets` to skip this.
- Desktop shortcut is removed best-effort.
- `--dry-run` reports every action without touching anything.
- `--yes` accepts all prompts — suitable for automation.

## Regenerating the lockfile

On the macOS M1 production machine:

```bash
python -m venv .lockenv
source .lockenv/bin/activate
pip install --upgrade pip pip-tools
pip-compile --generate-hashes \
            --output-file=requirements.lock \
            requirements.txt
deactivate && rm -rf .lockenv
```

Then commit the updated `requirements.lock`. CI verifies it matches `requirements.txt` and that install succeeds with `--require-hashes`.
